-- World-space proximity selection only. This extension never creates a physics link.
local M = {dependencies={'beamngVRControllerPoses'}}

local cfg
local previewEnabled=false
local previewAccumulator=0
local previewMarkers={left={},right={}}
local hands={
  left={gripValue=0,logicalGripPressed=false},
  right={gripValue=0,logicalGripPressed=false}
}

local function finite(n) return type(n)=='number' and n==n and n~=math.huge and n~=-math.huge end
local function xyz(v)
  if not v then return nil end
  local x,y,z=v.x or v[1],v.y or v[2],v.z or v[3]
  if not finite(x) or not finite(y) or not finite(z) then return nil end
  return x,y,z
end
local function nowSeconds()
  if Engine and Engine.Platform and Engine.Platform.getRuntime then
    local ok,value=pcall(Engine.Platform.getRuntime)
    if ok and finite(value) then return value end
  end
  return os.clock()
end
local function poseExtension()
  return extensions and extensions.beamngVRControllerPoses or nil
end
local function clearFields(state,reason)
  state.candidateSelected=false
  state.candidateVehicleId=nil; state.candidateNodeId=nil
  state.candidateNodeWorldPosition=nil; state.candidateDistanceMetres=nil
  state.selectionTime=nil; state.selectionControllerUpdateCounter=nil
  state.selectionObjectSignature=nil; state.selectionFailureReason=reason
end
local function updatePose(hand)
  local state=hands[hand]
  local provider=poseExtension()
  local pose=provider and provider.getControllerWorldPose and provider.getControllerWorldPose(hand) or nil
  state.poseValid=pose~=nil and pose.valid==true and finite(pose.ageMs) and pose.ageMs<=cfg.poseStaleAfterMs
  state.poseAgeMs=pose and pose.ageMs or nil
  local x,y,z
  if pose then x,y,z=xyz(pose.position) end
  if not x then state.poseValid=false; state.handWorldPosition=nil
  else state.handWorldPosition={x=x,y=y,z=z} end
  state.controllerUpdateCounter=pose and pose.updateCounter or nil
  return state.poseValid and state.handWorldPosition or nil
end
local function safeVehicleId(vehicle)
  if not vehicle then return nil end
  local ok,value=pcall(function() return vehicle:getId() end)
  if not ok or not finite(value) then
    ok,value=pcall(function() return vehicle:getID() end)
  end
  return ok and finite(value) and value or nil
end
local function safeNodeCount(vehicle)
  local ok,value=pcall(function() return vehicle:getNodeCount() end)
  if not ok or not finite(value) or value<=0 then return nil end
  return math.floor(value)
end
local function safePosition(vehicle)
  local ok,value=pcall(function() return vehicle:getPosition() end)
  if not ok then return nil end
  local x,y,z=xyz(value); if not x then return nil end
  return x,y,z
end
local function safeNodePosition(vehicle,nodeId)
  local ok,value=pcall(function() return vehicle:getNodePosition(nodeId) end)
  if not ok then return nil end
  return xyz(value)
end
local function playerVehicleId()
  if not cfg.excludePlayerVehicle or type(getPlayerVehicle)~='function' then return nil end
  local ok,vehicle=pcall(getPlayerVehicle,0)
  return ok and safeVehicleId(vehicle) or nil
end
local function eligible(vehicle,excludedId)
  local id=safeVehicleId(vehicle)
  if not id or id==excludedId or id==cfg.excludedVehicleId then return nil end
  if type(vehicle.isActive)=='function' then
    local ok,active=pcall(function() return vehicle:isActive() end)
    if not ok or active==false then return nil end
  end
  local count=safeNodeCount(vehicle)
  local vx,vy,vz=safePosition(vehicle)
  if not count or not vx then return nil end
  return id,count,vx,vy,vz
end
local function logSearch(hand,state)
  local result=state.candidateSelected and
    ('vehicle='..state.candidateVehicleId..' node='..state.candidateNodeId..' distance='..string.format('%.4f',state.candidateDistanceMetres)) or
    ('failure='..tostring(state.selectionFailureReason))
  log('I','vrNodeGrabber','search hand='..hand..' vehicles='..state.vehiclesExamined..' nodes='..state.nodesExamined..' '..result)
end

function M.clearCandidate(hand,reason)
  local state=hands[hand]
  if not state then return false,'hand must be left or right' end
  clearFields(state,reason or 'explicit clear')
  return true
end

function M.findNearestNode(hand)
  local state=hands[hand]
  if not state then return nil,'hand must be left or right' end
  state.vehiclesExamined=0; state.nodesExamined=0
  local handPosition=updatePose(hand)
  clearFields(state,nil)
  if not handPosition then state.selectionFailureReason='controller pose invalid or stale'; logSearch(hand,state); return nil,state.selectionFailureReason end
  local hx,hy,hz=xyz(handPosition)
  local radiusSquared=cfg.grabRadiusMetres*cfg.grabRadiusMetres
  local bestSquared,bestVehicle,bestNode,bestX,bestY,bestZ,bestSignature=radiusSquared,nil,nil,nil,nil,nil,nil
  local excludedId=playerVehicleId()
  if type(activeVehiclesIterator)~='function' then state.selectionFailureReason='active vehicle iterator unavailable'; logSearch(hand,state); return nil,state.selectionFailureReason end
  for vehicle in activeVehiclesIterator() do
    local vehicleId,nodeCount,vx,vy,vz=eligible(vehicle,excludedId)
    if vehicleId then
      state.vehiclesExamined=state.vehiclesExamined+1
      for nodeId=0,nodeCount-1 do
        local nx,ny,nz=safeNodePosition(vehicle,nodeId)
        state.nodesExamined=state.nodesExamined+1
        if nx then
          -- BeamNG node positions are vehicle-relative, as used by nodegrabberGamepad.
          local wx,wy,wz=vx+nx,vy+ny,vz+nz
          if finite(wx) and finite(wy) and finite(wz) then
            local dx,dy,dz=wx-hx,wy-hy,wz-hz
            local squared=dx*dx+dy*dy+dz*dz
            if squared<=bestSquared then
              bestSquared,bestVehicle,bestNode=squared,vehicleId,nodeId
              bestX,bestY,bestZ,bestSignature=wx,wy,wz,tostring(vehicle)
            end
          end
        end
      end
    end
  end
  if not bestVehicle then state.selectionFailureReason='no eligible node within radius'; logSearch(hand,state); return nil,state.selectionFailureReason end
  state.candidateSelected=true; state.candidateVehicleId=bestVehicle; state.candidateNodeId=bestNode
  state.candidateNodeWorldPosition={x=bestX,y=bestY,z=bestZ}
  state.candidateDistanceMetres=math.sqrt(bestSquared); state.selectionTime=nowSeconds()
  state.selectionControllerUpdateCounter=state.controllerUpdateCounter
  state.selectionObjectSignature=bestSignature; state.selectionFailureReason=nil
  logSearch(hand,state)
  return {hand=hand,vehicleId=bestVehicle,nodeId=bestNode,nodeWorldPosition=state.candidateNodeWorldPosition,
    distanceMetres=state.candidateDistanceMetres,selectionTime=state.selectionTime,
    controllerUpdateCounter=state.selectionControllerUpdateCounter}
end

function M.setGripState(hand,value)
  local state=hands[hand]
  if not state then return false,'hand must be left or right' end
  if not finite(value) or value<0 or value>1 then return false,'grip value must be between 0.0 and 1.0' end
  state.gripValue=value
  if not state.logicalGripPressed and value>=cfg.gripPressThreshold then
    state.logicalGripPressed=true; M.findNearestNode(hand)
  elseif state.logicalGripPressed and value<=cfg.gripReleaseThreshold then
    state.logicalGripPressed=false; M.clearCandidate(hand,'grip released')
  end
  return true
end

local function reacquire(vehicleId)
  if type(getObjectByID)~='function' then return nil end
  local ok,value=pcall(getObjectByID,vehicleId)
  return ok and value or nil
end
local function validateCandidate(hand)
  local state=hands[hand]
  if not updatePose(hand) then if state.candidateSelected then M.clearCandidate(hand,'controller pose invalid or stale') end; return end
  if not state.candidateSelected then return end
  local vehicle=reacquire(state.candidateVehicleId)
  if not vehicle or tostring(vehicle)~=state.selectionObjectSignature then M.clearCandidate(hand,'selected vehicle despawned or replaced'); return end
  local vx,vy,vz=safePosition(vehicle); local nx,ny,nz=safeNodePosition(vehicle,state.candidateNodeId)
  if not vx or not nx then M.clearCandidate(hand,'selected node unavailable'); return end
  state.candidateNodeWorldPosition={x=vx+nx,y=vy+ny,z=vz+nz}
end
local function refreshPreview()
  local radiusSquared=cfg.grabRadiusMetres*cfg.grabRadiusMetres
  local cap=math.max(0,math.floor(cfg.nearbyNodePreviewMaximumMarkersPerHand))
  local excludedId=playerVehicleId()
  for _,hand in ipairs({'left','right'}) do
    local markers={}; previewMarkers[hand]=markers
    local position=updatePose(hand); local hx,hy,hz
    if position then hx,hy,hz=xyz(position) end
    if hx and type(activeVehiclesIterator)=='function' then
      for vehicle in activeVehiclesIterator() do
        local _,count,vx,vy,vz=eligible(vehicle,excludedId)
        if count then for nodeId=0,count-1 do
          local nx,ny,nz=safeNodePosition(vehicle,nodeId)
          if nx then
            local wx,wy,wz=vx+nx,vy+ny,vz+nz; local dx,dy,dz=wx-hx,wy-hy,wz-hz
            if dx*dx+dy*dy+dz*dz<=radiusSquared then markers[#markers+1]={x=wx,y=wy,z=wz} end
            if #markers>=cap then break end
          end
        end end
        if #markers>=cap then break end
      end
    end
  end
end
local function colour(values) return ColorF(values[1],values[2],values[3],values[4]) end
local function draw()
  local orange=colour(cfg.freeHandColour); local yellow=colour(cfg.candidateNodeColour)
  for _,hand in ipairs({'left','right'}) do
    local state=hands[hand]
    if state.poseValid and state.handWorldPosition then debugDrawer:drawSphere(vec3(state.handWorldPosition),cfg.handSphereDiameter/2,orange) end
    if state.candidateSelected and state.candidateNodeWorldPosition then debugDrawer:drawSphere(vec3(state.candidateNodeWorldPosition),cfg.candidateNodeDiameter/2,yellow) end
    if previewEnabled then for _,position in ipairs(previewMarkers[hand]) do debugDrawer:drawSphere(vec3(position),cfg.candidateNodeDiameter/4,yellow) end end
  end
end

function M.setNearbyNodePreviewEnabled(enabled)
  previewEnabled=enabled==true; cfg.nearbyNodePreviewEnabled=previewEnabled
  if not previewEnabled then previewMarkers={left={},right={}} end
  return true
end
function M.getHandState(hand) return hands[hand] end
function M.getState() return {enabled=cfg.enabled,nearbyNodePreviewEnabled=previewEnabled,hands=hands} end
function M.onPreRender(dtReal)
  if not cfg or not cfg.enabled then return end
  validateCandidate('left'); validateCandidate('right')
  if previewEnabled then
    previewAccumulator=previewAccumulator+(finite(dtReal) and dtReal or 0)
    if previewAccumulator>=cfg.nearbyNodePreviewIntervalSeconds then previewAccumulator=0; refreshPreview() end
  end
  draw()
end
function M.onVehicleReset(vehicleId)
  for _,hand in ipairs({'left','right'}) do if hands[hand].candidateVehicleId==vehicleId then M.clearCandidate(hand,'selected vehicle reset') end end
end
function M.onVehicleDestroyed(vehicleId)
  for _,hand in ipairs({'left','right'}) do if hands[hand].candidateVehicleId==vehicleId then M.clearCandidate(hand,'selected vehicle despawned') end end
end
function M.onExtensionLoaded()
  cfg=jsonReadFile('settings/vrNodeGrabber.json') or jsonReadFile('/settings/vrNodeGrabber.json')
  if not cfg then log('E','vrNodeGrabber','configuration not found'); return false end
  previewEnabled=cfg.nearbyNodePreviewEnabled==true
  for _,hand in ipairs({'left','right'}) do
    hands[hand].poseValid=false; hands[hand].poseAgeMs=nil; hands[hand].handWorldPosition=nil
    hands[hand].nodesExamined=0; hands[hand].vehiclesExamined=0; clearFields(hands[hand],'extension loaded')
  end
  local provider=poseExtension()
  if not provider or type(provider.getControllerWorldPose)~='function' then log('E','vrNodeGrabber','beamngVRControllerPoses dependency unavailable'); return false end
  if provider.setLegacyControllerSpheresVisible then provider.setLegacyControllerSpheresVisible(cfg.legacyControllerSpheresVisible==true) end
  return true
end
function M.onExtensionUnloaded()
  M.clearCandidate('left','extension unloaded'); M.clearCandidate('right','extension unloaded')
  local provider=poseExtension()
  if provider and provider.setLegacyControllerSpheresVisible then provider.setLegacyControllerSpheresVisible(true) end
end
return M
