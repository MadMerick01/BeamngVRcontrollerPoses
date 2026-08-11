-- Passive, visual-only consumer of the final right-controller world pose.
-- The mesh is supplied by a separate unpacked visual mod and is not owned here.
local M = {dependencies={'beamngVRControllerPoses'}}

local settingsPath='settings/vrMockPistol.json'
local pistolShape='/art/shapes/vrpistol/vr_pistol.dae'
local cfg
local pistol
local creationAttempted=false
local state={
  enabled=false,visible=false,rightControllerPoseValid=false,
  rightControllerPoseAgeMs=nil,renderingBackend='TSStatic DAE scene object',
  creationStage='idle',failedOperation=nil,failedField=nil,lastError=nil
}

local function finite(v)
  return type(v)=='number' and v==v and v~=math.huge and v~=-math.huge
end

local function setHidden(hidden)
  if not pistol then return end
  pcall(function() pistol:setHidden(hidden) end)
  pcall(function() pistol:setField('hidden',0,hidden and '1' or '0') end)
end

local function destroyPistol()
  if pistol then
    setHidden(true)
    pcall(function() pistol:delete() end)
    pistol=nil
  end
  state.visible=false
end

local function fail(stage,operation,field,err)
  state.creationStage=stage
  state.failedOperation=operation
  state.failedField=field
  state.lastError=operation..(field and (' ['..field..']') or '')..' failed: '..tostring(err)
  log('E','vrMockPistol',state.lastError)
  return false
end

local function configureField(object,stage,field,value,required)
  state.creationStage=stage
  local ok,err=pcall(function() object:setField(field,0,value) end)
  if ok then return true end
  if required then return fail(stage,'setField',field,err) end
  return false
end

local function createPistol()
  if creationAttempted then return pistol~=nil end
  creationAttempted=true

  state.creationStage='createObject'
  local ok,object=pcall(function() return createObject('TSStatic') end)
  if not ok or not object then
    return fail('createObject','createObject',nil,object or 'returned nil')
  end

  state.creationStage='shape assignment'
  local shapeOk,shapeErr=pcall(function() object.shapeName=pistolShape end)
  if not shapeOk then
    pcall(function() object:delete() end)
    return fail('shape assignment','shapeName',nil,shapeErr)
  end

  state.creationStage='object registration'
  local registrationOk,registrationErr=pcall(function()
    object:registerObject('vrMockPistol')
  end)
  if not registrationOk then
    pcall(function() object:delete() end)
    return fail('object registration','registerObject',nil,registrationErr)
  end

  pistol=object
  if not configureField(object,'collision configuration','collisionType','None',true) then
    destroyPistol()
    return false
  end
  configureField(object,'collision configuration','decalType','None',false)
  configureField(object,'shadow configuration','castShadows','0',false)
  configureField(object,'object persistence','canSave','0',false)
  setHidden(true)
  state.creationStage='ready'
  state.failedOperation=nil
  state.failedField=nil
  state.lastError=nil
  return true
end

local function readSettings()
  local loaded=jsonReadFile(settingsPath) or jsonReadFile('/'..settingsPath)
  if not loaded then return nil,'configuration not found' end
  if loaded.hand~='right' then return nil,'hand must be right' end
  if not finite(loaded.maximumPoseAgeMs) or loaded.maximumPoseAgeMs<0 then
    return nil,'maximumPoseAgeMs is invalid'
  end
  return loaded
end

function M.reloadSettings()
  local loaded,err=readSettings()
  if not loaded then
    state.lastError=err
    setHidden(true)
    state.visible=false
    return false,err
  end
  local wasEnabled=state.enabled
  cfg=loaded
  state.enabled=wasEnabled or cfg.enabled==true
  destroyPistol()
  creationAttempted=false
  createPistol() -- A missing external DAE is attempted only once per reload.
  return true
end

function M.setEnabled(enabled)
  state.enabled=enabled==true
  if state.enabled then
    createPistol()
  else
    setHidden(true)
    state.visible=false
    state.rightControllerPoseValid=false
  end
  return true
end

function M.getState()
  return {
    enabled=state.enabled,visible=state.visible,
    rightControllerPoseValid=state.rightControllerPoseValid,
    rightControllerPoseAgeMs=state.rightControllerPoseAgeMs,
    renderingBackend=state.renderingBackend,creationStage=state.creationStage,
    failedOperation=state.failedOperation,failedField=state.failedField,
    lastError=state.lastError
  }
end

function M.onPreRender()
  if not cfg or not state.enabled then return end
  local provider=extensions and extensions.beamngVRControllerPoses or nil
  local pose=provider and provider.getControllerWorldPose and
    provider.getControllerWorldPose('right') or nil
  state.rightControllerPoseAgeMs=pose and pose.ageMs or nil
  local p,q=pose and pose.position,pose and pose.orientation
  state.rightControllerPoseValid=pose~=nil and pose.valid==true and finite(pose.ageMs) and
    pose.ageMs<=cfg.maximumPoseAgeMs and p and q and
    finite(p.x) and finite(p.y) and finite(p.z) and
    finite(q.x) and finite(q.y) and finite(q.z) and finite(q.w)
  if not state.rightControllerPoseValid or not pistol then
    setHidden(true)
    state.visible=false
    return
  end

  local transformOk,transformErr=pcall(function()
    pistol:setPosRot(p.x,p.y,p.z,q.x,q.y,q.z,q.w)
  end)
  if not transformOk then
    setHidden(true)
    state.visible=false
    fail('setPosRot','setPosRot',nil,transformErr)
    destroyPistol() -- Do not retry and emit the same engine error every frame.
    return
  end
  setHidden(false)
  state.visible=true
  state.creationStage='visible'
end

function M.onExtensionLoaded()
  local ok,err=M.reloadSettings()
  if not ok then log('E','vrMockPistol',err); return false end
  local provider=extensions and extensions.beamngVRControllerPoses or nil
  if not provider or type(provider.getControllerWorldPose)~='function' then
    state.lastError='beamngVRControllerPoses dependency unavailable'
    return false
  end
  return true
end

function M.onExtensionUnloaded()
  destroyPistol()
  creationAttempted=false
  state.enabled=false
  state.rightControllerPoseValid=false
end

return M
