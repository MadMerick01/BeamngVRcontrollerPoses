-- Stage 1 consumer. UDP receive is non-blocking; debug primitives are submitted every frame.
local M = {}
local sock, socketlib, cfg, latest, lastCounter, lastLog = nil, nil, nil, nil, -1, 0
local state = {leftControllerWorld={valid=false}, rightControllerWorld={valid=false}, diagnostics={}}
local hmdBaseline, hmdSpaceKey, previousRawHmd, previousHmdSampleTime = nil, nil, nil, nil
local worldFromBaseQ=nil
local headingBaseline, alignedHeading = nil, nil

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
local function vaddScaled(p,d,s) return {p[1]+d[1]*s,p[2]+d[2]*s,p[3]+d[3]*s} end
local function tripod(p,q,length)
  return {x=vaddScaled(p,qrot(q,{1,0,0}),length),y=vaddScaled(p,qrot(q,{0,1,0}),length),z=vaddScaled(p,qrot(q,{0,0,1}),length)}
end
local function compose(a,b) local p=qrot(a.q,b.p); return {p={a.p[1]+p[1],a.p[2]+p[2],a.p[3]+p[3]},q=qmul(a.q,b.q)} end
local function mappedPosition(p)
  local o,s=cfg.axisOrder,cfg.axisSign
  return {p[o[1]]*s[1]*cfg.metresToBeamNGUnit,p[o[2]]*s[2]*cfg.metresToBeamNGUnit,p[o[3]]*s[3]*cfg.metresToBeamNGUnit}
end
local function mappedPose(p)
  local v=mappedPosition(p.p)
  local b=cfg.quaternionBasis
  return {p=v,q=qmul(qmul(b,p.q),qinv(b))}
end
local function distance(a,b)
  local x,y,z=a[1]-b[1],a[2]-b[2],a[3]-b[3]
  return math.sqrt(x*x+y*y+z*z)
end
local function wrapDegrees180(value)
  return (value+180)%360-180
end
local function headingFromForward(forward)
  local atan2=math.atan2 or function(y,x) return math.atan(y,x) end
  local heading=math.deg(atan2(forward[1],forward[2]))
  return (heading%360+360)%360
end
local function beamngHeading(q)
  return headingFromForward(qrot(q,{0,1,0}))
end
local function openXrHeading(q)
  local forward=qrot(q,{0,0,-1})
  -- OpenXR is x-right/y-up/-z-forward. Express clockwise yaw from -Z.
  local atan2=math.atan2 or function(y,x) return math.atan(y,x) end
  local heading=math.deg(atan2(forward[1],-forward[3]))
  return (heading%360+360)%360
end
local function resetHmdBaseline(reason)
  hmdBaseline=nil; hmdSpaceKey=nil; previousRawHmd=nil; previousHmdSampleTime=nil; worldFromBaseQ=nil
  headingBaseline=nil; alignedHeading=nil
  state.diagnostics.hmdBaselineResetReason=reason
  state.diagnostics.baselineResetReason=reason
end
local function vec3ToTable(v)
  if not v then return nil end
  if type(v)=='table' then return {v.x or v[1], v.y or v[2], v.z or v[3]} end
  return {v.x, v.y, v.z}
end
local function quatToXYZW(q)
  if not q then return nil end
  -- BeamNG's core_camera.getQuat()/getQuatXYZW convention is explicitly XYZW; keep project-internal XYZW.
  if type(q)=='table' then return {q.x or q[1], q.y or q[2], q.z or q[3], q.w or q[4]} end
  return {q.x, q.y, q.z, q.w}
end
local function beamCameraWorld()
  local pos=vec3ToTable(core_camera and core_camera.getPosition and core_camera.getPosition())
  local rawRot=quatToXYZW(core_camera and core_camera.getQuat and core_camera.getQuat())
  if not pos or not rawRot or not rawRot[4] then return nil end
  -- core_camera.getQuat() is the world-to-camera/view rotation.  The rigid-transform
  -- helpers consume camera-to-world rotations, so normalize and invert once at
  -- the BeamNG API boundary.  Controller-relative rotations are not inverted.
  local cameraToWorld=qnorm(rawRot)
  if not cameraToWorld then return nil end
  return {p=pos,q=qinv(cameraToWorld)}
end
local function updateHand(name, raw, cameraWorld, now)
  local out=state[name..'ControllerWorld']; out.valid=false
  if not raw or not raw.valid then return end
  -- Protocol 2 is already inverse(hmdInBase) * controllerInBase, sampled at one XrTime/baseSpace.
  local rel=mappedPose(raw)
  local offset={p=cfg[name..'PositionOffset'],q=cfg[name..'RotationOffset']}
  local world=compose(cameraWorld,compose(rel,offset))
  out.position=world.p; out.orientation=world.q; out.valid=true; out.updateCounter=latest.counter; out.ageMs=(now-latest.received)*1000
  out.relative=rel; out.rawRelative=raw
end
local validHmdTranslationModes={beamngOnly=true,beamngPlusHmdDelta=true,beamngMinusHmdDelta=true,beamngFixedBaseHmdDelta=true}
local function hmdCandidates(cameraAnchor, worldDelta, fixedDelta)
  -- Keep these as direct calculations from the BeamNG camera.  The test
  -- alternatives must never accumulate or derive from one another.
  return {
    beamngOnly={p={cameraAnchor.p[1],cameraAnchor.p[2],cameraAnchor.p[3]},q=cameraAnchor.q},
    beamngPlusHmdDelta={p={cameraAnchor.p[1]+worldDelta[1],cameraAnchor.p[2]+worldDelta[2],cameraAnchor.p[3]+worldDelta[3]},q=cameraAnchor.q},
    beamngMinusHmdDelta={p={cameraAnchor.p[1]-worldDelta[1],cameraAnchor.p[2]-worldDelta[2],cameraAnchor.p[3]-worldDelta[3]},q=cameraAnchor.q},
    beamngFixedBaseHmdDelta={p={cameraAnchor.p[1]+fixedDelta[1],cameraAnchor.p[2]+fixedDelta[2],cameraAnchor.p[3]+fixedDelta[3]},q=cameraAnchor.q}
  }
end
local function actualHmdWorld(cameraAnchor, hmd)
  local rawDelta,mappedDelta,worldDelta,fixedDelta=nil,nil,{0,0,0},{0,0,0}
  local rawQ,mappedQ=nil,nil
  local valid=hmd and hmd.valid and hmd.p and hmd.q
  if valid then
  local key=tostring(hmd.session or '')..':'..tostring(hmd.base or '')
  local jump=cfg.hmdRecenterJumpMetres or 0.35
  local sampleWentBack=previousHmdSampleTime and hmd.sampleTime and hmd.sampleTime<previousHmdSampleTime
  if hmdSpaceKey and hmdSpaceKey~=key then resetHmdBaseline('tracking space changed')
  elseif sampleWentBack then resetHmdBaseline('sample time reset')
  elseif previousRawHmd and distance(previousRawHmd,hmd.p)>jump then resetHmdBaseline('HMD pose discontinuity') end
  rawQ=qnorm(hmd.q)
  local root=math.sqrt(0.5)
  local basis={root,0,0,root}
  mappedQ=qnorm(qmul(qmul(basis,rawQ),qinv(basis)))
  if not hmdBaseline then
    worldFromBaseQ=qnorm(qmul(qnorm(cameraAnchor.q),qinv(mappedQ)))
    hmdBaseline={
      p={hmd.p[1],hmd.p[2],hmd.p[3]},q=rawQ,mappedQ=mappedQ,
      cameraQ=qnorm(cameraAnchor.q),worldFromBaseQ=worldFromBaseQ,
      session=hmd.session,base=hmd.base,sampleTime=hmd.sampleTime
    }
    hmdSpaceKey=key
  end
  previousRawHmd={hmd.p[1],hmd.p[2],hmd.p[3]}; previousHmdSampleTime=hmd.sampleTime

  -- The baseline removes standing height/tracking-origin placement.  Apply the
  -- confirmed OpenXR -> BeamNG mapping (x,y,z) -> (x,-z,y), then rotate the
  -- room-scale displacement by the corrected camera-to-world quaternion.
  rawDelta={hmd.p[1]-hmdBaseline.p[1],hmd.p[2]-hmdBaseline.p[2],hmd.p[3]-hmdBaseline.p[3]}
  mappedDelta=mappedPosition(rawDelta)
  worldDelta=qrot(cameraAnchor.q,mappedDelta)
  fixedDelta=qrot(worldFromBaseQ,mappedDelta)
  end
  local candidates=hmdCandidates(cameraAnchor,worldDelta,fixedDelta)
  local mode=cfg.hmdTranslationMode
  if not validHmdTranslationModes[mode] then mode='beamngOnly'; cfg.hmdTranslationMode=mode end
  local selected=candidates[mode]
  state.diagnostics.beamngCameraPosition={cameraAnchor.p[1],cameraAnchor.p[2],cameraAnchor.p[3]}
  state.diagnostics.rawOpenXrHmdPosition=valid and {hmd.p[1],hmd.p[2],hmd.p[3]} or nil
  state.diagnostics.rawHmdPose=hmd
  state.diagnostics.hmdBaseline=hmdBaseline and {hmdBaseline.p[1],hmdBaseline.p[2],hmdBaseline.p[3]} or nil
  state.diagnostics.rawHmdDelta=rawDelta
  state.diagnostics.mappedHmdDelta=mappedDelta
  state.diagnostics.rotatedWorldHmdDelta=valid and worldDelta or nil
  state.diagnostics.rawBaseFromHmdPosition=valid and {hmd.p[1],hmd.p[2],hmd.p[3]} or nil
  state.diagnostics.rawBaseFromHmdOrientation=rawQ
  state.diagnostics.mappedBaseFromHmdOrientation=mappedQ
  state.diagnostics.baselineBaseFromHmdPosition=hmdBaseline and hmdBaseline.p or nil
  state.diagnostics.baselineBaseFromHmdOrientation=hmdBaseline and hmdBaseline.q or nil
  state.diagnostics.baselineMappedHmdOrientation=hmdBaseline and hmdBaseline.mappedQ or nil
  state.diagnostics.baselineBeamngCameraOrientation=hmdBaseline and hmdBaseline.cameraQ or nil
  state.diagnostics.fixedWorldFromBaseOrientation=worldFromBaseQ
  state.diagnostics.rawBaseDelta=rawDelta
  state.diagnostics.mappedBaseDelta=mappedDelta
  state.diagnostics.liveCameraWorldDelta=valid and worldDelta or nil
  state.diagnostics.fixedBaseWorldDelta=valid and fixedDelta or nil
  state.diagnostics.fixedBaseWorldRight=worldFromBaseQ and qrot(worldFromBaseQ,{1,0,0}) or nil
  state.diagnostics.fixedBaseWorldForward=worldFromBaseQ and qrot(worldFromBaseQ,{0,1,0}) or nil
  state.diagnostics.fixedBaseWorldUp=worldFromBaseQ and qrot(worldFromBaseQ,{0,0,1}) or nil
  state.diagnostics.beamngCameraAnchor=cameraAnchor
  local beamHeading=beamngHeading(cameraAnchor.q)
  local xrHeading=valid and hmd.q and openXrHeading(hmd.q) or nil
  if xrHeading and not headingBaseline then headingBaseline=xrHeading end
  local relativeHeading=xrHeading and wrapDegrees180(xrHeading-headingBaseline) or nil
  state.diagnostics.heading={
    beamngDegrees=beamHeading,
    openXrDegrees=xrHeading,
    openXrFromBaselineDegrees=relativeHeading,
    beamngMinusOpenXrDegrees=xrHeading and wrapDegrees180(beamHeading-xrHeading) or nil,
    baselineOpenXrDegrees=headingBaseline,
    alignedRelativeDegrees=alignedHeading,
    alignedErrorDegrees=relativeHeading and alignedHeading and wrapDegrees180(relativeHeading-alignedHeading) or nil
  }
  state.diagnostics.selectedHmdTranslationMode=mode
  state.diagnostics.candidateHmdWorldPositions={
    beamngOnly=candidates.beamngOnly.p,
    beamngPlusHmdDelta=candidates.beamngPlusHmdDelta.p,
    beamngMinusHmdDelta=candidates.beamngMinusHmdDelta.p,
    beamngFixedBaseHmdDelta=candidates.beamngFixedBaseHmdDelta.p
  }
  state.diagnostics.fixedBaseCandidateHmdWorldPosition=candidates.beamngFixedBaseHmdDelta.p
  state.diagnostics.actualHmdWorldPosition=selected.p -- retained for PR #13 diagnostic consumers
  return selected,candidates
end
local function receive()
  while true do
    local data=sock:receive()
    if not data then break end
    local ok,p=pcall(jsonDecode,data)
    if ok and p and p.v==2 and type(p.counter)=='number' and p.counter>lastCounter then p.received=socketlib.gettime(); latest=p; lastCounter=p.counter end
  end
end
-- The supplied API dump exposes debugDrawer only as userdata, and this extension
-- already relies on its proven drawSphere signature.  Closely spaced spheres
-- therefore form VR-visible sticks without guessing an undocumented line API.
local function drawSphereStick(a,b,diameter,colour)
  local length=distance(a,b)
  if length==0 then return end
  local steps=math.max(1,math.ceil(length/math.max(diameter,0.001)))
  for i=0,steps do
    local t=i/steps
    debugDrawer:drawSphere(vec3({a[1]+(b[1]-a[1])*t,a[2]+(b[2]-a[2])*t,a[3]+(b[3]-a[3])*t}),diameter/2,colour)
  end
end
local function drawTripod(centre,endpoints,settings)
  local colours={x=ColorF(1,0,0,1),y=ColorF(0,1,0,1),z=ColorF(0,0,1,1)}
  for _,axis in ipairs({'x','y','z'}) do
    drawSphereStick(centre,endpoints[axis],settings.lineThickness,colours[axis])
    debugDrawer:drawSphere(vec3(endpoints[axis]),settings.endpointDiameter/2,colours[axis])
  end
end
local function drawDiagnostics(candidates,hmdWorld)
  local settings=cfg.axisTripods or {}
  settings.axisLength=settings.axisLength or 0.25
  settings.endpointDiameter=settings.endpointDiameter or 0.025
  settings.lineThickness=settings.lineThickness or 0.01
  local tripodState={enabled=settings.enabled==true,axisLength=settings.axisLength or 0.25,
    drawDiagnosticSphereTripods=settings.drawDiagnosticSphereTripods==true,
    drawControllerTripods=settings.drawControllerTripods==true,drawOriginLines=settings.drawOriginLines==true,
    diagnostic={},controllers={},originLines={}}
  local cameraAxes=cfg.cameraAxisSpheres or {}
  if cameraAxes.enabled==true then
    local distance=cameraAxes.distance or 1.0
    local diameter=cameraAxes.diameter or cfg.sphereDiameter
    local axes={
      right={offset={distance,0,0},colour=ColorF(1,0,0,1)},
      forward={offset={0,distance,0},colour=ColorF(0,1,0,1)},
      up={offset={0,0,distance},colour=ColorF(0,0,1,1)}
    }
    state.diagnostics.cameraAxisSphereWorldPositions={}
    for name,axis in pairs(axes) do
      local sphere=compose(candidates.beamngOnly,{p=axis.offset,q={0,0,0,1}})
      state.diagnostics.cameraAxisSphereWorldPositions[name]=sphere.p
      debugDrawer:drawSphere(vec3(sphere.p),diameter/2,axis.colour)
    end
  else
    state.diagnostics.cameraAxisSphereWorldPositions=nil
  end
  if cfg.cameraTestSphere and cfg.cameraTestSphere.enabled then
    local localPos=cfg.cameraTestSphere.offset or {0,1,0}
    local red=compose(candidates.beamngOnly,{p=localPos,q={0,0,0,1}})
    local green=compose(candidates.beamngPlusHmdDelta,{p=localPos,q={0,0,0,1}})
    local yellow=compose(candidates.beamngMinusHmdDelta,{p=localPos,q={0,0,0,1}})
    local white=compose(candidates.beamngFixedBaseHmdDelta,{p=localPos,q={0,0,0,1}})
    state.diagnostics.diagnosticSphereWorldPositions={beamngOnly=red.p,beamngPlusHmdDelta=green.p,beamngMinusHmdDelta=yellow.p,beamngFixedBaseHmdDelta=white.p}
    state.diagnostics.fixedBaseDiagnosticSphereWorldPosition=white.p
    state.diagnostics.cameraTestSphereWorld=red.p -- backward-compatible name
    local radius=(cfg.cameraTestSphere.diameter or cfg.sphereDiameter)/2
    debugDrawer:drawSphere(vec3(red.p),radius,ColorF(1,0,0,1))
    debugDrawer:drawSphere(vec3(green.p),radius,ColorF(0,1,0,1))
    debugDrawer:drawSphere(vec3(yellow.p),radius,ColorF(1,1,0,1))
    debugDrawer:drawSphere(vec3(white.p),radius,ColorF(1,1,1,1))
    for name,item in pairs({beamngOnly=red,beamngPlusHmdDelta=green,beamngMinusHmdDelta=yellow,beamngFixedBaseHmdDelta=white}) do
      local endpoints=tripod(item.p,item.q,tripodState.axisLength)
      tripodState.diagnostic[name]={centre=item.p,orientation=item.q,endpoints=endpoints}
      tripodState.originLines[name]={start=candidates[name].p,endpoint=item.p}
      if tripodState.enabled and tripodState.drawDiagnosticSphereTripods then drawTripod(item.p,endpoints,settings) end
      if tripodState.enabled and tripodState.drawOriginLines then drawSphereStick(candidates[name].p,item.p,settings.lineThickness,ColorF(0.75,0.75,0.75,1)) end
    end
  end
  local c=ColorF(cfg.sphereColour[1],cfg.sphereColour[2],cfg.sphereColour[3],cfg.sphereColour[4]); local radius=cfg.sphereDiameter/2
  for _,hand in ipairs({'left','right'}) do
    local p=state[hand..'ControllerWorld']
    if p.valid then
      debugDrawer:drawSphere(vec3(p.position),radius,c)
      local endpoints=tripod(p.position,p.orientation,tripodState.axisLength)
      tripodState.controllers[hand]={centre=p.position,orientation=p.orientation,endpoints=endpoints}
      tripodState.originLines[hand]={start=hmdWorld.p,endpoint=p.position}
      if tripodState.enabled and tripodState.drawControllerTripods then drawTripod(p.position,endpoints,settings) end
      if tripodState.enabled and tripodState.drawOriginLines then
        drawSphereStick(hmdWorld.p,p.position,settings.lineThickness,hand=='left' and ColorF(0,1,1,1) or ColorF(1,0,1,1))
      end
    end
  end
  state.diagnostics.axisTripods=tripodState
end
function M.onExtensionLoaded()
  cfg=jsonReadFile('settings/beamngVRControllerPoses.json') or jsonReadFile('/settings/beamngVRControllerPoses.json')
  if not cfg then log('E','beamngVRControllerPoses','configuration not found'); return false end
  socketlib=require('socket'); sock=socketlib.udp(); sock:settimeout(0); assert(sock:setsockname(cfg.listenAddress,cfg.listenPort))
  resetHmdBaseline('extension loaded')
  log('I','beamngVRControllerPoses','listening for pose datagrams on '..cfg.listenAddress..':'..cfg.listenPort)
end
function M.onPreRender(dtReal,dtSim,dtRaw)
  if not sock then return end; receive(); local now=socketlib.gettime(); local cameraAnchor=beamCameraWorld()
  if not latest or not cameraAnchor or (now-latest.received)*1000>cfg.staleAfterMs then state.leftControllerWorld.valid=false; state.rightControllerWorld.valid=false; return end
  -- Packets from older protocol-2 publishers have no hmd member and retain the
  -- previous camera-anchor behavior rather than being rejected.
  local hmdWorld,candidates=actualHmdWorld(cameraAnchor,latest.hmd)
  state.diagnostics.cameraWorld=hmdWorld
  updateHand('left',latest.left,hmdWorld,now); updateHand('right',latest.right,hmdWorld,now)
  state.diagnostics.finalControllerWorldPositions={
    left=state.leftControllerWorld.valid and state.leftControllerWorld.position or nil,
    right=state.rightControllerWorld.valid and state.rightControllerWorld.position or nil
  }
  state.diagnostics.finalLeftControllerWorldPosition=state.diagnostics.finalControllerWorldPositions.left
  state.diagnostics.finalRightControllerWorldPosition=state.diagnostics.finalControllerWorldPositions.right
  drawDiagnostics(candidates,hmdWorld)
  if now-lastLog>cfg.logIntervalSeconds then
    log('I','beamngVRControllerPoses','fixed-base diagnostics='..dumps(state.diagnostics)); lastLog=now
  end
end
function M.resetHmdBaseline() resetHmdBaseline('explicit reset') end
function M.resetHeadingBaseline()
  headingBaseline=nil; alignedHeading=nil
  state.diagnostics.heading=nil; state.diagnostics.headingVisual=nil
  return true
end
function M.markCurrentHeadingAsAligned()
  local heading=state.diagnostics.heading
  if not heading or heading.openXrFromBaselineDegrees==nil then return false end
  alignedHeading=heading.openXrFromBaselineDegrees
  heading.alignedRelativeDegrees=alignedHeading; heading.alignedErrorDegrees=0
  return true
end
function M.setHmdTranslationMode(mode)
  if not validHmdTranslationModes[mode] then
    log('E','beamngVRControllerPoses','invalid HMD translation mode: '..tostring(mode))
    return false
  end
  cfg.hmdTranslationMode=mode
  resetHmdBaseline('translation mode changed to '..mode)
  state.diagnostics.selectedHmdTranslationMode=mode
  return true
end
function M.setAxisTripodsEnabled(enabled) cfg.axisTripods.enabled=enabled==true; return true end
function M.setDiagnosticTripodsEnabled(enabled) cfg.axisTripods.drawDiagnosticSphereTripods=enabled==true; return true end
function M.setControllerTripodsEnabled(enabled) cfg.axisTripods.drawControllerTripods=enabled==true; return true end
function M.setOriginLinesEnabled(enabled) cfg.axisTripods.drawOriginLines=enabled==true; return true end
function M.getState() return state end
function M.onExtensionUnloaded() if sock then sock:close(); sock=nil end end
return M
