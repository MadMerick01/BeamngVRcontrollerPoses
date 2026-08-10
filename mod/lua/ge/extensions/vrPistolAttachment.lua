-- Text-only attachment for the pistol model mounted by VRPistol_Visual.
local M = {}

local tag = 'vrPistolAttachment'
local objectName = 'VRPistolAttachment_RightHand'
local pistolModelPath = '/art/shapes/vrpistol/vr_pistol.dae'
local maximumPoseAgeMs = 125

-- Controller-local grip correction. Tune these only after headset testing.
local gripPositionOffset = {x=0, y=0, z=0}
local gripRotationOffset = {x=0, y=0, z=0, w=1}

-- Provisional model-local muzzle calibration. Keep the grip calibration separate:
-- these values must be checked against the real model in BeamNG VR.
local muzzleLocalPositionOffset = {x=0, y=0.32, z=0.08}
local barrelLocalForwardAxis = {x=0, y=1, z=0}
local muzzleLocalRotationOffset = {x=0, y=0, z=0, w=1}
local debugRayEnabled = false
local debugRayLength = 5

local pistol = nil
local runtimeActive = true
local providerAvailable = nil
local trackingState = 'initial'
local creationFailureLogged = false
local firstValidPoseSeen = false
local externalRendererReference = nil
local externalRendererDisabled = false
local externalRendererDisableFailureLogged = false
local currentMuzzlePose = nil
local muzzleConfigurationFailureLogged = false
local transformFailureActive = false

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

local function normaliseQuaternion(q)
  if not q or not finite(q.x) or not finite(q.y) or not finite(q.z) or not finite(q.w) then return nil end
  local lengthSquared=q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w
  if not finite(lengthSquared) or lengthSquared<=1e-12 then return nil end
  local inverseLength=1/math.sqrt(lengthSquared)
  return {x=q.x*inverseLength,y=q.y*inverseLength,z=q.z*inverseLength,w=q.w*inverseLength}
end

local function normaliseVector(v)
  if not v or not finite(v.x) or not finite(v.y) or not finite(v.z) then return nil end
  local lengthSquared=v.x*v.x+v.y*v.y+v.z*v.z
  if not finite(lengthSquared) or lengthSquared<=1e-12 then return nil end
  local inverseLength=1/math.sqrt(lengthSquared)
  return {x=v.x*inverseLength,y=v.y*inverseLength,z=v.z*inverseLength}
end

local function composeGripTransform(position,orientation)
  local controllerOrientation=normaliseQuaternion(orientation)
  if not controllerOrientation then return nil,nil end
  local offset=rotateVector(controllerOrientation,gripPositionOffset)
  return {
    x=position.x+offset.x,y=position.y+offset.y,z=position.z+offset.z
  },normaliseQuaternion(multiplyQuaternion(controllerOrientation,gripRotationOffset))
end


-- Produces both outputs from one authoritative transform chain, preventing the
-- displayed pistol and its muzzle pose from acquiring independent transforms.
local function composePistolAndMuzzleTransform(position,orientation)
  if not finite(debugRayLength) or debugRayLength<=0 then return nil end
  local pistolPosition,pistolOrientation=composeGripTransform(position,orientation)
  if not pistolOrientation then return nil end
  local muzzleOrientation=normaliseQuaternion(multiplyQuaternion(pistolOrientation,muzzleLocalRotationOffset))
  local localDirection=normaliseVector(barrelLocalForwardAxis)
  if not muzzleOrientation or not localDirection then return nil end
  local worldOffset=rotateVector(pistolOrientation,muzzleLocalPositionOffset)
  local direction=normaliseVector(rotateVector(muzzleOrientation,localDirection))
  if not direction or not finite(worldOffset.x) or not finite(worldOffset.y) or not finite(worldOffset.z) then return nil end
  return {
    pistolPosition=pistolPosition,
    pistolOrientation=pistolOrientation,
    muzzlePosition={
      x=pistolPosition.x+worldOffset.x,
      y=pistolPosition.y+worldOffset.y,
      z=pistolPosition.z+worldOffset.z
    },
    muzzleDirection=direction,
    muzzleOrientation=muzzleOrientation
  }
end

local function invalidateMuzzlePose(ageMs,updateCounter)
  currentMuzzlePose={valid=false,ageMs=ageMs,updateCounter=updateCounter}
end

local function publishMuzzlePose(transform,pose)
  currentMuzzlePose={
    valid=true,
    position=transform.muzzlePosition,
    direction=transform.muzzleDirection,
    orientation=transform.muzzleOrientation,
    ageMs=pose.ageMs,
    updateCounter=pose.updateCounter
  }
end

local function classifyPose(pose)
  if not pose or pose.valid~=true then return nil,'invalid' end
  if not finite(pose.ageMs) or pose.ageMs<0 or pose.ageMs>maximumPoseAgeMs then return nil,'stale' end
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
    log('W',tag,'right-controller tracking lost; pistol hidden because the pose is stale')
  elseif nextState=='invalid' then
    log('W',tag,'right-controller tracking lost; pistol hidden')
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
    local orphan=scenetree and type(scenetree.findObject)=='function' and scenetree.findObject(objectName) or nil
    if orphan then orphan:delete() end
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
  invalidateMuzzlePose(nil,nil)
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
  log('I',tag,'muzzle debug ray is '..(debugRayEnabled and 'enabled' or 'disabled'))
end

function M.onPreRender()
  disableBundledRenderer()
  if not runtimeActive then invalidateMuzzlePose(nil,nil); setHidden(true); transitionTracking('runtime-inactive'); return end
  local poseProvider=provider()
  if not poseProvider then invalidateMuzzlePose(nil,nil); setHidden(true); transitionTracking('provider-unavailable'); return end
  local ok,rawPose=pcall(poseProvider.getControllerWorldPose,'right')
  local pose,state=classifyPose(ok and rawPose or nil)
  if not pose then
    invalidateMuzzlePose(rawPose and rawPose.ageMs or nil,rawPose and rawPose.updateCounter or nil)
    setHidden(true); transitionTracking(state); return
  end
  transitionTracking('valid')
  if not createPistol() then invalidateMuzzlePose(pose.ageMs,pose.updateCounter); return end
  local transform=composePistolAndMuzzleTransform(pose.position,pose.orientation)
  if not transform then
    invalidateMuzzlePose(pose.ageMs,pose.updateCounter)
    setHidden(true)
    if not muzzleConfigurationFailureLogged then
      muzzleConfigurationFailureLogged=true
      log('E',tag,'invalid muzzle transform configuration')
    end
    return
  end
  muzzleConfigurationFailureLogged=false
  publishMuzzlePose(transform,pose)
  local transformed,err=pcall(function()
    local position,orientation=transform.pistolPosition,transform.pistolOrientation
    pistol:setPosRot(position.x,position.y,position.z,orientation.x,orientation.y,orientation.z,orientation.w)
    setHidden(false)
    if debugRayEnabled then
      local startPoint=transform.muzzlePosition
      local direction=transform.muzzleDirection
      local endPoint={x=startPoint.x+direction.x*debugRayLength,
        y=startPoint.y+direction.y*debugRayLength,z=startPoint.z+direction.z*debugRayLength}
      debugDrawer:drawLine(vec3(startPoint),vec3(endPoint),ColorF(1,0.15,0.05,1))
    end
  end)
  if not transformed then
    invalidateMuzzlePose(pose.ageMs,pose.updateCounter)
    setHidden(true)
    if not transformFailureActive then
      transformFailureActive=true
      log('E',tag,'pistol transform update failed: '..tostring(err))
    end
  else
    transformFailureActive=false
  end
end

function M.getMuzzleWorldPose()
  local pose=currentMuzzlePose
  if not pose or pose.valid~=true then
    return {valid=false,position=nil,direction=nil,orientation=nil,
      ageMs=pose and pose.ageMs or nil,updateCounter=pose and pose.updateCounter or nil}
  end
  return {
    valid=true,
    position={x=pose.position.x,y=pose.position.y,z=pose.position.z},
    direction={x=pose.direction.x,y=pose.direction.y,z=pose.direction.z},
    orientation={x=pose.orientation.x,y=pose.orientation.y,z=pose.orientation.z,w=pose.orientation.w},
    ageMs=pose.ageMs,
    updateCounter=pose.updateCounter
  }
end

function M.onClientPostStartMission() runtimeActive=true end
function M.onClientEndMission() runtimeActive=false; invalidateMuzzlePose(nil,nil); setHidden(true); transitionTracking('runtime-inactive') end
function M.onProviderUnloaded() providerAvailable=false; invalidateMuzzlePose(nil,nil); setHidden(true); transitionTracking('provider-unavailable') end
function M.onExtensionUnloaded() cleanup('extension unloaded') end

return M
