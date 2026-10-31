-- vrPistolHit: vehicle-side half of vrPistol. Finds where the bullet ray meets
-- the vehicle's nodes and applies the impact model from Bolides'
-- BulletDamage.lua: a mass-scaled push on the struck node along the bullet, plus
-- a falloff blast on nearby nodes that dents the body. A hit near a wheel hub
-- can also deflate that tyre.

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

-- Origin arrives in world space; node positions are relative to obj:getPosition().
function M.hit(ox, oy, oz, dx, dy, dz, maxDist, hitRadius, impactForce, blastRadius, blastForce,
               blastInversion, deflateTyres)
  local origin = vec3(ox, oy, oz) - obj:getPosition()
  local dir = vec3(dx, dy, dz)
  local hitR2 = hitRadius * hitRadius

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
