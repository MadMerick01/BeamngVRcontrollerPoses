-- vrHands: world-space VR controller poses for BeamNG.drive 0.40+.
--
-- The OpenXR layer (BeamNGVRPosesLayer.dll) sends each controller's grip and aim
-- pose relative to the headset, sampled at the same XrTime BeamNG uses for that
-- frame. BeamNG 0.40 sets the final camera to the exact rendered head pose
-- (gameengine.lua: anchor + OpenXR predicted pose), so
--   controllerWorld = headWorld * controllerRelativeToHead
-- with no tracking-space, recenter or locomotion bookkeeping.
--
-- Public API (all positions/directions are vec3 in world space):
--   vrHands.getPose('right', 'aim')  -> {valid, pos, dir, up, right, rot, ageMs}
--     dir is the pose's pointing direction (OpenXR -Z), rot is a BeamNG quat
--     from quatFromDir(dir, up), ready for TSStatic:setPosRot.
--   vrHands.getTrigger('right')       -> 0..1
--   vrHands.getSqueeze('right')       -> 0..1
--   vrHands.pulse('right', 0.8, 0.05) -> controller vibration (amplitude, seconds)
--   vrHands.setDebug(true|false)      -> draw controller axes and aim rays
--   vrHands.getStatus()               -> diagnostics table

local M = {}
-- Run onPreRender after core_camera's, so the camera (and the anchor recorded
-- from it) is this frame's and not last frame's. A frame late, the gun trails
-- the car by one frame of travel, which shows as jitter at speed.
M.dependencies = {'core_camera'}

local cfg = {
  port = 44441,
  staleMs = 150,
  debug = false,
  fixHeadOffset = true,
  cameraQuat = 'conjugate',  -- or 'hamilton'
  useTrackingSpace = true,
}

local socketlib, udp
local packet, packetTime, packetCount, lastError = nil, nil, 0, nil
local headConvention = cfg.cameraQuat

-- What gameengine.lua passed to and got from OpenXR this frame (see wrapOpenXR).
local xrFrame = {anchorPos = nil, anchorRot = nil, headOffset = nil, headRot = nil, time = nil}
local lastRaw  -- last packet text, for captures
local capture  -- {untilTime, path, lines}

local emptyPose = {valid = false}

-- -------------------------------------------------------------- math ---

local function qmul(a, b)
  return {
    a[4]*b[1] + a[1]*b[4] + a[2]*b[3] - a[3]*b[2],
    a[4]*b[2] - a[1]*b[3] + a[2]*b[4] + a[3]*b[1],
    a[4]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[4],
    a[4]*b[4] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
  }
end

-- Rotates vector v (3 numbers) by unit quaternion q (x, y, z, w), Hamilton convention.
local function qrot(q, v)
  local r = qmul(qmul(q, {v[1], v[2], v[3], 0}), {-q[1], -q[2], -q[3], q[4]})
  return {r[1], r[2], r[3]}
end

-- OpenXR view space (+X right, +Y up, -Z forward) to BeamNG camera space
-- (+X right, +Y forward, +Z up).
local function xrToCamera(v) return {v[1], -v[3], v[2]} end

local function dot(a, b) return a[1]*b[1] + a[2]*b[2] + a[3]*b[3] end

-- -------------------------------------------------------------- head ---

-- Returns head position {x,y,z} and a function mapping camera-space vectors to world.
local function headTransform()
  local okP, p = pcall(getCameraPosition)
  local okQ, q = pcall(getCameraQuat)
  if not okP or not okQ or not p or not q then return nil end
  local hq = {q.x, q.y, q.z, q.w}

  -- getCameraQuat is camera-to-world but, with this file's Hamilton qrot, needs
  -- its conjugate: captures from BeamNG 0.40.4 hold a resting controller within
  -- 1 cm that way and drift by tens of cm the other. (Checking against
  -- core_camera.getForward picked the wrong one in game.)
  headConvention = cfg.cameraQuat
  if headConvention ~= 'hamilton' then hq = {-hq[1], -hq[2], -hq[3], hq[4]} end

  local head = {p.x, p.y, p.z}

  -- gameengine.lua adds the headset's tracked offset to the camera anchor without
  -- turning it by the anchor's yaw, so getCameraPosition() only follows the real
  -- head while the anchor faces north. The headset view is rendered correctly, so
  -- rebuild the true head position: anchor + anchorRot * offset.
  local o, a = xrFrame.headOffset, xrFrame.anchorRot
  if cfg.fixHeadOffset and o and a and socketlib and socketlib.gettime() - xrFrame.time < 0.25 then
    if headConvention ~= 'hamilton' then a = {-a[1], -a[2], -a[3], a[4]} end
    local r = qrot(a, o)
    head = {head[1] + r[1] - o[1], head[2] + r[2] - o[2], head[3] + r[3] - o[3]}
  end

  return head, function(v) return qrot(hq, v) end
end

-- This frame's camera anchor: origin of BeamNG's tracking space in the world and
-- a function turning tracking-space vectors (BeamNG axes) into world ones.
local function baseTransform()
  local a, A = xrFrame.anchorRot, xrFrame.anchorPos
  if not (cfg.useTrackingSpace and a and A and xrFrame.anchorTime and socketlib) then return nil end
  if socketlib.gettime() - xrFrame.anchorTime > 0.25 then return nil end
  if cfg.cameraQuat ~= 'hamilton' then a = {-a[1], -a[2], -a[3], a[4]} end
  return {A[1], A[2], A[3]}, function(v) return qrot(a, v) end
end

-- Records the camera anchor rotation and the headset offset that gameengine.lua
-- exchanges with OpenXR each frame. The OpenXR table may not exist until VR
-- starts, so onPreRender keeps retrying. The originals live in a Lua global
-- (the OpenXR binding may refuse new keys) so a reload never wraps twice.
local hooks = {set = nil, get = nil}

local function wrapOpenXR()
  if type(OpenXR) ~= 'table' then return end
  local okS, curSet = pcall(function() return OpenXR.setGeluaCameraPosRot end)
  local okG, curGet = pcall(function() return OpenXR.getCameraPosRotPredictedXYZXYZW end)
  if not (okS and okG) or (curSet == hooks.set and curGet == hooks.get) then return end
  if type(curSet) ~= 'function' or type(curGet) ~= 'function' then return end

  local saved = rawget(_G, 'vrHandsOpenXROriginals')
  if not saved or saved.xr ~= OpenXR then
    saved = {xr = OpenXR, set = curSet, get = curGet}
    rawset(_G, 'vrHandsOpenXROriginals', saved)
  end
  local origSet, origGet = saved.set, saved.get

  hooks.set = function(px, py, pz, rx, ry, rz, rw, ...)
    local t = socketlib and socketlib.gettime()
    local prev, prevT = xrFrame.anchorPos, xrFrame.anchorTime
    if t and prev and prevT and t - prevT > 1e-4 and t - prevT < 0.1 then
      local dt = t - prevT
      xrFrame.anchorVel = {(px - prev[1]) / dt, (py - prev[2]) / dt, (pz - prev[3]) / dt}
      xrFrame.frameDt = dt
    end
    xrFrame.anchorPos, xrFrame.anchorRot, xrFrame.anchorTime = {px, py, pz}, {rx, ry, rz, rw}, t
    return origSet(px, py, pz, rx, ry, rz, rw, ...)
  end
  hooks.get = function(...)
    local px, py, pz, rx, ry, rz, rw = origGet(...)
    if px and socketlib then
      xrFrame.headOffset, xrFrame.headRot, xrFrame.time = {px, py, pz}, {rx, ry, rz, rw}, socketlib.gettime()
    end
    return px, py, pz, rx, ry, rz, rw
  end
  local ok, err = pcall(function()
    OpenXR.setGeluaCameraPosRot = hooks.set
    OpenXR.getCameraPosRotPredictedXYZXYZW = hooks.get
  end)
  if ok and OpenXR.setGeluaCameraPosRot == hooks.set and OpenXR.getCameraPosRotPredictedXYZXYZW == hooks.get then
    log('I', 'vrHands', 'hooked OpenXR camera calls')
  else
    pcall(function()
      OpenXR.setGeluaCameraPosRot, OpenXR.getCameraPosRotPredictedXYZXYZW = origSet, origGet
    end)
    log('W', 'vrHands', 'cannot hook OpenXR camera calls: ' .. tostring(err))
    hooks.set, hooks.get = curSet, curGet  -- don't retry every frame
  end
end

-- ------------------------------------------------------------ network ---

local function receive()
  if not udp then return end
  while true do
    local data, err = udp:receive()
    if not data then
      if err ~= 'timeout' then lastError = err end
      return
    end
    local ok, p = pcall(jsonDecode, data)
    if ok and type(p) == 'table' and p.v == 3 and type(p.hands) == 'table' then
      packet, packetTime, packetCount, lastRaw = p, socketlib.gettime(), packetCount + 1, data
    end
  end
end

local function packetAgeMs()
  if not packetTime then return nil end
  return (socketlib.gettime() - packetTime) * 1000
end

-- ------------------------------------------------------------ poses ---

local function computePose(hand, kind)
  local age = packetAgeMs()
  if not packet or not age or age > cfg.staleMs then return emptyPose end
  local h = packet.hands[hand]
  local s = h and h[kind]
  if not s or not s.ok or type(s.p) ~= 'table' or type(s.q) ~= 'table' then return emptyPose end

  -- Preferred: the controller's pose in BeamNG's tracking space, placed with this
  -- frame's camera anchor, the same way BeamNG places its own controller models.
  -- Fallback: head-relative pose on top of the final camera.
  local origin, toWorld, p, q = baseTransform()
  if origin and type(s.rp) == 'table' and type(s.rq) == 'table' then
    p, q = s.rp, s.rq
  else
    origin, toWorld = headTransform()
    if not origin then return emptyPose end
    p, q = s.p, s.q
  end
  local headPos = origin
  local offset = toWorld(xrToCamera(p))
  local dir = toWorld(xrToCamera(qrot(q, {0, 0, -1})))
  local up = toWorld(xrToCamera(qrot(q, {0, 1, 0})))
  local right = toWorld(xrToCamera(qrot(q, {1, 0, 0})))

  local pose = {
    valid = true,
    pos = vec3(headPos[1] + offset[1], headPos[2] + offset[2], headPos[3] + offset[3]),
    dir = vec3(dir[1], dir[2], dir[3]),
    up = vec3(up[1], up[2], up[3]),
    right = vec3(right[1], right[2], right[3]),
    ageMs = age,
  }
  pose.rot = quatFromDir(pose.dir, pose.up)
  return pose
end

function M.getPose(hand, kind)
  return computePose(hand, kind or 'aim')
end

local function value(hand, name)
  local age = packetAgeMs()
  if not packet or not age or age > cfg.staleMs then return 0 end
  local h = packet.hands[hand]
  return h and tonumber(h[name]) or 0
end

function M.getTrigger(hand) return value(hand, 'trigger') end
function M.getSqueeze(hand) return value(hand, 'squeeze') end

-- Vibrates a controller through the OpenXR layer (amplitude 0..1, seconds).
function M.pulse(hand, amplitude, seconds)
  if not udp then return end
  pcall(udp.sendto, udp, string.format('pulse %s %.3f %.3f', hand, amplitude or 1, seconds or 0.05),
    '127.0.0.1', cfg.port + 1)
end

function M.setDebug(enabled) cfg.debug = enabled and true or false end

local function say(msg)
  log('I', 'vrHands', msg)
  if ui_message then pcall(ui_message, msg, 10, 'vrHands') end
end

-- Records what vrHands sees each frame for a few seconds, for diagnosing
-- alignment: anchor and head offset from OpenXR, the final camera, the layer
-- packet and the computed gun pose. Writes <user folder>/vrHandsCapture/*.jsonl.
function M.startCapture(seconds)
  if not socketlib then return end
  local dir = '/vrHandsCapture/'
  pcall(function() FS:directoryCreate(dir, true) end)
  capture = {untilTime = socketlib.gettime() + (seconds or 20), lines = {},
    path = dir .. os.date('%Y%m%d_%H%M%S') .. '.jsonl'}
  say('vrHands capture started: hold the right controller still, turn and tilt your head')
end

local function vec3List(v) return v and {v.x, v.y, v.z} or nil end

local function recordCapture()
  if not capture then return end
  local now = socketlib.gettime()
  if now > capture.untilTime then
    local ok = pcall(writeFile, capture.path, table.concat(capture.lines, '\n') .. '\n')
    local where = capture.path
    pcall(function() where = FS:getUserPath() .. capture.path:sub(2) end)
    say(ok and ('vrHands capture saved: ' .. where) or 'vrHands capture could not be written')
    capture = nil
    return
  end
  local okP, p = pcall(getCameraPosition)
  local okQ, q = pcall(getCameraQuat)
  local okN, name = pcall(function() return core_camera.getActiveCamName() end)
  local gun = M.getPose('right', 'aim')
  local frame = {
    t = now, cam = okN and name or nil, conv = headConvention, fix = cfg.fixHeadOffset,
    anchorPos = xrFrame.anchorPos, anchorRot = xrFrame.anchorRot,
    headOffset = xrFrame.headOffset, headRot = xrFrame.headRot, xrAge = xrFrame.time and now - xrFrame.time,
    camPos = okP and vec3List(p) or nil, camQuat = okQ and q and {q.x, q.y, q.z, q.w} or nil,
    gunPos = gun.valid and vec3List(gun.pos) or nil, gunDir = gun.valid and vec3List(gun.dir) or nil,
  }
  local ok, text = pcall(jsonEncode, frame)
  if ok then capture.lines[#capture.lines + 1] = text:sub(1, -2) .. ',"packet":' .. (lastRaw or 'null') .. '}' end
end
function M.getConfig() return cfg end

-- Camera anchor this frame: world position, velocity (m/s) and the last frame time (s).
function M.getAnchorMotion()
  local p, v = xrFrame.anchorPos, xrFrame.anchorVel
  return {pos = p and vec3(p[1], p[2], p[3]), vel = v and vec3(v[1], v[2], v[3]) or vec3(0, 0, 0),
    frameDt = xrFrame.frameDt}
end

function M.getStatus()
  return {
    listening = udp ~= nil,
    port = cfg.port,
    packets = packetCount,
    packetAgeMs = packetAgeMs(),
    lastError = lastError,
    headConvention = headConvention,
    cameraLagMs = xrFrame.anchorTime and socketlib and (socketlib.gettime() - xrFrame.anchorTime) * 1000 or nil,
    trackingSpace = baseTransform() ~= nil and packet ~= nil and packet.head ~= nil,
    openxrHooked = hooks.set ~= nil and type(OpenXR) == 'table' and OpenXR.setGeluaCameraPosRot == hooks.set,
    headOffsetFix = cfg.fixHeadOffset and xrFrame.time ~= nil and socketlib ~= nil and socketlib.gettime() - xrFrame.time < 0.25,
    left = {grip = M.getPose('left', 'grip').valid, aim = M.getPose('left', 'aim').valid, trigger = M.getTrigger('left')},
    right = {grip = M.getPose('right', 'grip').valid, aim = M.getPose('right', 'aim').valid, trigger = M.getTrigger('right')},
  }
end

-- ------------------------------------------------------------- debug ---

local red, green, blue, yellow = ColorF(1, 0, 0, 1), ColorF(0, 1, 0, 1), ColorF(0, 0.4, 1, 1), ColorF(1, 1, 0, 1)

local function drawAxes(pose, length)
  local p = pose.pos
  debugDrawer:drawLine(p, p + pose.right * length, red)
  debugDrawer:drawLine(p, p + pose.dir * length, green)
  debugDrawer:drawLine(p, p + pose.up * length, blue)
end

local function drawDebug()
  for _, hand in ipairs({'left', 'right'}) do
    local grip = M.getPose(hand, 'grip')
    if grip.valid then
      debugDrawer:drawSphere(grip.pos, 0.02, yellow)
      drawAxes(grip, 0.08)
    end
    local aim = M.getPose(hand, 'aim')
    if aim.valid then debugDrawer:drawLine(aim.pos, aim.pos + aim.dir * 3, yellow) end
  end
end

-- ------------------------------------------------------------- hooks ---

-- Stay loaded across level changes (BeamNG unloads "auto" extensions).
function M.onInit()
  if setExtensionUnloadMode then setExtensionUnloadMode(M, 'manual') end
end

function M.onExtensionLoaded()
  local ok, lib = pcall(require, 'socket')
  if not ok then
    log('E', 'vrHands', 'LuaSocket unavailable: ' .. tostring(lib))
    return
  end
  socketlib = lib
  wrapOpenXR()
  udp = socketlib.udp()
  udp:settimeout(0)
  local bound, err = udp:setsockname('127.0.0.1', cfg.port)
  if not bound then
    log('E', 'vrHands', 'cannot listen on UDP ' .. cfg.port .. ': ' .. tostring(err) ..
      ' (is the old beamngVRControllerPoses extension still loaded?)')
    udp:close()
    udp = nil
    return
  end
  log('I', 'vrHands', 'listening for controller poses on 127.0.0.1:' .. cfg.port)
end

function M.onExtensionUnloaded()
  if udp then udp:close() end
  udp = nil
end

-- Runs after the camera update in onPreRender, so getCameraPosition() is this
-- frame's final head pose. Consumers should read poses from their own
-- onPreRender (or later), not onUpdate.
function M.onPreRender()
  wrapOpenXR()
  receive()
  recordCapture()
  if cfg.debug then drawDebug() end
end

return M
