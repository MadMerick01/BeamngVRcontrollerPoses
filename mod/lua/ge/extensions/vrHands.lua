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

local cfg = {
  port = 44441,
  staleMs = 150,
  debug = false,
  fixHeadOffset = true,
}

local socketlib, udp
local packet, packetTime, packetCount, lastError = nil, nil, 0, nil
local headConvention = 'unknown'

-- What gameengine.lua passed to and got from OpenXR this frame (see wrapOpenXR).
local xrFrame = {anchorRot = nil, headOffset = nil, time = nil}

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

  -- getCameraQuat is camera-to-world. Confirm its handedness once against the
  -- engine's own forward vector instead of assuming a quaternion convention.
  if headConvention == 'unknown' and core_camera and core_camera.getForward then
    local okF, f = pcall(core_camera.getForward)
    if okF and f then
      local fwd = {f.x, f.y, f.z}
      if dot(qrot(hq, {0, 1, 0}), fwd) > 0.98 then headConvention = 'hamilton'
      elseif dot(qrot({-hq[1], -hq[2], -hq[3], hq[4]}, {0, 1, 0}), fwd) > 0.98 then headConvention = 'conjugate' end
      if headConvention ~= 'unknown' then log('I', 'vrHands', 'camera quaternion convention: ' .. headConvention) end
    end
  end
  if headConvention == 'conjugate' then hq = {-hq[1], -hq[2], -hq[3], hq[4]} end

  local head = {p.x, p.y, p.z}

  -- gameengine.lua adds the headset's tracked offset to the camera anchor without
  -- turning it by the anchor's yaw, so getCameraPosition() only follows the real
  -- head while the anchor faces north. The headset view is rendered correctly, so
  -- rebuild the true head position: anchor + anchorRot * offset.
  local o, a = xrFrame.headOffset, xrFrame.anchorRot
  if cfg.fixHeadOffset and o and a and socketlib and socketlib.gettime() - xrFrame.time < 0.25 then
    if headConvention == 'conjugate' then a = {-a[1], -a[2], -a[3], a[4]} end
    local r = qrot(a, o)
    head = {head[1] + r[1] - o[1], head[2] + r[2] - o[2], head[3] + r[3] - o[3]}
  end

  return head, function(v) return qrot(hq, v) end
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
    xrFrame.anchorRot = {rx, ry, rz, rw}
    return origSet(px, py, pz, rx, ry, rz, rw, ...)
  end
  hooks.get = function(...)
    local px, py, pz, rx, ry, rz, rw = origGet(...)
    if px and socketlib then
      xrFrame.headOffset, xrFrame.time = {px, py, pz}, socketlib.gettime()
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
      packet, packetTime, packetCount = p, socketlib.gettime(), packetCount + 1
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

  local headPos, toWorld = headTransform()
  if not headPos then return emptyPose end

  local q = s.q
  local offset = toWorld(xrToCamera(s.p))
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
function M.getConfig() return cfg end

function M.getStatus()
  return {
    listening = udp ~= nil,
    port = cfg.port,
    packets = packetCount,
    packetAgeMs = packetAgeMs(),
    lastError = lastError,
    headConvention = headConvention,
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
  if cfg.debug then drawDebug() end
end

return M
