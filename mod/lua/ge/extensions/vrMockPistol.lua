-- Visual-only two-prism alignment aid. It owns no physics or gameplay state.
-- BeamNG 0.39's supplied API dump exposes debugDrawer only as userdata and
-- documents no stereoscopic oriented-box call, so two reusable TSStatics are
-- used instead of relying on an unverified debug-render method.
local M = {dependencies={'beamngVRControllerPoses'}}

local settingsPath='settings/vrMockPistol.json'
local cubeShape='art/shapes/blocks/cube_1m.dae'
local cfg
local pieces={barrel=nil,handle=nil}
local state={
  enabled=false,visible=false,rightControllerPoseValid=false,
  rightControllerPoseAgeMs=nil,renderingBackend='TSStatic cube scene objects',
  barrelLocalPose=nil,handleLocalPose=nil,barrelWorldPose=nil,handleWorldPose=nil,
  creationStage='idle',failedPiece=nil,failedOperation=nil,failedField=nil,
  optionalFieldWarnings={},lastError=nil
}

local function finite(v) return type(v)=='number' and v==v and v~=math.huge and v~=-math.huge end
local function copy3(v) return v and {v[1],v[2],v[3]} or nil end
local function copy4(v) return v and {v[1],v[2],v[3],v[4]} or nil end
local function copyPose(v) return v and {position=copy3(v.position),orientation=copy4(v.orientation)} or nil end
local function qmul(a,b) return {
  a[4]*b[1]+a[1]*b[4]+a[2]*b[3]-a[3]*b[2],
  a[4]*b[2]-a[1]*b[3]+a[2]*b[4]+a[3]*b[1],
  a[4]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[4],
  a[4]*b[4]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3]}
end
local function qinv(q) return {-q[1],-q[2],-q[3],q[4]} end
local function qrot(q,p)
  local r=qmul(qmul(q,{p[1],p[2],p[3],0}),qinv(q))
  return {r[1],r[2],r[3]}
end
local function compose(parent,localPose)
  local offset=qrot(parent.orientation,localPose.position)
  return {position={parent.position[1]+offset[1],parent.position[2]+offset[2],parent.position[3]+offset[3]},
    orientation=qmul(parent.orientation,localPose.orientation)}
end
local function validArray(v,n)
  if type(v)~='table' then return false end
  for i=1,n do if not finite(v[i]) then return false end end
  return true
end
local function localPose(piece)
  return {position=copy3(piece.position),orientation=copy4(piece.orientation)}
end
local function setHidden(object,hidden)
  if not object then return end
  pcall(function() object:setHidden(hidden) end)
  pcall(function() object:setField('hidden',0,hidden and '1' or '0') end)
end
local function deletePiece(name)
  local object=pieces[name]
  if not object then return end
  setHidden(object,true)
  pcall(function() object:delete() end)
  pieces[name]=nil
end
local function destroyPieces()
  deletePiece('barrel'); deletePiece('handle'); state.visible=false
end
local function hidePieces()
  setHidden(pieces.barrel,true); setHidden(pieces.handle,true); state.visible=false
end
local function clearFailure(stage)
  state.creationStage=stage or 'idle'; state.failedPiece=nil
  state.failedOperation=nil; state.failedField=nil; state.lastError=nil
end
local function fail(piece,stage,operation,field,err)
  state.creationStage=stage; state.failedPiece=piece
  state.failedOperation=operation; state.failedField=field
  state.lastError=piece..' '..operation..(field and (' ['..field..']') or '')..' failed: '..tostring(err)
  log('E','vrMockPistol',state.lastError)
  return nil,state.lastError
end
local function discard(object)
  if not object then return end
  setHidden(object,true); pcall(function() object:delete() end)
end
local function configureField(object,pieceName,stage,field,value,required)
  state.creationStage=stage
  local ok,err=pcall(function() object:setField(field,0,value) end)
  if ok then return true end
  local message=pieceName..' optional field '..field..' failed: '..tostring(err)
  if required then return fail(pieceName,stage,'setField',field,err) end
  state.optionalFieldWarnings[#state.optionalFieldWarnings+1]=message
  log('W','vrMockPistol',message)
  return false
end
local function makePiece(name,dimensions)
  state.creationStage='createObject'
  local ok,object=pcall(function() return createObject('TSStatic') end)
  if not ok or not object then return fail(name,'createObject','createObject',nil,object or 'returned nil') end

  state.creationStage='shape assignment'
  local shapeOk,shapeErr=pcall(function() object.shapeName=cubeShape end)
  if not shapeOk then discard(object); return fail(name,'shape assignment','shapeName',nil,shapeErr) end

  -- Register first: Torque dynamic fields are reliably addressable only after
  -- the SimObject exists in the scene tree.
  state.creationStage='object registration'
  local registrationOk,registrationErr=pcall(function() object:registerObject('vrMockPistol_'..name) end)
  if not registrationOk then discard(object); return fail(name,'object registration','registerObject',nil,registrationErr) end

  -- collisionType is a Torque enum field. BeamNG 0.39's dump exposes TSStatic's
  -- binding but not enum constants, so use its string-addressable field parser;
  -- never send "None" through the native __newindex binding.
  if not configureField(object,name,'collision configuration','collisionType','None',true) then
    discard(object); return nil,state.lastError
  end
  configureField(object,name,'collision configuration','decalType','None',false)
  configureField(object,name,'shadow configuration','castShadows','0',false)
  configureField(object,name,'object persistence','canSave','0',false)
  -- The stock unit cube accepts per-instance colour; no installed material is changed.
  local c=cfg.colour
  configureField(object,name,'colour configuration','instanceColor',
    string.format('%g %g %g %g',c[1],c[2],c[3],c[4]),false)

  -- Settings are documented as forward length/depth, width, height. BeamNG's
  -- confirmed controller-forward axis is local +Y, so the cube scale is X/Y/Z.
  state.creationStage='initial scaling'
  local scaleOk,scaleErr=pcall(function() object:setScale(vec3({dimensions[2],dimensions[1],dimensions[3]})) end)
  if not scaleOk then discard(object); return fail(name,'initial scaling','setScale',nil,scaleErr) end
  setHidden(object,true)
  state.creationStage='ready'
  return object
end
local function ensurePieces()
  if pieces.barrel and pieces.handle then return true end
  destroyPieces()
  local object,err=makePiece('barrel',cfg.barrel.dimensions)
  if not object then state.lastError=err; return false end
  pieces.barrel=object
  object,err=makePiece('handle',cfg.handle.dimensions)
  if not object then state.lastError=err; destroyPieces(); return false end
  pieces.handle=object
  return true
end
local function transformPiece(name,object,pose)
  local p,q=pose.position,pose.orientation
  state.creationStage='setPosRot'
  local positionOk,positionErr=pcall(function()
    object:setPosRot(p[1],p[2],p[3],q[1],q[2],q[3],q[4])
  end)
  if not positionOk then return fail(name,'setPosRot','setPosRot',nil,positionErr) end
  state.creationStage='visibility'
  local visibilityOk,visibilityErr=pcall(function() object:setHidden(false) end)
  if not visibilityOk then
    visibilityOk,visibilityErr=pcall(function() object:setField('hidden',0,'0') end)
  end
  if not visibilityOk then return fail(name,'visibility','setHidden','hidden',visibilityErr) end
  return true
end
local function readSettings()
  local loaded=jsonReadFile(settingsPath) or jsonReadFile('/'..settingsPath)
  if not loaded then return nil,'configuration not found' end
  if loaded.hand~='right' then return nil,'hand must be right' end
  if not finite(loaded.maximumPoseAgeMs) or loaded.maximumPoseAgeMs<0 then return nil,'maximumPoseAgeMs is invalid' end
  if not validArray(loaded.colour,4) then return nil,'colour is invalid' end
  for _,name in ipairs({'barrel','handle'}) do
    local p=loaded[name]
    if type(p)~='table' or not validArray(p.position,3) or not validArray(p.orientation,4) or not validArray(p.dimensions,3) then
      return nil,name..' configuration is invalid'
    end
    for i=1,3 do if p.dimensions[i]<=0 then return nil,name..' dimensions must be positive' end end
  end
  return loaded
end

function M.reloadSettings()
  local loaded,err=readSettings()
  if not loaded then state.lastError=err; hidePieces(); return false,err end
  local wasEnabled=state.enabled
  destroyPieces()
  cfg=loaded; state.enabled=wasEnabled and true or cfg.enabled==true
  state.barrelLocalPose=localPose(cfg.barrel); state.handleLocalPose=localPose(cfg.handle)
  state.optionalFieldWarnings={}; clearFailure('settings loaded')
  return true
end
function M.setEnabled(enabled)
  state.enabled=enabled==true
  if not state.enabled then
    destroyPieces(); state.rightControllerPoseValid=false
    state.barrelWorldPose=nil; state.handleWorldPose=nil
  end
  return true
end
function M.getState()
  return {enabled=state.enabled,visible=state.visible,
    rightControllerPoseValid=state.rightControllerPoseValid,rightControllerPoseAgeMs=state.rightControllerPoseAgeMs,
    renderingBackend=state.renderingBackend,barrelLocalPose=copyPose(state.barrelLocalPose),
    handleLocalPose=copyPose(state.handleLocalPose),barrelWorldPose=copyPose(state.barrelWorldPose),
    handleWorldPose=copyPose(state.handleWorldPose),creationStage=state.creationStage,
    failedPiece=state.failedPiece,failedOperation=state.failedOperation,failedField=state.failedField,
    optionalFieldWarnings=state.optionalFieldWarnings,lastError=state.lastError}
end
function M.onPreRender()
  if not cfg or not state.enabled then return end
  local provider=extensions and extensions.beamngVRControllerPoses or nil
  local pose=provider and provider.getControllerWorldPose and provider.getControllerWorldPose('right') or nil
  state.rightControllerPoseAgeMs=pose and pose.ageMs or nil
  local p,q=pose and pose.position,pose and pose.orientation
  state.rightControllerPoseValid=pose~=nil and pose.valid==true and finite(pose.ageMs) and
    pose.ageMs<=cfg.maximumPoseAgeMs and p and q and finite(p.x) and finite(p.y) and finite(p.z) and
    finite(q.x) and finite(q.y) and finite(q.z) and finite(q.w)
  if not state.rightControllerPoseValid then
    state.barrelWorldPose=nil; state.handleWorldPose=nil; hidePieces(); return
  end
  if not ensurePieces() then hidePieces(); return end
  local parent={position={p.x,p.y,p.z},orientation={q.x,q.y,q.z,q.w}}
  local barrelWorld=compose(parent,state.barrelLocalPose)
  local handleWorld=compose(parent,state.handleLocalPose)
  if not transformPiece('barrel',pieces.barrel,barrelWorld) then hidePieces(); return end
  if not transformPiece('handle',pieces.handle,handleWorld) then hidePieces(); return end
  state.barrelWorldPose=barrelWorld; state.handleWorldPose=handleWorld
  state.visible=true; clearFailure('visible')
end
function M.onExtensionLoaded()
  local ok,err=M.reloadSettings()
  if not ok then log('E','vrMockPistol',err); return false end
  local provider=extensions and extensions.beamngVRControllerPoses or nil
  if not provider or type(provider.getControllerWorldPose)~='function' then
    state.lastError='beamngVRControllerPoses dependency unavailable'; return false
  end
  return true
end
function M.onExtensionUnloaded()
  destroyPieces(); state.enabled=false; state.rightControllerPoseValid=false
  state.barrelWorldPose=nil; state.handleWorldPose=nil
end

return M
