-- vrPistol: a revolver on the right VR controller that shoots traffic.
--
--   extensions.load('vrPistol')       -- also loads vrHands
--   vrPistol.setLaser(true|false)     -- aiming laser (on by default)
--   vrPistol.setModelOffset(x, y, z, pitchDeg)  -- fit the model to your hand
--   vrPistol.fireFromCamera()         -- desktop test: shoot along the camera view
--
-- The shot always travels along the controller's aim ray (OpenXR aim pose), so
-- the model offset only changes how the gun looks, never where it hits.
--
-- The model is a double-action revolver (mod/art/shapes/vrrevolver, built by
-- tools/revolverModel/build_revolver.py). Squeezing the trigger cocks the
-- hammer and turns the cylinder; at the break point the hammer drops, the gun
-- kicks and the muzzle flashes.

local M = {}
M.dependencies = {'vrHands'}

local cfg = {
  hand = 'right',
  modelDir = '/art/shapes/vrrevolver/',
  -- Where the gun's origin (bore axis, rear face of the cylinder) sits in
  -- aim-pose space, metres: x right, y along the aim ray, z up.
  modelOffset = {x = 0, y = -0.02, z = 0.05},
  modelPitchDeg = 0,
  -- Action and recoil.
  hammerCockDeg = 48,
  triggerTravelDeg = 20,
  recoilBack = 0.035,    -- metres the gun kicks back
  recoilPitchDeg = 22,   -- muzzle flip
  flashTime = 0.045,
  laser = true,
  range = 300,
  triggerPress = 0.7,
  triggerRelease = 0.3,
  cooldown = 0.12,
  hitRadius = 0.3,       -- how far from the ray a vehicle node still counts as hit
  -- Impact physics, same model and defaults as Bolides' BulletDamage.lua:
  impactForce = 6000,    -- push on the hit node along the bullet, scaled by node mass
  blastRadius = 1.3,     -- nodes within this radius get a falloff push (dents)
  blastForce = 70,
  blastInversion = 0.3,  -- chance a node is pulled instead of pushed
  deflateTyres = true,   -- a hit near a wheel hub also flattens that tyre
  allowSelfHit = false,
}

local parts, modelMissingLogged
local triggerHeld, lastShot = false, -math.huge
local shotsFired, flashRoll = 0, 0
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

-- Each part's shape has its origin on its pivot; PARTS gives that pivot in gun
-- space (x right, y along the barrel, z up), matching build_revolver.py.
local PARTS = {
  {name = 'frame', pivot = vec3(0, 0, 0)},
  {name = 'cylinder', pivot = vec3(0, 0, -0.0155)},
  {name = 'hammer', pivot = vec3(0, -0.0175, -0.006)},
  {name = 'trigger', pivot = vec3(0, 0.012, -0.040)},
  {name = 'flash', pivot = vec3(0, 0.218, 0)},
}
local MUZZLE = vec3(0, 0.218, 0)
local RECOIL_PIVOT = vec3(0, -0.045, -0.08)  -- in the hand: the gun flips about the grip

local function deleteParts()
  for _, p in pairs(parts or {}) do pcall(function() p:delete() end) end
  parts = nil
end

local function ensureModel()
  if parts then return true end
  if modelMissingLogged then return false end
  for _, p in ipairs(PARTS) do
    if not FS:fileExists(cfg.modelDir .. p.name .. '.dae') then
      modelMissingLogged = true
      log('W', 'vrPistol', 'revolver model not found (' .. cfg.modelDir .. p.name .. '.dae), shooting works without it')
      return false
    end
  end
  local ok, err = pcall(function()
    parts = {}
    for _, p in ipairs(PARTS) do
      local name = 'vrPistolModel_' .. p.name
      local old = scenetree.findObject(name)
      if old then old:delete() end
      local o = createObject('TSStatic')
      o.shapeName = cfg.modelDir .. p.name .. '.dae'
      o:setField('collisionType', 0, 'None')
      o:setField('decalType', 0, 'None')
      o:setField('castShadows', 0, p.name == 'flash' and '0' or '1')
      o:setField('canSave', 0, '0')
      o:registerObject(name)
      parts[p.name] = o
    end
  end)
  if not ok then
    log('E', 'vrPistol', 'could not create revolver model: ' .. tostring(err))
    deleteParts()
    modelMissingLogged = true
  end
  return parts ~= nil
end

-- Rotates v about the unit axis by angle (radians).
local function rotate(v, axis, angle)
  local c, s = math.cos(angle), math.sin(angle)
  return v * c + axis:cross(v) * s + axis * (axis:dot(v) * (1 - c))
end

local function smoothstep(x)
  x = math.max(0, math.min(1, x))
  return x * x * (3 - 2 * x)
end

-- Recoil envelope: snaps back in a few ms, settles over ~0.3 s. Peaks near 1.
local function recoilKick(t)
  if t < 0 or t > 0.6 then return 0 end
  return 1.25 * (1 - math.exp(-t / 0.006)) * math.exp(-t / 0.075)
end

-- Gun-space frame for this frame: origin and right/forward/up in world space.
local function gunFrame(aim, t)
  local o = cfg.modelOffset
  local a = math.rad(cfg.modelPitchDeg)
  local r = aim.right
  local f = aim.dir * math.cos(a) + aim.up * math.sin(a)
  local u = aim.up * math.cos(a) - aim.dir * math.sin(a)
  local origin = aim.pos + aim.right * o.x + aim.dir * o.y + aim.up * o.z

  local kick = recoilKick(t - lastShot)
  if kick > 0 then
    -- Flip the muzzle up about the grip, then push the whole gun back.
    local p = math.rad(cfg.recoilPitchDeg) * kick
    local pivot = origin + r * RECOIL_PIVOT.x + f * RECOIL_PIVOT.y + u * RECOIL_PIVOT.z
    origin = pivot + rotate(origin - pivot, r, p) - f * (cfg.recoilBack * kick)
    f, u = rotate(f, r, p), rotate(u, r, p)
  end
  return origin, r, f, u
end

local function toWorld(origin, r, f, u, v)
  return origin + r * v.x + f * v.y + u * v.z
end

-- Pose of each moving part in its own frame: angles in radians.
--   hammer:   about +X, positive tips the spur back (cocking)
--   trigger:  about +X, negative swings the blade back (pulling)
--   cylinder: about +Y, positive turns the top chamber to the left
local function actionAngles(trigger, t)
  local pull = math.max(0, math.min(1, trigger / cfg.triggerPress))
  local hammer, cylinderTurn = 0, 0
  if not triggerHeld then
    -- Double action: the cylinder indexes in the first part of the pull, the
    -- hammer comes back all the way to the break.
    hammer = math.rad(cfg.hammerCockDeg) * smoothstep(pull)
    cylinderTurn = smoothstep(pull * 1.3)
  end
  return {
    hammer = hammer,
    trigger = -math.rad(cfg.triggerTravelDeg) * pull,
    cylinder = math.rad(60) * (shotsFired + cylinderTurn),
    flash = t - lastShot < cfg.flashTime,
  }
end

local function placePart(o, pos, f, u)
  local rot = quatFromDir(f, u)
  o:setHidden(false)
  o:setPosRot(pos.x, pos.y, pos.z, rot.x, rot.y, rot.z, rot.w)
end

local function placeModel(aim, trigger, t)
  if not ensureModel() then return end
  if not aim.valid then
    for _, o in pairs(parts) do o:setHidden(true) end
    return
  end
  local origin, r, f, u = gunFrame(aim, t)
  local ang = actionAngles(trigger, t)
  local function at(name) for _, p in ipairs(PARTS) do if p.name == name then return toWorld(origin, r, f, u, p.pivot) end end end

  placePart(parts.frame, at('frame'), f, u)
  placePart(parts.cylinder, at('cylinder'), f, rotate(u, f, -ang.cylinder))
  placePart(parts.hammer, at('hammer'), rotate(f, r, ang.hammer), rotate(u, r, ang.hammer))
  placePart(parts.trigger, at('trigger'), rotate(f, r, ang.trigger), rotate(u, r, ang.trigger))
  if ang.flash then
    placePart(parts.flash, at('flash'), f, rotate(u, f, flashRoll))
  else
    parts.flash:setHidden(true)
  end
end

-- Muzzle in world space at rest (no recoil), for the laser.
local function muzzlePos(aim)
  if not parts then return aim.pos end
  local o = cfg.modelOffset
  local a = math.rad(cfg.modelPitchDeg)
  local f = aim.dir * math.cos(a) + aim.up * math.sin(a)
  local u = aim.up * math.cos(a) - aim.dir * math.sin(a)
  local origin = aim.pos + aim.right * o.x + aim.dir * o.y + aim.up * o.z
  return toWorld(origin, aim.right, f, u, MUZZLE)
end

-- Advances the cylinder and starts the recoil and muzzle flash.
local function cycleAction(t)
  shotsFired = shotsFired + 1
  lastShot = t
  flashRoll = math.random() * math.pi * 2
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
      "extensions.load('vrPistolHit'); extensions.vrPistolHit.hit(%.4f,%.4f,%.4f,%.6f,%.6f,%.6f,%.2f,%.3f,%.1f,%.3f,%.1f,%.3f,%s)",
      origin.x, origin.y, origin.z, dir.x, dir.y, dir.z, wallDist, cfg.hitRadius,
      cfg.impactForce, cfg.blastRadius, cfg.blastForce, cfg.blastInversion, tostring(cfg.deflateTyres)))
  elseif wallDist < cfg.range then
    addMarker(origin + dir * wallDist, missColour)
  end
end

-- Called from vehicle Lua when a shot connects.
function M.onVehicleHit(vehId, x, y, z, nodesPushed)
  addMarker(vec3(x, y, z), hitColour)
  log('D', 'vrPistol', string.format('hit vehicle %d, pushed %d nodes', vehId, nodesPushed))
end

function M.fireFromCamera()
  cycleAction(now())
  M.shoot(getCameraPosition(), core_camera.getForward())
end

function M.setLaser(enabled) cfg.laser = enabled and true or false end

function M.setModelOffset(x, y, z, pitchDeg)
  cfg.modelOffset = {x = x or 0, y = y or 0, z = z or 0}
  cfg.modelPitchDeg = pitchDeg or 0
end

function M.getConfig() return cfg end

-- For tests and tuning: the action state the model is drawn with.
function M.getActionState(trigger)
  local t = now()
  local a = actionAngles(trigger or 0, t)
  a.shotsFired = shotsFired
  a.recoil = recoilKick(t - lastShot)
  return a
end

-- ------------------------------------------------------------- hooks ---

-- Stay loaded across level changes (BeamNG unloads "auto" extensions).
function M.onInit()
  if setExtensionUnloadMode then setExtensionUnloadMode(M, 'manual') end
end

function M.onPreRender()
  local aim = vrHands.getPose(cfg.hand, 'aim')
  local trigger = aim.valid and vrHands.getTrigger(cfg.hand) or 0
  local t = now()

  if aim.valid then
    if cfg.laser then
      -- Drawn from the muzzle; the shot itself still uses the aim ray.
      local dist = staticHitDistance(aim.pos, aim.dir)
      local from = muzzlePos(aim)
      debugDrawer:drawLine(from, aim.pos + aim.dir * math.min(dist, 60), laserColour)
    end
    if not triggerHeld and trigger >= cfg.triggerPress then
      triggerHeld = true
      if t - lastShot >= cfg.cooldown then
        cycleAction(t)
        M.shoot(aim.pos, aim.dir)
      else
        shotsFired = shotsFired + 1  -- too soon: the action still cycles, no shot
      end
    elseif triggerHeld and trigger <= cfg.triggerRelease then
      triggerHeld = false
    end
  else
    triggerHeld = false
  end

  placeModel(aim, trigger, t)

  for i = #markers, 1, -1 do
    local m = markers[i]
    if t > m.untilTime then table.remove(markers, i)
    else debugDrawer:drawSphere(m.pos, 0.06, m.colour) end
  end
end

function M.onClientEndMission()
  deleteParts()
end

function M.onExtensionUnloaded()
  M.onClientEndMission()
end

return M
