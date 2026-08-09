-- Text-only attachment for the pistol model mounted by VRPistol_Visual.
local M = {}

local tag = 'vrPistolAttachment'
local objectName = 'VRPistolAttachment_RightHand'
local pistolModelPath = '/art/shapes/vrpistol/vr_pistol.dae'
local maximumPoseAgeMs = 125

-- Controller-local grip correction. Tune these only after headset testing.
local gripPositionOffset = {x=0, y=0, z=0}
local gripRotationOffset = {x=0, y=0, z=0, w=1}

local pistol = nil
local runtimeActive = true
local providerAvailable = nil
local trackingState = 'initial'
local creationFailureLogged = false
local firstValidPoseSeen = false
local externalRendererReference = nil
local externalRendererDisabled = false
local externalRendererDisableFailureLogged = false

local function finite(value)
  return type(value)=='number' and value==value and value~=math.huge and value~=-math.huge
end

local function multiplyQuaternion(a,b)
  return {
    x=a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
    y=a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
    z=a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,
    w=a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z
  }
end

local function rotateVector(q,v)
  local vectorQuaternion={x=v.x,y=v.y,z=v.z,w=0}
  local conjugate={x=-q.x,y=-q.y,z=-q.z,w=q.w}
  local rotated=multiplyQuaternion(multiplyQuaternion(q,vectorQuaternion),conjugate)
  return {x=rotated.x,y=rotated.y,z=rotated.z}
end

local function composeGripTransform(position,orientation)
  local offset=rotateVector(orientation,gripPositionOffset)
  return {
    x=position.x+offset.x,y=position.y+offset.y,z=position.z+offset.z
  },multiplyQuaternion(orientation,gripRotationOffset)
end

local function classifyPose(pose)
  if not pose or pose.valid~=true then return nil,'invalid' end
  if not finite(pose.ageMs) or pose.ageMs>maximumPoseAgeMs then return nil,'stale' end
  local p,q=pose.position,pose.orientation
  if not p or not q or not finite(p.x) or not finite(p.y) or not finite(p.z) or
      not finite(q.x) or not finite(q.y) or not finite(q.z) or not finite(q.w) then
    return nil,'invalid'
  end
  if q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w==0 then return nil,'invalid' end
  return pose,'valid'
end

local function setHidden(hidden)
  if not pistol then return end
  pcall(function() pistol:setHidden(hidden) end)
  pcall(function() pistol:setField('hidden',0,hidden and '1' or '0') end)
end

local function transitionTracking(nextState)
  if trackingState==nextState then return end
  local previous=trackingState
  trackingState=nextState
  if nextState=='valid' then
    if not firstValidPoseSeen then
      firstValidPoseSeen=true
      log('I',tag,'first valid right-controller pose received')
    elseif previous~='valid' then
      log('I',tag,'right-controller tracking recovered')
    end
  elseif nextState=='stale' then
    log('W',tag,'pistol hidden because the right-controller pose is stale')
  elseif nextState=='invalid' then
    log('W',tag,'pistol hidden because right-controller tracking is invalid')
  elseif nextState=='provider-unavailable' then
    log('W',tag,'pistol hidden because the controller-pose provider is unavailable')
  elseif nextState=='runtime-inactive' then
    log('I',tag,'pistol hidden because the mission runtime is inactive')
  end
end

local function modelExists()
  return FS and type(FS.fileExists)=='function' and FS:fileExists(pistolModelPath)
end

local function createPistol()
  if pistol then return true end
  if not modelExists() then
    if not creationFailureLogged then
      creationFailureLogged=true
      log('E',tag,'external pistol model is unavailable: '..pistolModelPath)
    end
    return false
  end
  local candidate=nil
  local ok,obj=pcall(function()
    candidate=createObject('TSStatic')
    if not candidate then error('createObject returned nil') end
    candidate.shapeName=pistolModelPath
    candidate:setField('collisionType',0,'None')
    candidate:setField('decalType',0,'None')
    candidate:setField('castShadows',0,'0')
    candidate:setField('canSave',0,'0')
    candidate:registerObject(objectName)
    return candidate
  end)
  if not ok or not obj then
    if candidate then pcall(function() candidate:delete() end) end
    if not creationFailureLogged then
      creationFailureLogged=true
      log('E',tag,'pistol TSStatic creation failed: '..tostring(obj))
    end
    return false
  end
  pistol=obj
  creationFailureLogged=false
  setHidden(true)
  log('I',tag,'pistol TSStatic created successfully')
  return true
end

local function disableBundledRenderer()
  local current=extensions and extensions.vrPistolVisual or nil
  if not current then
    externalRendererReference=nil
    externalRendererDisabled=false
    externalRendererDisableFailureLogged=false
    return
  end
  if current~=externalRendererReference then
    externalRendererReference=current
    externalRendererDisabled=false
    externalRendererDisableFailureLogged=false
  end
  if externalRendererDisabled and type(current.isEnabled)=='function' then
    local checked,enabled=pcall(current.isEnabled)
    if checked and enabled==false then return end
    externalRendererDisabled=false
  elseif externalRendererDisabled then
    return
  end
  if type(current.setEnabled)=='function' then
    local ok,result=pcall(current.setEnabled,false)
    if ok and result==false then
      externalRendererDisabled=true
      externalRendererDisableFailureLogged=false
      log('I',tag,'disabled the visual mod bundled renderer to prevent a duplicate TSStatic')
    elseif not externalRendererDisableFailureLogged then
      externalRendererDisableFailureLogged=true
      log('W',tag,'could not disable the visual mod bundled renderer')
    end
  end
end

local function cleanup(reason)
  if pistol then
    setHidden(true)
    pcall(function() pistol:delete() end)
    pistol=nil
  end
  log('I',tag,'attachment cleanup: '..reason)
end

local function provider()
  local candidate=extensions and extensions.beamngVRControllerPoses or nil
  if not candidate or type(candidate.getControllerWorldPose)~='function' then
    if providerAvailable~=false then
      providerAvailable=false
      log('W',tag,'controller-pose provider unavailable')
    end
    return nil
  end
  if providerAvailable~=true then
    providerAvailable=true
    log('I',tag,'controller-pose provider found')
  end
  return candidate
end

function M.onExtensionLoaded()
  log('I',tag,'attachment extension loaded')
  log('I',tag,'resolved external pistol model path: '..pistolModelPath)
end

function M.onPreRender()
  disableBundledRenderer()
  if not runtimeActive then setHidden(true); transitionTracking('runtime-inactive'); return end
  local poseProvider=provider()
  if not poseProvider then setHidden(true); transitionTracking('provider-unavailable'); return end
  local ok,rawPose=pcall(poseProvider.getControllerWorldPose,'right')
  local pose,state=classifyPose(ok and rawPose or nil)
  if not pose then setHidden(true); transitionTracking(state); return end
  transitionTracking('valid')
  if not createPistol() then return end
  local position,orientation=composeGripTransform(pose.position,pose.orientation)
  local transformed,err=pcall(function()
    pistol:setPosRot(position.x,position.y,position.z,
      orientation.x,orientation.y,orientation.z,orientation.w)
    setHidden(false)
  end)
  if not transformed then
    setHidden(true)
    log('E',tag,'pistol transform update failed: '..tostring(err))
  end
end

function M.onClientPostStartMission() runtimeActive=true end
function M.onClientEndMission() runtimeActive=false; setHidden(true); transitionTracking('runtime-inactive') end
function M.onProviderUnloaded() providerAvailable=false; setHidden(true); transitionTracking('provider-unavailable') end
function M.onExtensionUnloaded() cleanup('extension unloaded') end

return M
