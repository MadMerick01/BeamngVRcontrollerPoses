-- vrPistol: a revolver on the right VR controller that shoots traffic.
--
--   extensions.load('vrPistol')       -- also loads vrHands
--   vrPistol.setLaser(true|false)     -- aiming laser (off by default)
--   vrPistol.setModelOffset(x, y, z, pitchDeg)  -- fit the model to your hand
--   vrPistol.fireFromCamera()         -- desktop test: shoot along the camera view
--
-- The shot always travels along the controller's aim ray (OpenXR aim pose), so
-- the model offset only changes how the gun looks, never where it hits.
--
-- The model is a double-action revolver (mod/art/shapes/vrrevolver, built by
-- tools/revolverModel/build_revolver.py). Squeezing the trigger cocks the
-- hammer and turns the cylinder; at the break point the hammer drops, the gun
-- kicks and the muzzle flashes. By default a low-poly copy (vrPistolMesh.lua) is
-- drawn with the debug drawer every frame instead, so it never lags at speed;
-- set getConfig().modelStyle = 'textured' for the full model.

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
  hapticAmplitude = 1.0, -- controller vibration per shot (0 disables)
  hapticSeconds = 0.06,
  flashTime = 0.045,
  -- Gunshot from Bolides (PlayerGunShot.wav), same playback and volume as its FirstPersonShoot.
  -- vrPistol.testSound() plays it from the console.
  shotSound = '/art/sound/vrpistol/revolver_shot.wav',
  shotVolume = 18,
  shotPitch = 1.0,
  equipSound = '/art/sound/vrpistol/pistol_equip.wav',  -- played when the gun is drawn ('' mutes it)
  equipVolume = 12,
  laser = false,
  hitMarkers = false,    -- orange/grey spheres where shots land
  -- Hold the grip to draw the gun; let go to put it away (no gun, no shots).
  -- While it is drawn BeamNG's own controller pointer (black line and orange
  -- ball) is switched off, and switched back on when the gun is put away.
  equipOnGrip = true,
  gripPress = 0.6,
  gripRelease = 0.4,
  hideGamePointer = true,
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
  -- 'simple' draws a low-poly gun with the debug drawer, fresh every frame like
  -- the laser, so it never trails the car. 'textured' uses the full revolver
  -- model, which BeamNG redraws late (it lags at speed).
  modelStyle = 'simple',
  simpleDoubleSided = false,  -- also draw each triangle's back face
  -- How the textured model follows the hand (see startModelTest):
  -- 'server' moves the scene object, 'client' also moves its rendered copy directly.
  placeMode = 'server',
  predictSeconds = 0,    -- shift the model along the camera's motion by this much time
}

local parts, modelMissingLogged, materialsLoaded
local triggerHeld, lastShot = false, -math.huge
local equipped, savedPointerSetting = false, nil
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
  -- Shape materials in /art/shapes are not loaded automatically outside a
  -- level's own folders, so the revolver came out orange (no material).
  if not materialsLoaded then
    materialsLoaded = true
    local file = cfg.modelDir .. 'main.materials.json'
    local okM, errM = pcall(function() loadJsonMaterialsFile(file) end)
    if not okM then log('W', 'vrPistol', 'could not load ' .. file .. ': ' .. tostring(errM)) end
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

-- Scene objects made from Lua are server objects; the copy that gets drawn is
-- updated over the (local) network, which can lag a frame or more behind.
-- 'client' mode moves the drawn copy too, when the engine lets us reach it.
local clientObjects = setmetatable({}, {__mode = 'k'})
local function clientOf(o)
  local c = clientObjects[o]
  if c == nil then
    local ok, co = pcall(function() return o:getClientObject() end)
    c = ok and co or false
    clientObjects[o] = c
  end
  return c or nil
end

local predictShift = vec3(0, 0, 0)

local function placePart(o, pos, f, u)
  local rot = quatFromDir(f, u)
  pos = pos + predictShift
  if o == parts.frame then M._lastFramePos = pos end
  o:setHidden(false)
  o:setPosRot(pos.x, pos.y, pos.z, rot.x, rot.y, rot.z, rot.w)
  if cfg.placeMode == 'client' then
    local c = clientOf(o)
    if c then pcall(function() c:setPosRot(pos.x, pos.y, pos.z, rot.x, rot.y, rot.z, rot.w) end) end
  end
end

-- Low-poly gun drawn with the debug drawer (mod/lua/ge/extensions/vrPistolMesh.lua,
-- built by tools/revolverModel/build_lowpoly.py).
local simpleMesh, simpleMissing
local LIGHT = {0.3, -0.4, 0.85}
do local l = math.sqrt(LIGHT[1] ^ 2 + LIGHT[2] ^ 2 + LIGHT[3] ^ 2) LIGHT = {LIGHT[1] / l, LIGHT[2] / l, LIGHT[3] / l} end
local SHADES = 12
local colourCache = {}
local va, vb, vc

local function loadSimpleMesh()
  if simpleMesh or simpleMissing then return simpleMesh end
  -- lua/ge/extensions is on the require path (core/cameraUpdate requires 'core/cameraInput').
  local ok, mesh = pcall(require, 'vrPistolMesh')
  if not ok or type(mesh) ~= 'table' then
    simpleMissing = true
    log('W', 'vrPistol', 'simple gun mesh missing: ' .. tostring(mesh))
    return nil
  end
  -- Precompute each triangle's normal in part space.
  for _, part in pairs(mesh) do
    local t, n = part.tris, {}
    for i = 1, #t, 9 do
      local ax, ay, az = t[i + 3] - t[i], t[i + 4] - t[i + 1], t[i + 5] - t[i + 2]
      local bx, by, bz = t[i + 6] - t[i], t[i + 7] - t[i + 1], t[i + 8] - t[i + 2]
      local nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
      local l = math.sqrt(nx * nx + ny * ny + nz * nz)
      if l < 1e-12 then l = 1 end
      n[#n + 1], n[#n + 2], n[#n + 3] = nx / l, ny / l, nz / l
    end
    part.normals = n
  end
  va, vb, vc = vec3(0, 0, 0), vec3(0, 0, 0), vec3(0, 0, 0)
  simpleMesh = mesh
  return mesh
end

local function shadedColour(r, g, b, level)
  local key = r * 65536 + g * 256 + b + level * 16777216
  local c = colourCache[key]
  if not c then
    -- Lift very dark materials (blued steel) so their shape still reads.
    local k = 0.35 + 0.65 * level / SHADES
    local function ch(x) return math.min(255, math.floor((x * 1.1 + 22) * k + 0.5)) end
    -- drawTriSolid takes a packed colour number (BeamNG's color() helper).
    if type(color) == 'function' then c = color(ch(r), ch(g), ch(b), 255) end
    if type(c) ~= 'number' then c = ch(r) + ch(g) * 256 + ch(b) * 65536 + 255 * 16777216 end
    colourCache[key] = c
  end
  return c
end

local function drawSimplePart(name, pos, f, u)
  local part = simpleMesh and simpleMesh[name]
  if not part then return end
  local r = f:cross(u)
  local px, py, pz = pos.x, pos.y, pos.z
  local rx, ry, rz, fx, fy, fz, ux, uy, uz = r.x, r.y, r.z, f.x, f.y, f.z, u.x, u.y, u.z
  local t, n, col = part.tris, part.normals, part.colours
  local both = cfg.simpleDoubleSided
  for i = 0, #t / 9 - 1 do
    local j = i * 9
    local function corner(v, k)
      local x, y, z = t[k + 1], t[k + 2], t[k + 3]
      v:set(px + rx * x + fx * y + ux * z, py + ry * x + fy * y + uy * z, pz + rz * x + fz * y + uz * z)
    end
    corner(va, j) corner(vb, j + 3) corner(vc, j + 6)
    local nx, ny, nz = n[i * 3 + 1], n[i * 3 + 2], n[i * 3 + 3]
    local wx, wy, wz = rx * nx + fx * ny + ux * nz, ry * nx + fy * ny + uy * nz, rz * nx + fz * ny + uz * nz
    local d = math.abs(wx * LIGHT[1] + wy * LIGHT[2] + wz * LIGHT[3])
    local c = shadedColour(col[i * 3 + 1], col[i * 3 + 2], col[i * 3 + 3], math.floor(d * SHADES + 0.5))
    debugDrawer:drawTriSolid(va, vb, vc, c)
    if both then debugDrawer:drawTriSolid(va, vc, vb, c) end
  end
end

local simpleFailed

local function placeModel(aim, trigger, t)
  local simple = cfg.modelStyle == 'simple' and not simpleFailed and loadSimpleMesh()
  if simple then
    if parts then deleteParts() end
  elseif not ensureModel() then
    return
  end
  predictShift = vec3(0, 0, 0)
  if cfg.predictSeconds ~= 0 and vrHands.getAnchorMotion then
    predictShift = vrHands.getAnchorMotion().vel * cfg.predictSeconds
  end
  if not aim.valid then
    for _, o in pairs(parts or {}) do o:setHidden(true) end
    return
  end
  local origin, r, f, u = gunFrame(aim, t)
  local ang = actionAngles(trigger, t)
  local function at(name) for _, p in ipairs(PARTS) do if p.name == name then return toWorld(origin, r, f, u, p.pivot) end end end

  local function put(name, pos, pf, pu)
    if simple then
      local ok, err = pcall(drawSimplePart, name, pos, pf, pu)
      if not ok and not simpleFailed then
        -- Don't throw every frame: fall back to the textured model.
        simpleFailed = true
        log('E', 'vrPistol', 'simple gun cannot be drawn, using the textured model: ' .. tostring(err))
      end
    else
      placePart(parts[name], pos, pf, pu)
    end
  end
  put('frame', at('frame'), f, u)
  put('cylinder', at('cylinder'), f, rotate(u, f, -ang.cylinder))
  put('hammer', at('hammer'), rotate(f, r, ang.hammer), rotate(u, r, ang.hammer))
  put('trigger', at('trigger'), rotate(f, r, ang.trigger), rotate(u, r, ang.trigger))
  if ang.flash then
    put('flash', at('flash'), f, rotate(u, f, flashRoll))
  elseif not simple then
    parts.flash:setHidden(true)
  end
end

-- Muzzle in world space at rest (no recoil), for the laser.
local function muzzlePos(aim)
  if not parts and not simpleMesh then return aim.pos end
  local o = cfg.modelOffset
  local a = math.rad(cfg.modelPitchDeg)
  local f = aim.dir * math.cos(a) + aim.up * math.sin(a)
  local u = aim.up * math.cos(a) - aim.dir * math.sin(a)
  local origin = aim.pos + aim.right * o.x + aim.dir * o.y + aim.up * o.z
  return toWorld(origin, aim.right, f, u, MUZZLE)
end

-- Gunshot and equip sound, played step for step the way Bolides' FirstPersonShoot plays its
-- player shot: every shot first makes sure a pool of 2D SFX sources exists in
-- the player vehicle's Lua (so it survives a vehicle reload or switch), then a
-- second queued command plays the next source in the pool, trying each playSFX
-- signature BeamNG has used, with playSFXOnce as the last resort. With no
-- player vehicle it falls back to the GUI channel.
local function ensureSources(veh, name, file, count)
  veh:queueLuaCommand(string.format([[
    _G.__vrPistolShotAudio = _G.__vrPistolShotAudio or { ids = {}, pools = {} }
    local A = _G.__vrPistolShotAudio.ids
    local P = _G.__vrPistolShotAudio.pools
    local function mk(path, nm)
      if A[nm] then return end
      local id = obj:createSFXSource(path, "Audio2D", nm, 0)
      A[nm] = id
      log('I', 'vrPistol', 'created SFX source ' .. nm .. ' = ' .. tostring(id))
    end
    local base = %q
    local path = %q
    local count = %d
    P[base] = P[base] or { ids = {}, index = 1 }
    local pool = P[base]
    for i = 1, count do
      local nm = base .. "_" .. tostring(i)
      mk(path, nm)
      pool.ids[i] = nm
    end
    if (pool.index or 1) < 1 or (pool.index or 1) > count then
      pool.index = 1
    end
  ]], name, file, count))
end

local function playSound(name, file, vol, pitch, count)
  if not file or file == '' then return end
  local veh = be:getPlayerVehicle(0)
  if not veh or not veh.queueLuaCommand then
    local ok, err = pcall(function() Engine.Audio.playOnce('AudioGui', file) end)
    if not ok then log('W', 'vrPistol', 'no player vehicle and Engine.Audio.playOnce failed: ' .. tostring(err)) end
    return
  end
  ensureSources(veh, name, file, count)
  veh:queueLuaCommand(string.format([[
    if not (_G.__vrPistolShotAudio and _G.__vrPistolShotAudio.ids) then return end
    local A = _G.__vrPistolShotAudio.ids
    local P = _G.__vrPistolShotAudio.pools or {}
    local pool = P[%q]
    if not pool or not pool.ids or #pool.ids == 0 then return end
    local idx = pool.index or 1
    if idx > #pool.ids then idx = 1 end
    local nm = pool.ids[idx]
    pool.index = idx + 1
    if pool.index > #pool.ids then pool.index = 1 end
    local id = A[nm]
    if not id then return end

    if obj.setSFXSourceLooping then pcall(function() obj:setSFXSourceLooping(id, false) end) end
    if obj.setSFXSourceLoop then pcall(function() obj:setSFXSourceLoop(id, false) end) end

    if obj.setSFXSourceVolume then pcall(function() obj:setSFXSourceVolume(id, 1.0) end) end
    if obj.setSFXVolume then      pcall(function() obj:setSFXVolume(id, 1.0) end) end
    if obj.setVolume then         pcall(function() obj:setVolume(id, 1.0) end) end

    local played = false
    if obj.playSFX then
      played = played or pcall(function() obj:playSFX(id) end)
      played = played or pcall(function() obj:playSFX(id, 0) end)
      played = played or pcall(function() obj:playSFX(id, false) end)
      played = played or pcall(function() obj:playSFX(id, 0, false) end)
      played = played or pcall(function() obj:playSFX(id, 0, %0.3f, %0.3f, false) end)
      played = played or pcall(function() obj:playSFX(id, %0.3f, %0.3f, false) end)
      played = played or pcall(function() obj:playSFX(id, 0, false, %0.3f, %0.3f) end)
    end

    if (not played) and obj.playSFXOnce and %q ~= "" then
      pcall(function() obj:playSFXOnce(%q, 0, %0.3f, %0.3f) end)
    end

    if obj.setSFXSourceVolume then pcall(function() obj:setSFXSourceVolume(id, %0.3f) end) end
    if obj.setSFXSourcePitch  then pcall(function() obj:setSFXSourcePitch(id, %0.3f) end) end
  ]], name, vol, pitch, vol, pitch, vol, pitch, file, file, vol, pitch, vol, pitch))
end

local function playShotSound() playSound('vrPistolShot', cfg.shotSound, cfg.shotVolume, cfg.shotPitch or 1.0, 6) end
local function playEquipSound() playSound('vrPistolEquip', cfg.equipSound, cfg.equipVolume, 1.0, 2) end

-- Console check for the gunshot without shooting anything.
function M.testSound(which) if which == 'equip' then playEquipSound() else playShotSound() end end

-- Advances the cylinder, starts the recoil and muzzle flash, and fires the sound.
local function cycleAction(t)
  shotsFired = shotsFired + 1
  lastShot = t
  flashRoll = math.random() * math.pi * 2
  playShotSound()
  if cfg.hapticAmplitude > 0 then vrHands.pulse(cfg.hand, cfg.hapticAmplitude, cfg.hapticSeconds) end
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
  if not cfg.hitMarkers then return end
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

-- Model smoothness test: drive steadily at speed and watch the gun. It cycles
-- through ways of moving the model, announcing each phase, and records what
-- happened every frame to <user folder>/vrHandsCapture/model_*.jsonl.
local modelTest
local TEST_PHASES = {
  {name = 'A', placeMode = 'server', predictFrames = 0, msg = 'A: as now'},
  {name = 'B', placeMode = 'client', predictFrames = 0, msg = 'B: move the drawn copy directly'},
  {name = 'C', placeMode = 'server', predictFrames = 1, msg = 'C: lead the car by 1 frame'},
  {name = 'D', placeMode = 'server', predictFrames = 2, msg = 'D: lead the car by 2 frames'},
  {name = 'E', placeMode = 'client', predictFrames = 1, msg = 'E: drawn copy + lead by 1 frame'},
}

local function say(msg)
  log('I', 'vrPistol', msg)
  if ui_message then pcall(ui_message, msg, 8, 'vrPistolTest') end
end

function M.startModelTest(phaseSeconds)
  if modelTest then return end
  local dir = '/vrHandsCapture/'
  pcall(function() FS:directoryCreate(dir, true) end)
  modelTest = {phaseSeconds = phaseSeconds or 8, elapsed = 0, phase = 0, lines = {},
    saved = {placeMode = cfg.placeMode, predictSeconds = cfg.predictSeconds},
    path = dir .. 'model_' .. os.date('%Y%m%d_%H%M%S') .. '.jsonl'}
  say('Gun smoothness test: drive steadily at speed and watch the gun. Note which letter looks best.')
end

local function v3(v) return v and {v.x, v.y, v.z} or nil end

local function modelTestFrame(aim, dtReal)
  local T = modelTest
  if not T then return end
  T.elapsed = T.elapsed + (dtReal or 1 / 90)
  local i = math.floor(T.elapsed / T.phaseSeconds) + 1
  if i > #TEST_PHASES then
    cfg.placeMode, cfg.predictSeconds = T.saved.placeMode, T.saved.predictSeconds
    local ok = pcall(writeFile, T.path, table.concat(T.lines, '\n') .. '\n')
    local where = T.path
    pcall(function() where = FS:getUserPath() .. T.path:sub(2) end)
    say(ok and ('Gun test done. Which letter looked best? Saved: ' .. where) or 'Gun test could not be saved')
    modelTest = nil
    return
  end
  local ph = TEST_PHASES[i]
  local motion = vrHands.getAnchorMotion and vrHands.getAnchorMotion() or {}
  if i ~= T.phase then
    T.phase = i
    say('Phase ' .. ph.msg)
  end
  cfg.placeMode = ph.placeMode
  cfg.predictSeconds = ph.predictFrames * (motion.frameDt or 0.011)

  local frame = parts and parts.frame
  local server, client
  if frame then
    pcall(function() server = v3(frame:getPosition()) end)
    local c = clientOf(frame)
    if c then pcall(function() client = v3(c:getPosition()) end) end
  end
  local okP, cam = pcall(getCameraPosition)
  local entry = {
    t = T.elapsed, phase = ph.name, frameDt = motion.frameDt, anchorPos = v3(motion.pos), anchorVel = v3(motion.vel),
    camPos = okP and v3(cam) or nil, aimPos = aim.valid and v3(aim.pos) or nil,
    target = v3(M._lastFramePos), server = server, client = client, hasClient = frame and clientOf(frame) ~= nil,
  }
  local ok, text = pcall(jsonEncode, entry)
  if ok then T.lines[#T.lines + 1] = text end
end

-- For tests and tuning: the action state the model is drawn with.
function M.getActionState(trigger)
  local t = now()
  local a = actionAngles(trigger or 0, t)
  a.shotsFired = shotsFired
  a.recoil = recoilKick(t - lastShot)
  return a
end

-- ---------------------------------------------------------- equipping ---

-- BeamNG's pointer is the 'openXRuseControllers' setting. Its value is saved
-- when the gun is drawn and put back when it is put away.
local function setGamePointerHidden(hidden)
  if not settings or not settings.setValue then return end
  if hidden then
    if savedPointerSetting ~= nil then return end
    local ok, current = pcall(settings.getValue, 'openXRuseControllers')
    savedPointerSetting = (ok and current ~= nil) and current or true
    pcall(settings.setValue, 'openXRuseControllers', false)
  elseif savedPointerSetting ~= nil then
    pcall(settings.setValue, 'openXRuseControllers', savedPointerSetting)
    savedPointerSetting = nil
  end
end

local function updateEquipped(aim)
  local want = true
  if cfg.equipOnGrip then
    local grip = aim.valid and vrHands.getSqueeze(cfg.hand) or 0
    if equipped then want = aim.valid and grip > cfg.gripRelease
    else want = aim.valid and grip >= cfg.gripPress end
  end
  if want ~= equipped then
    equipped = want
    triggerHeld = false
    if cfg.hideGamePointer or not want then setGamePointerHidden(want) end
    if want and cfg.equipOnGrip then playEquipSound() end
  end
  return equipped
end

function M.isEquipped() return equipped end

-- ------------------------------------------------------------- hooks ---

-- Stay loaded across level changes (BeamNG unloads "auto" extensions).
function M.onInit()
  if setExtensionUnloadMode then setExtensionUnloadMode(M, 'manual') end
end

function M.onPreRender(dtReal)
  local aim = vrHands.getPose(cfg.hand, 'aim')
  if not updateEquipped(aim) then aim = {valid = false} end
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
  modelTestFrame(aim, dtReal)

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
  setGamePointerHidden(false)
  equipped = false
end

return M
