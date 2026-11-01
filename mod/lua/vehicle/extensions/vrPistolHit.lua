-- vrPistolHit: vehicle-side half of vrPistol. Finds where the bullet ray meets
-- the vehicle's nodes and applies the impact model from Bolides'
-- BulletDamage.lua: a mass-scaled push on the struck node along the bullet, plus
-- a falloff blast on nearby nodes that dents the body. A hit near a wheel hub
-- can also deflate that tyre.
--
-- A shot whose path passes through the driver box (a cube around the driver's
-- head and chest, hung from the vehicle's driver camera) also switches off this
-- vehicle's AI, so it coasts: no throttle, no brake, hands off the wheel.

local M = {}

local function clamp(x, a, b) return math.max(a, math.min(b, x)) end

local function deflateNearbyTyre(hitPos)
  if not (v.data.wheels and beamstate and beamstate.deflateTire) then return end
  for wheelId, wheel in pairs(v.data.wheels) do
    if wheel.node1 and wheel.node2 and wheel.radius then
      local hub = (obj:getNodePosition(wheel.node1) + obj:getNodePosition(wheel.node2)) * 0.5
      if (hitPos - hub):length() < wheel.radius * 1.05 then
        pcall(beamstate.deflateTire, wheelId)
        return
      end
    end
  end
end

-- ------------------------------------------------------------ driver ---

-- The driver camera from the jbeam camerasInternal section. Each internal
-- camera is a node beamed to the body, so it moves with the cabin. Looked up
-- once: {cid = node} if the camera node can be found, otherwise
-- {offset = jbeam offset from the ref node, ref = ref node cid}. false = none.
local driverCam

local CAM_RANK = {driver = 4, rider = 3, dash = 2}

local function nodePos0(node)
  local p = node and node.pos
  if p then return vec3(p.x or p[1], p.y or p[2], p.z or p[3]) end
  if node and node.posX then return vec3(node.posX, node.posY, node.posZ) end
end

local function nodeCid(ref)
  if type(ref) == 'number' then return ref end
  if type(ref) == 'string' then
    for _, n in pairs(v.data.nodes) do if n.name == ref then return n.cid end end
  end
end

local function pickCamera()
  local best, bestRank
  for _, c in pairs(v.data.camerasInternal or {}) do
    if type(c) == 'table' and not c.hidden then
      local rank = CAM_RANK[c.type] or (c.isInside and 1) or 0
      if not bestRank or rank > bestRank then best, bestRank = c, rank end
    end
  end
  return best
end

local function findDriverCam()
  local c = pickCamera()
  if not c then return false end
  local cid = c.camNodeID or c.cid or nodeCid(c.idCam)
  if cid and v.data.nodes[cid] then return {cid = cid} end
  if not c.x then return false end
  local pos = vec3(c.x, c.y, c.z)
  -- The camera node itself, created at the camera's jbeam position.
  for _, n in pairs(v.data.nodes) do
    local p = nodePos0(n)
    if p and (p - pos):squaredLength() < 1e-4 then return {cid = n.cid} end
  end
  local ref = v.data.refNodes and v.data.refNodes[0]
  local refCid = ref and ref.ref
  local refPos = refCid and nodePos0(v.data.nodes[refCid])
  if refPos then return {ref = refCid, offset = pos - refPos} end
  return false
end

-- Vehicle axes in world orientation: forward, up, right.
local function bodyAxes()
  local f = vec3(obj:getDirectionVector()):normalized()
  local u = vec3(obj:getDirectionVectorUp()):normalized()
  return f, u, f:cross(u):normalized()
end

-- Driver camera position relative to obj:getPosition(), or nil.
local function driverCamPos(f, u, r)
  if driverCam == nil then
    driverCam = findDriverCam()
    if not driverCam then log('W', 'vrPistolHit', 'no driver camera found; driver shots will not stop this vehicle') end
  end
  if not driverCam then return nil end
  if driverCam.cid then return obj:getNodePosition(driverCam.cid) end
  -- jbeam axes: +x is the car's left, -y its front, +z up.
  local o = driverCam.offset
  return obj:getNodePosition(driverCam.ref) - r * o.x - f * o.y + u * o.z
end

-- The driver box: centre (relative to obj:getPosition()), axes, half sizes.
local function driverBox(size, drop)
  local f, u, r = bodyAxes()
  local cam = driverCamPos(f, u, r)
  if not cam then return nil end
  local h = size * 0.5
  return cam - u * drop, {r, f, u}, {h, h, h}
end

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

local function aiDriving()
  if not (ai and ai.setMode) then return false end
  if type(ai.mode) == 'string' then return ai.mode ~= 'disabled' end
  if ai.isDriving then
    local ok, driving = pcall(ai.isDriving)
    return ok and driving == true
  end
  return false
end

-- AI off, pedals released: the car rolls on in gear and slows down by itself.
local function stopDriver()
  if not aiDriving() then return false end
  ai.setMode('disabled')
  if input and input.event then
    pcall(input.event, 'throttle', 0, 2)
    pcall(input.event, 'brake', 0, 2)
  end
  return true
end

-- Driver shot test: true if the path from origin up to maxDist goes through
-- the driver box. origin is relative to obj:getPosition().
local function driverShot(origin, dir, maxDist, size, drop)
  if not size or size <= 0 then return nil end
  local center, axes, half = driverBox(size, drop or 0)
  if not center then return nil end
  local t = rayBox(origin, dir, center, axes, half, maxDist)
  if t then return origin + dir * t end
end

-- For vrPistol.showDriverBoxes: sends this vehicle's driver box to GE.
function M.reportDriverBox(size, drop)
  local center, axes, half = driverBox(size or 0.4, drop or 0.15)
  if not center then
    obj:queueGameEngineLua(string.format('if vrPistol then vrPistol.onDriverBox(%d) end', objectId))
    return
  end
  local c, r, f = obj:getPosition() + center, axes[1], axes[2]
  obj:queueGameEngineLua(string.format(
    'if vrPistol then vrPistol.onDriverBox(%d,%.4f,%.4f,%.4f,%.5f,%.5f,%.5f,%.5f,%.5f,%.5f,%.4f,%s) end',
    objectId, c.x, c.y, c.z, r.x, r.y, r.z, f.x, f.y, f.z, half[1], tostring(aiDriving())))
end

-- Origin arrives in world space; node positions are relative to obj:getPosition().
function M.hit(ox, oy, oz, dx, dy, dz, maxDist, hitRadius, impactForce, blastRadius, blastForce,
               blastInversion, deflateTyres, driverBoxSize, driverBoxDrop)
  local origin = vec3(ox, oy, oz) - obj:getPosition()
  local dir = vec3(dx, dy, dz)
  local hitR2 = hitRadius * hitRadius

  -- The whole path counts, not just the first panel it meets: a bullet that
  -- stops in the door or glass still reaches the driver.
  local driverAt = driverShot(origin, dir, maxDist, driverBoxSize, driverBoxDrop)
  if driverAt and stopDriver() then
    local w = obj:getPosition() + driverAt
    obj:queueGameEngineLua(string.format('if vrPistol then vrPistol.onDriverHit(%d,%.4f,%.4f,%.4f) end',
      objectId, w.x, w.y, w.z))
  end

  local hitNode, hitT
  for _, node in pairs(v.data.nodes) do
    local p = obj:getNodePosition(node.cid)
    local t = (p - origin):dot(dir)
    if t > 0 and t < maxDist and (not hitT or t < hitT) then
      if (p - (origin + dir * t)):squaredLength() < hitR2 then hitNode, hitT = node.cid, t end
    end
  end
  if not hitNode then return end

  -- Impact point on the ray itself, beside the struck node.
  local impact = origin + dir * hitT

  local mass = 1
  if obj.getNodeMass then mass = obj:getNodeMass(hitNode) or 1 end
  obj:applyForceVector(hitNode, dir * impactForce * mass)

  local pushed = 1
  if blastRadius > 0 then
    local inner = blastRadius * 0.5
    local forceBase = blastForce * 2000
    for _, node in pairs(v.data.nodes) do
      local offset = obj:getNodePosition(node.cid) - impact
      local distance = offset:length()
      if distance <= blastRadius and distance > 1e-6 then
        local sign = fsign(math.random() - blastInversion)
        local force = forceBase * clamp(1 - (distance - inner) / (blastRadius - inner), 0, 1)
        obj:applyForceVector(node.cid, offset * (sign * force / distance))
        pushed = pushed + 1
      end
    end
  end

  if deflateTyres then deflateNearbyTyre(impact) end

  local world = obj:getPosition() + impact
  obj:queueGameEngineLua(string.format('if vrPistol then vrPistol.onVehicleHit(%d,%.4f,%.4f,%.4f,%d) end',
    objectId, world.x, world.y, world.z, pushed))
end

return M
