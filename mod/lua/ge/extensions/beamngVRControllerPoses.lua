-- Stage 1 consumer. UDP receive is non-blocking; debug primitives are submitted every frame.
local M = {}
local sock, socketlib, cfg, latest, lastCounter, lastLog = nil, nil, nil, nil, -1, 0
local state = {leftControllerWorld={valid=false}, rightControllerWorld={valid=false}, diagnostics={}}
local hmdTranslationMode='beamngOnly'
local baseline, hmdSpaceKey, previousRawHmd, previousHmdSampleTime = nil, nil, nil, nil

-- All project quaternions use OpenXR/BeamNG API order (x, y, z, w).
local function qmul(a,b) return {
  a[4]*b[1]+a[1]*b[4]+a[2]*b[3]-a[3]*b[2],
  a[4]*b[2]-a[1]*b[3]+a[2]*b[4]+a[3]*b[1],
  a[4]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[4],
  a[4]*b[4]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3]}
end
local function qinv(q) return {-q[1],-q[2],-q[3],q[4]} end
local function qnorm(q)
  local n=math.sqrt(q[1]*q[1]+q[2]*q[2]+q[3]*q[3]+q[4]*q[4])
  if n==0 then return nil end
  return {q[1]/n,q[2]/n,q[3]/n,q[4]/n}
end
local function qrot(q,p) local r=qmul(qmul(q,{p[1],p[2],p[3],0}),qinv(q)); return {r[1],r[2],r[3]} end
local function compose(a,b)
  local p=qrot(a.q,b.p)
  return {p={a.p[1]+p[1],a.p[2]+p[2],a.p[3]+p[3]},q=qnorm(qmul(a.q,b.q))}
end
local function inverse(t)
  -- A pose inverse is not merely a quaternion conjugate: its negative position
  -- must be rotated into the inverse frame as well.
  local qi=qinv(t.q)
  return {p=qrot(qi,{-t.p[1],-t.p[2],-t.p[3]}),q=qi}
end
local function finiteNumber(v) return type(v)=='number' and v==v and v~=math.huge and v~=-math.huge end
local function validPose(p)
  if not p or not p.p or not p.q then return false end
  for i=1,3 do if not finiteNumber(p.p[i]) then return false end end
  for i=1,4 do if not finiteNumber(p.q[i]) then return false end end
  return qnorm(p.q)~=nil
end
local function copyPose(p) return p and {p={p.p[1],p.p[2],p.p[3]},q={p.q[1],p.q[2],p.q[3],p.q[4]}} or nil end
local function mappedPosition(p)
  local o,s=cfg.axisOrder,cfg.axisSign
  return {p[o[1]]*s[1]*cfg.metresToBeamNGUnit,p[o[2]]*s[2]*cfg.metresToBeamNGUnit,p[o[3]]*s[3]*cfg.metresToBeamNGUnit}
end
local function mappedControllerPose(p)
  -- Preserve the confirmed PR #25 controller-relative/calibration path unchanged.
  local b=qnorm(cfg.quaternionBasis)
  return {p=mappedPosition(p.p),q=qnorm(qmul(qmul(b,qnorm(p.q)),qinv(b)))}
end
local function mappedTrackingPose(p)
  -- (x,y,z)->(x,-z,y) is the +90-degree X basis rotation. Apply that one
  -- basis consistently to both components of the complete tracking-local pose.
  local root=math.sqrt(0.5); local b={root,0,0,root}
  return {p=mappedPosition(p.p),q=qnorm(qmul(qmul(b,qnorm(p.q)),qinv(b)))}
end
local function distance(a,b)
  local x,y,z=a[1]-b[1],a[2]-b[2],a[3]-b[3]
  return math.sqrt(x*x+y*y+z*z)
end
local function vaddScaled(p,d,s) return {p[1]+d[1]*s,p[2]+d[2]*s,p[3]+d[3]*s} end
local function tripod(p,q,length)
  return {x=vaddScaled(p,qrot(q,{1,0,0}),length),y=vaddScaled(p,qrot(q,{0,1,0}),length),z=vaddScaled(p,qrot(q,{0,0,1}),length)}
end
local function resetHmdBaseline(reason)
  baseline=nil; hmdSpaceKey=nil; previousRawHmd=nil; previousHmdSampleTime=nil
  state.baselineValid=false; state.baselineResetReason=reason
  state.baselineBeamngCameraWorld=nil; state.baselineTrackingHmdRaw=nil
  state.baselineTrackingHmdMapped=nil; state.baselineWorldFromTracking=nil
  state.trackingWorldRight=nil; state.trackingWorldForward=nil; state.trackingWorldUp=nil
  state.diagnostics.baselineResetReason=reason
end
local function vec3ToTable(v)
  if not v then return nil end
  if type(v)=='table' then return {v.x or v[1],v.y or v[2],v.z or v[3]} end
  return {v.x,v.y,v.z}
end
local function quatToXYZW(q)
  if not q then return nil end
  if type(q)=='table' then return {q.x or q[1],q.y or q[2],q.z or q[3],q.w or q[4]} end
  return {q.x,q.y,q.z,q.w}
end
local function beamCameraWorld()
  local pos=vec3ToTable(core_camera and core_camera.getPosition and core_camera.getPosition())
  local raw=quatToXYZW(core_camera and core_camera.getQuat and core_camera.getQuat())
  if not pos or not raw then return nil end
  local view=qnorm(raw); if not view then return nil end
  return {p=pos,q=qinv(view)}
end
local function predictedOpenXRTrackingLocalPose()
  local getter=OpenXR and OpenXR.getCameraPosRotPredictedXYZXYZW
  if type(getter)~='function' then return nil,nil,'predicted OpenXR tracking-local pose getter unavailable' end
  local ok,px,py,pz,qx,qy,qz,qw=pcall(getter); local raw={px,py,pz,qx,qy,qz,qw}
  if not ok then return nil,raw,'predicted OpenXR tracking-local pose getter failed' end
  for i=1,7 do if not finiteNumber(raw[i]) then return nil,raw,'predicted OpenXR tracking-local pose is malformed or non-finite' end end
  local q=qnorm({qx,qy,qz,qw}); if not q then return nil,raw,'predicted OpenXR tracking-local quaternion has zero length' end
  -- Diagnostic only. The native packet HMD is authoritative because it shares base/time with the hands.
  return {p={px,py,pz},q=q},raw,nil
end
local function storeBaseline(cameraWorld,hmd,mapped,key)
  local camera=copyPose(cameraWorld); local raw=copyPose(hmd); local mappedCopy=copyPose(mapped)
  -- Complete baseline operation: cameraWorld * inverse(mapped tracking-local HMD).
  local worldFromTracking=compose(camera,inverse(mappedCopy))
  baseline={camera=camera,raw=raw,mapped=mappedCopy,worldFromTracking=worldFromTracking}
  hmdSpaceKey=key
  state.baselineValid=true; state.baselineBeamngCameraWorld=copyPose(camera)
  state.baselineTrackingHmdRaw=copyPose(raw); state.baselineTrackingHmdMapped=copyPose(mappedCopy)
  state.baselineWorldFromTracking=copyPose(worldFromTracking)
  state.trackingWorldRight=qrot(worldFromTracking.q,{1,0,0})
  state.trackingWorldForward=qrot(worldFromTracking.q,{0,1,0})
  state.trackingWorldUp=qrot(worldFromTracking.q,{0,0,1})
end
local function updateRigidCandidate(cameraWorld,hmd)
  if not hmd or not hmd.valid or not validPose(hmd) then return state.baselineRigidCandidateHmdWorld end
  local raw={p={hmd.p[1],hmd.p[2],hmd.p[3]},q=qnorm(hmd.q)}
  local key=tostring(hmd.session or '')..':'..tostring(hmd.base or '')
  local sampleWentBack=previousHmdSampleTime and hmd.sampleTime and hmd.sampleTime<previousHmdSampleTime
  if hmdSpaceKey and hmdSpaceKey~=key then resetHmdBaseline('OpenXR session or base space changed')
  elseif sampleWentBack then resetHmdBaseline('OpenXR sample time reset')
  elseif previousRawHmd and distance(previousRawHmd,raw.p)>(cfg.hmdRecenterJumpMetres or 0.35) then resetHmdBaseline('VR recenter detected') end
  local mapped=mappedTrackingPose(raw)
  if not baseline then storeBaseline(cameraWorld,raw,mapped,key) end
  previousRawHmd={raw.p[1],raw.p[2],raw.p[3]}; previousHmdSampleTime=hmd.sampleTime
  state.currentTrackingHmdRaw=copyPose(raw); state.currentTrackingHmdMapped=copyPose(mapped)
  -- The requested full multiplication, with no independently rotated/added delta:
  -- compose(compose(baselineBeamngCameraWorld, inverse(baselineTrackingHmd)), currentTrackingHmd)
  local candidate=compose(baseline.worldFromTracking,mapped)
  state.baselineRigidCandidateHmdWorld=copyPose(candidate)
  return candidate
end
local function controllerPose(hmdWorld,raw,name)
  if not raw or not raw.valid or not validPose(raw) then return nil end
  local relative=mappedControllerPose({p=raw.p,q=raw.q})
  local offset={p=cfg[name..'PositionOffset'],q=qnorm(cfg[name..'RotationOffset'])}
  return compose(compose(hmdWorld,relative),offset),relative
end
local function publishSelectedHand(name,raw,selected,cameraWorld,candidate,now)
  local beamng=controllerPose(cameraWorld,raw,name)
  local rigid=candidate and controllerPose(candidate,raw,name) or nil
  state['beamngOnly'..(name=='left' and 'Left' or 'Right')..'ControllerWorld']=copyPose(beamng)
  state['baselineRigid'..(name=='left' and 'Left' or 'Right')..'ControllerWorld']=copyPose(rigid)
  local world=(selected=='baselineRigidTracking') and rigid or beamng
  local out=state[name..'ControllerWorld']; out.valid=false
  if world then out.position=world.p; out.orientation=world.q; out.valid=true; out.updateCounter=latest.counter; out.ageMs=(now-latest.received)*1000 end
end
local function receive()
  while true do
    local data=sock:receive(); if not data then break end
    local ok,p=pcall(jsonDecode,data)
    if ok and p and p.v==2 and type(p.counter)=='number' and p.counter>lastCounter then p.received=socketlib.gettime(); latest=p; lastCounter=p.counter end
  end
end
local function drawSphereStick(a,b,diameter,colour)
  local length=distance(a,b); if length==0 then return end
  local steps=math.max(1,math.ceil(length/math.max(diameter,0.001)))
  for i=0,steps do local t=i/steps; debugDrawer:drawSphere(vec3({a[1]+(b[1]-a[1])*t,a[2]+(b[2]-a[2])*t,a[3]+(b[3]-a[3])*t}),diameter/2,colour) end
end
local function drawTripod(centre,endpoints,settings)
  local colours={x=ColorF(1,0,0,1),y=ColorF(0,1,0,1),z=ColorF(0,0,1,1)}
  for _,axis in ipairs({'x','y','z'}) do drawSphereStick(centre,endpoints[axis],settings.lineThickness,colours[axis]); debugDrawer:drawSphere(vec3(endpoints[axis]),settings.endpointDiameter/2,colours[axis]) end
end
local function drawDiagnostics(cameraWorld,candidate,selectedHmd)
  local settings=cfg.axisTripods or {}; settings.axisLength=settings.axisLength or 0.25; settings.endpointDiameter=settings.endpointDiameter or 0.025; settings.lineThickness=settings.lineThickness or 0.01
  local tripodState={enabled=settings.enabled==true,diagnostic={},controllers={},originLines={}}
  if cfg.cameraTestSphere and cfg.cameraTestSphere.enabled then
    local offset={p=cfg.cameraTestSphere.offset or {0,1,0},q={0,0,0,1}}
    local red=compose(cameraWorld,offset); local purple=candidate and compose(candidate,offset) or nil
    state.diagnostics.diagnosticSphereWorldPositions={beamngOnly=red.p,baselineRigidTracking=purple and purple.p or nil}
    local radius=(cfg.cameraTestSphere.diameter or cfg.sphereDiameter)/2
    debugDrawer:drawSphere(vec3(red.p),radius,ColorF(1,0,0,1))
    if purple then debugDrawer:drawSphere(vec3(purple.p),radius,ColorF(0.65,0,1,1)) end
  end
  local colour=ColorF(cfg.sphereColour[1],cfg.sphereColour[2],cfg.sphereColour[3],cfg.sphereColour[4]); local radius=cfg.sphereDiameter/2
  for _,hand in ipairs({'left','right'}) do
    local p=state[hand..'ControllerWorld']
    if p.valid then
      debugDrawer:drawSphere(vec3(p.position),radius,colour)
      local endpoints=tripod(p.position,p.orientation,settings.axisLength); tripodState.controllers[hand]={centre=p.position,orientation=p.orientation,endpoints=endpoints}
      if settings.enabled==true and settings.drawControllerTripods==true then drawTripod(p.position,endpoints,settings) end
      if settings.enabled==true and settings.drawOriginLines==true then drawSphereStick(selectedHmd.p,p.position,settings.lineThickness,hand=='left' and ColorF(0,1,1,1) or ColorF(1,0,1,1)) end
    end
  end
  state.diagnostics.axisTripods=tripodState
end
function M.onExtensionLoaded()
  cfg=jsonReadFile('settings/beamngVRControllerPoses.json') or jsonReadFile('/settings/beamngVRControllerPoses.json')
  if not cfg then log('E','beamngVRControllerPoses','configuration not found'); return false end
  hmdTranslationMode='beamngOnly'; cfg.hmdTranslationMode='beamngOnly'; state.selectedHmdTranslationMode=hmdTranslationMode
  socketlib=require('socket'); sock=socketlib.udp(); sock:settimeout(0); assert(sock:setsockname(cfg.listenAddress,cfg.listenPort))
  resetHmdBaseline('extension loaded')
  log('I','beamngVRControllerPoses','listening for pose datagrams on '..cfg.listenAddress..':'..cfg.listenPort)
end
function M.onPreRender(dtReal,dtSim,dtRaw)
  if not sock then return end; receive(); local now=socketlib.gettime(); local cameraWorld=beamCameraWorld()
  if not latest or not cameraWorld or (now-latest.received)*1000>cfg.staleAfterMs then state.leftControllerWorld.valid=false; state.rightControllerWorld.valid=false; return end
  local candidate=updateRigidCandidate(cameraWorld,latest.hmd)
  local selectedHmd=(hmdTranslationMode=='baselineRigidTracking' and candidate) or cameraWorld
  publishSelectedHand('left',latest.left,hmdTranslationMode,cameraWorld,candidate,now)
  publishSelectedHand('right',latest.right,hmdTranslationMode,cameraWorld,candidate,now)
  local predicted,rawPredicted,predictedError=predictedOpenXRTrackingLocalPose()
  state.selectedHmdTranslationMode=hmdTranslationMode
  state.predictedOpenXRTrackingLocalPoseAvailable=predicted~=nil; state.predictedOpenXRTrackingLocalRawValues=rawPredicted
  state.predictedOpenXRTrackingLocalPosition=predicted and predicted.p or nil; state.predictedOpenXRTrackingLocalQuaternion=predicted and predicted.q or nil
  state.predictedOpenXRTrackingLocalError=predictedError
  state.diagnostics.selectedHmdTranslationMode=hmdTranslationMode; state.diagnostics.baselineValid=state.baselineValid
  state.diagnostics.baselineResetReason=state.baselineResetReason; state.diagnostics.baselineBeamngCameraWorld=state.baselineBeamngCameraWorld
  state.diagnostics.baselineTrackingHmdRaw=state.baselineTrackingHmdRaw; state.diagnostics.baselineTrackingHmdMapped=state.baselineTrackingHmdMapped
  state.diagnostics.baselineWorldFromTracking=state.baselineWorldFromTracking; state.diagnostics.currentTrackingHmdRaw=state.currentTrackingHmdRaw
  state.diagnostics.currentTrackingHmdMapped=state.currentTrackingHmdMapped; state.diagnostics.baselineRigidCandidateHmdWorld=state.baselineRigidCandidateHmdWorld
  state.diagnostics.baselineRigidLeftControllerWorld=state.baselineRigidLeftControllerWorld; state.diagnostics.baselineRigidRightControllerWorld=state.baselineRigidRightControllerWorld
  state.diagnostics.beamngOnlyLeftControllerWorld=state.beamngOnlyLeftControllerWorld; state.diagnostics.beamngOnlyRightControllerWorld=state.beamngOnlyRightControllerWorld
  state.diagnostics.trackingWorldRight=state.trackingWorldRight; state.diagnostics.trackingWorldForward=state.trackingWorldForward; state.diagnostics.trackingWorldUp=state.trackingWorldUp
  drawDiagnostics(cameraWorld,candidate,selectedHmd)
  if now-lastLog>cfg.logIntervalSeconds then log('I','beamngVRControllerPoses','baseline-rigid diagnostics='..dumps(state.diagnostics)); lastLog=now end
end
function M.setHmdTranslationMode(mode)
  if mode~='beamngOnly' and mode~='baselineRigidTracking' then log('E','beamngVRControllerPoses','invalid HMD translation mode: '..tostring(mode)); return false end
  hmdTranslationMode=mode; cfg.hmdTranslationMode=mode; state.selectedHmdTranslationMode=mode
  if mode=='baselineRigidTracking' then resetHmdBaseline('baselineRigidTracking mode selected') end
  return true
end
function M.setCameraSourceMode(mode) return mode=='beamngOnly' end
function M.resetHmdBaseline() resetHmdBaseline('explicit reset'); return true end
function M.setAxisTripodsEnabled(enabled) cfg.axisTripods.enabled=enabled==true; return true end
function M.setDiagnosticTripodsEnabled(enabled) cfg.axisTripods.drawDiagnosticSphereTripods=enabled==true; return true end
function M.setControllerTripodsEnabled(enabled) cfg.axisTripods.drawControllerTripods=enabled==true; return true end
function M.setOriginLinesEnabled(enabled) cfg.axisTripods.drawOriginLines=enabled==true; return true end
function M.getState() return state end
function M.onExtensionUnloaded() if sock then sock:close(); sock=nil end end
return M
