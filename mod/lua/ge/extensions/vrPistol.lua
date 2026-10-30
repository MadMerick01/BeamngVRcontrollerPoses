-- vrPistol: a handgun on the right VR controller that shoots traffic.
--
--   extensions.load('vrPistol')       -- also loads vrHands
--   vrPistol.setLaser(true|false)     -- aiming laser (on by default)
--   vrPistol.setModelOffset(x, y, z, pitchDeg)  -- fit the model to your hand
--   vrPistol.fireFromCamera()         -- desktop test: shoot along the camera view
--
-- The shot always travels along the controller's aim ray (OpenXR aim pose), so
-- the model offset only changes how the gun looks, never where it hits.

local M = {}
M.dependencies = {'vrHands'}

local cfg = {
  hand = 'right',
  modelPath = '/art/shapes/vrpistol/vr_pistol.dae',  -- from the VRPistol_Visual mod; optional
  -- Model placement in aim-pose space, metres: x right, y along the aim ray, z up.
  modelOffset = {x = 0, y = -0.04, z = -0.03},
  modelPitchDeg = 0,
  laser = true,
  range = 300,
  triggerPress = 0.7,
  triggerRelease = 0.3,
  cooldown = 0.12,
  hitRadius = 0.3,       -- how far from the ray a vehicle node still counts as hit
  damageRadius = 0.25,   -- beams with a node this close to the hit node are broken
  maxBeamsPerShot = 4,
  allowSelfHit = false,
}

local model, modelMissingLogged
local triggerHeld, lastShot = false, -math.huge
local markers = {}  -- {pos, untilTime, colour}

local laserColour = ColorF(1, 0.1, 0.1, 0.8)
local hitColour = ColorF(1, 0.6, 0, 1)
local missColour = ColorF(0.6, 0.6, 0.6, 1)

local function now() return os.clock() end
pcall(function()
  local socket = require('socket')
  now = socket.gettime
end)

-- --------------------------------------------------------------- model ---

local function ensureModel()
  if model then return true end
  if not FS:fileExists(cfg.modelPath) then
    if not modelMissingLogged then
      modelMissingLogged = true
      log('W', 'vrPistol', 'pistol model not found (' .. cfg.modelPath .. '), shooting works without it')
    end
    return false
  end
  local ok, obj = pcall(function()
    local old = scenetree.findObject('vrPistolModel')
    if old then old:delete() end
    local o = createObject('TSStatic')
    o.shapeName = cfg.modelPath
    o:setField('collisionType', 0, 'None')
    o:setField('decalType', 0, 'None')
    o:setField('castShadows', 0, '0')
    o:setField('canSave', 0, '0')
    o:registerObject('vrPistolModel')
    return o
  end)
  if ok and obj then model = obj else
    log('E', 'vrPistol', 'could not create pistol model: ' .. tostring(obj))
    modelMissingLogged = true
  end
  return model ~= nil
end

local function placeModel(aim)
  if not ensureModel() then return end
  if not aim.valid then
    model:setHidden(true)
    return
  end
  local o = cfg.modelOffset
  local pos = aim.pos + aim.right * o.x + aim.dir * o.y + aim.up * o.z
  local a = math.rad(cfg.modelPitchDeg)
  local dir = aim.dir * math.cos(a) + aim.up * math.sin(a)
  local up = aim.up * math.cos(a) - aim.dir * math.sin(a)
  local rot = quatFromDir(dir, up)
  model:setHidden(false)
  model:setPosRot(pos.x, pos.y, pos.z, rot.x, rot.y, rot.z, rot.w)
end

-- -------------------------------------------------------------- hits ---

-- Ray against an oriented box; returns entry distance or nil.
local function rayBox(origin, dir, center, axes, half, maxDist)
  local tMin, tMax = 0, maxDist
  local d = center - origin
  for i = 1, 3 do
    local e, f = axes[i]:dot(d), axes[i]:dot(dir)
    if math.abs(f) > 1e-9 then
      local t1, t2 = (e + half[i]) / f, (e - half[i]) / f
      if t1 > t2 then t1, t2 = t2, t1 end
      tMin, tMax = math.max(tMin, t1), math.min(tMax, t2)
      if tMin > tMax then return nil end
    elseif -e - half[i] > 0 or -e + half[i] < 0 then
      return nil
    end
  end
  return tMin
end

local function vehicleBox(veh)
  local ok, box = pcall(function() return veh:getSpawnWorldOOBB() end)
  if ok and box then
    local h = box:getHalfExtents()
    return vec3(box:getCenter()), {vec3(box:getAxis(0)), vec3(box:getAxis(1)), vec3(box:getAxis(2))}, {h.x, h.y, h.z}
  end
  local okW, wb = pcall(function() return veh:getWorldBox() end)
  if okW and wb then
    local mn, mx = vec3(wb.minExtents), vec3(wb.maxExtents)
    return (mn + mx) * 0.5, {vec3(1, 0, 0), vec3(0, 1, 0), vec3(0, 0, 1)},
      {(mx.x - mn.x) / 2, (mx.y - mn.y) / 2, (mx.z - mn.z) / 2}
  end
end

local function staticHitDistance(origin, dir)
  local ok, d = pcall(castRayStatic, origin, dir, cfg.range)
  if ok and type(d) == 'number' and d < cfg.range then return d end
  return cfg.range
end

local function addMarker(pos, colour)
  markers[#markers + 1] = {pos = pos, untilTime = now() + 1.5, colour = colour}
end

function M.shoot(origin, dir)
  dir = dir:normalized()
  local wallDist = staticHitDistance(origin, dir)
  local player = be:getPlayerVehicle(0)

  -- Every vehicle whose box the ray enters before the nearest wall gets the
  -- precise node test; the vehicle side reports back if something was hit.
  local candidates = {}
  for i = 0, be:getObjectCount() - 1 do
    local veh = be:getObject(i)
    if veh and (cfg.allowSelfHit or veh ~= player) then
      local center, axes, half = vehicleBox(veh)
      if center then
        local t = rayBox(origin, dir, center, axes, half, wallDist)
        if t then candidates[#candidates + 1] = {veh = veh, t = t} end
      end
    end
  end
  table.sort(candidates, function(a, b) return a.t < b.t end)

  -- Nearest box first. Vehicle Lua runs asynchronously, so the nearest box gets
  -- the shot; a miss inside it (gaps, windows) is accepted for now.
  local target = candidates[1]
  if target then
    target.veh:queueLuaCommand(string.format(
      "extensions.load('vrPistolHit'); extensions.vrPistolHit.hit(%.4f,%.4f,%.4f,%.6f,%.6f,%.6f,%.2f,%.3f,%.3f,%d)",
      origin.x, origin.y, origin.z, dir.x, dir.y, dir.z, wallDist, cfg.hitRadius, cfg.damageRadius, cfg.maxBeamsPerShot))
  elseif wallDist < cfg.range then
    addMarker(origin + dir * wallDist, missColour)
  end
end

-- Called from vehicle Lua when a shot connects.
function M.onVehicleHit(vehId, x, y, z, beamsBroken)
  addMarker(vec3(x, y, z), hitColour)
  log('D', 'vrPistol', string.format('hit vehicle %d, broke %d beams', vehId, beamsBroken))
end

function M.fireFromCamera()
  M.shoot(getCameraPosition(), core_camera.getForward())
end

function M.setLaser(enabled) cfg.laser = enabled and true or false end

function M.setModelOffset(x, y, z, pitchDeg)
  cfg.modelOffset = {x = x or 0, y = y or 0, z = z or 0}
  cfg.modelPitchDeg = pitchDeg or 0
end

function M.getConfig() return cfg end

-- ------------------------------------------------------------- hooks ---

function M.onPreRender()
  local aim = vrHands.getPose(cfg.hand, 'aim')
  placeModel(aim)

  if aim.valid then
    if cfg.laser then
      local dist = staticHitDistance(aim.pos, aim.dir)
      debugDrawer:drawLine(aim.pos, aim.pos + aim.dir * math.min(dist, 60), laserColour)
    end
    local trigger = vrHands.getTrigger(cfg.hand)
    if not triggerHeld and trigger >= cfg.triggerPress then
      triggerHeld = true
      local t = now()
      if t - lastShot >= cfg.cooldown then
        lastShot = t
        M.shoot(aim.pos, aim.dir)
      end
    elseif triggerHeld and trigger <= cfg.triggerRelease then
      triggerHeld = false
    end
  else
    triggerHeld = false
  end

  local t = now()
  for i = #markers, 1, -1 do
    local m = markers[i]
    if t > m.untilTime then table.remove(markers, i)
    else debugDrawer:drawSphere(m.pos, 0.06, m.colour) end
  end
end

function M.onClientEndMission()
  if model then pcall(function() model:delete() end) end
  model = nil
end

function M.onExtensionUnloaded()
  M.onClientEndMission()
end

return M
