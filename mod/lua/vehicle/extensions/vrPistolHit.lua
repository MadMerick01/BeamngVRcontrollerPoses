-- vrPistolHit: vehicle-side half of vrPistol. Finds the node the bullet ray hits
-- and breaks a few beams around it; a hit near a wheel hub deflates that tyre.

local M = {}

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

-- Origin and hit positions arrive in world space; node positions are relative
-- to obj:getPosition().
function M.hit(ox, oy, oz, dx, dy, dz, maxDist, hitRadius, damageRadius, maxBeams)
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

  local hitPos = obj:getNodePosition(hitNode)
  local damageR2 = damageRadius * damageRadius
  local nearby = {}
  for _, beam in pairs(v.data.beams) do
    local okBroken, broken = pcall(obj.beamIsBroken, obj, beam.cid)
    if not (okBroken and broken) then
      local d1 = (obj:getNodePosition(beam.id1) - hitPos):squaredLength()
      local d2 = (obj:getNodePosition(beam.id2) - hitPos):squaredLength()
      local d = math.min(d1, d2)
      if d < damageR2 then nearby[#nearby + 1] = {cid = beam.cid, d = d} end
    end
  end
  table.sort(nearby, function(a, b) return a.d < b.d end)

  local broken = 0
  for i = 1, math.min(maxBeams, #nearby) do
    if pcall(obj.breakBeam, obj, nearby[i].cid) then broken = broken + 1 end
  end
  deflateNearbyTyre(hitPos)

  local world = obj:getPosition() + hitPos
  obj:queueGameEngineLua(string.format('if vrPistol then vrPistol.onVehicleHit(%d,%.4f,%.4f,%.4f,%d) end',
    objectId, world.x, world.y, world.z, broken))
end

return M
