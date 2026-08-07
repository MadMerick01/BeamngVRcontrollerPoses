-- Stage 1 consumer. UDP receive is non-blocking; debug primitives are submitted every frame.
local M = {}
local sock, socketlib, cfg, latest, lastCounter, lastLog = nil, nil, nil, nil, -1, 0
local state = {leftControllerWorld={valid=false}, rightControllerWorld={valid=false}, diagnostics={}}
local cameraSourceMode='beamngOnly'
local hmdBaseline, hmdSpaceKey, previousRawHmd, previousHmdSampleTime = nil, nil, nil, nil
local worldFromBaseQ=nil
local headingBaseline, alignedHeading = nil, nil
local rigidBaseline, lastBaselineRigidCandidate = nil, nil
local rebasedWorldFromTracking, lastRebasedRigidCandidate=nil,nil
local artificialYawRebaseCount, lastArtificialRebaseLog = 0, 0
local movingWorldFromTracking, previousBeamngCameraAnchorPosition, lastMovingRigidCandidate=nil,nil,nil
local baselineOrangeReferenceHmdWorldPosition=nil
local movingArtificialYawRebaseCount=0
local geluaCapture={captureInstalled=false,captureAvailable=false,captureFailureReason='capture not installed',setterSequence=0,pairComplete=false}
local geluaOriginalSetter,geluaOriginalGetter,geluaSetterWrapper,geluaGetterWrapper=nil,nil,nil,nil
local unpackValues=table.unpack or unpack
local function packValues(...) return {n=select('#',...),...} end

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
local function quaternionAngularDifferenceDegrees(a,b)
  local an,bn=qnorm(a),qnorm(b)
  if not an or not bn then return nil end
  local dot=math.abs(an[1]*bn[1]+an[2]*bn[2]+an[3]*bn[3]+an[4]*bn[4])
  dot=math.min(1,math.max(-1,dot))
  return math.deg(2*math.acos(dot))
end
local function vaddScaled(p,d,s) return {p[1]+d[1]*s,p[2]+d[2]*s,p[3]+d[3]*s} end
local function tripod(p,q,length)
  return {x=vaddScaled(p,qrot(q,{1,0,0}),length),y=vaddScaled(p,qrot(q,{0,1,0}),length),z=vaddScaled(p,qrot(q,{0,0,1}),length)}
end
local function compose(a,b) local p=qrot(a.q,b.p); return {p={a.p[1]+p[1],a.p[2]+p[2],a.p[3]+p[3]},q=qmul(a.q,b.q)} end
local function inversePose(t)
  local qi=qinv(t.q)
  return {p=qrot(qi,{-t.p[1],-t.p[2],-t.p[3]}),q=qi}
end
local function mappedPosition(p)
  local o,s=cfg.axisOrder,cfg.axisSign
  return {p[o[1]]*s[1]*cfg.metresToBeamNGUnit,p[o[2]]*s[2]*cfg.metresToBeamNGUnit,p[o[3]]*s[3]*cfg.metresToBeamNGUnit}
end
local function mappedPose(p)
  local v=mappedPosition(p.p)
  local b=cfg.quaternionBasis
  return {p=v,q=qmul(qmul(b,p.q),qinv(b))}
end
local function mappedTrackingHmdPose(p,q)
  -- The confirmed (x,y,z)->(x,-z,y) conversion is one +90-degree X basis
  -- change, applied consistently to both components of this complete pose.
  local root=math.sqrt(0.5)
  local basis={root,0,0,root}
  return {p=mappedPosition(p),q=qnorm(qmul(qmul(basis,q),qinv(basis)))}
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
  rigidBaseline=nil; lastBaselineRigidCandidate=nil; rebasedWorldFromTracking=nil; lastRebasedRigidCandidate=nil
  movingWorldFromTracking=nil; previousBeamngCameraAnchorPosition=nil; lastMovingRigidCandidate=nil
  baselineOrangeReferenceHmdWorldPosition=nil; movingArtificialYawRebaseCount=0
  artificialYawRebaseCount=0; lastArtificialRebaseLog=0
  state.baselineValid=false
  state.baselineRigidCandidateHmdWorld=nil
  state.baselineRigidPositionBeamngRotationHmdWorld=nil
  state.baselineRigidPosition=nil; state.baselineRigidTrackingOrientation=nil
  state.selectedHybridOrientation=nil
  state.hybridLeftControllerWorld=nil; state.hybridRightControllerWorld=nil
  state.hybridDiagnosticSphereWorldPosition=nil
  state.targetWorldFromTrackingOrientation=nil
  state.storedWorldFromTrackingOrientationBeforeRebase=nil
  state.artificialAlignmentDeltaDegrees=nil
  state.artificialYawRebaseTriggered=false
  state.artificialYawRebaseCount=0
  state.lastArtificialYawRebaseReason=nil; state.lastArtificialYawRebaseTime=nil
  state.hmdWorldPositionBeforeArtificialRebase=nil; state.hmdWorldPositionAfterArtificialRebase=nil
  state.artificialRebasePositionDiscontinuityMetres=nil
  state.rebasedWorldFromTracking=nil; state.rebasedHybridHmdWorld=nil
  state.rebasedHybridLeftControllerWorld=nil; state.rebasedHybridRightControllerWorld=nil
  state.rebasedHybridDiagnosticSphereWorldPosition=nil
  state.movingReferenceWorldFromTracking=nil; state.previousBeamngCameraAnchorPosition=nil
  state.baselineOrangeReferenceHmdWorldPosition=nil; state.currentOrangeReferenceHmdWorldPosition=nil
  state.physicalOffsetFromRecenter=nil; state.physicalOffsetFromRecenterMagnitude=nil
  state.currentBeamngCameraAnchorPosition=nil; state.absoluteMovingHmdWorldPosition=nil
  state.absoluteMovingHmdWorldOrientation=nil; state.absoluteMovingHybridHmdWorld=nil
  state.absoluteMovingLeftControllerWorld=nil; state.absoluteMovingRightControllerWorld=nil
  state.absoluteMovingDiagnosticSphereWorldPosition=nil; state.beamngAnchorJumpDetected=false
  state.movingAnchorJumpDetected=false
  state.movingAnchorResetReason=reason; state.movingCandidateHmdWorld=nil; state.movingHybridHmdWorld=nil
  state.movingHybridLeftControllerWorld=nil; state.movingHybridRightControllerWorld=nil
  state.movingHybridDiagnosticSphereWorldPosition=nil; state.movingArtificialYawRebaseCount=0
  state.movingArtificialYawAlignmentDeltaDegrees=nil; state.movingPositionBeforeAnchorUpdate=nil
  state.anchorSource='core_camera.getPosition'
  state.physicalOffsetSource='PR28 orange reference from native OpenXR HMD'
  state.orientationSource='corrected core_camera.getQuat'
  state.diagnostics.baselineRigidPositionBeamngRotationHmdWorld=nil
  state.diagnostics.baselineRigidPosition=nil; state.diagnostics.baselineRigidTrackingOrientation=nil
  state.diagnostics.selectedHybridOrientation=nil
  state.diagnostics.hybridLeftControllerWorld=nil; state.diagnostics.hybridRightControllerWorld=nil
  state.diagnostics.hybridDiagnosticSphereWorldPosition=nil
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
local function finiteNumber(value)
  return type(value)=='number' and value==value and value~=math.huge and value~=-math.huge
end
local function captureNow() return socketlib and socketlib.gettime() or os.clock() end
local function validSeven(a,b,c,d,e,f,g)
  local values={a,b,c,d,e,f,g}
  for i=1,7 do if not finiteNumber(values[i]) then return false end end
  local qlen=d*d+e*e+f*f+g*g
  return qlen>0
end
local function invalidateGeluaCapture(reason)
  geluaCapture.captureAvailable=false; geluaCapture.captureFailureReason=reason
  geluaCapture.pairComplete=false; geluaCapture.getterSequence=nil
end
local function geluaNativeCandidate(now)
  local maxAgeMs=(cfg and cfg.geluaCaptureMaxAgeMs) or 100
  geluaCapture.pairAgeMs=geluaCapture.getterTimestamp and (now-geluaCapture.getterTimestamp)*1000 or nil
  if not geluaCapture.captureInstalled then return nil,'capture not installed' end
  if not geluaCapture.pairComplete then return nil,geluaCapture.captureFailureReason or 'setter/getter pair incomplete' end
  if geluaCapture.getterSequence~=geluaCapture.setterSequence then return nil,'setter/getter sequence mismatch' end
  if not geluaCapture.setterTimestamp or not geluaCapture.getterTimestamp or geluaCapture.getterTimestamp<geluaCapture.setterTimestamp then return nil,'getter was not captured after setter' end
  if not geluaCapture.pairAgeMs or geluaCapture.pairAgeMs>maxAgeMs then return nil,'setter/getter pair is stale' end
  local a,p=geluaCapture.rawAnchorPosition,geluaCapture.rawPredictedPosition
  local aq,pq=geluaCapture.rawAnchorQuaternion,geluaCapture.rawPredictedQuaternion
  if not a or not p or not aq or not pq then return nil,'capture values unavailable' end
  -- BeamNG gameengine.lua calls setAddXYZ(predicted position), then
  -- setMulXYZW(predicted XYZW, anchor XYZW): Hamilton predicted * anchor.
  local q=qnorm(qmul({pq[1],pq[2],pq[3],pq[4]},{aq[1],aq[2],aq[3],aq[4]}))
  if not q then return nil,'composed quaternion has zero length' end
  return {p={a[1]+p[1],a[2]+p[2],a[3]+p[3]},q=q},nil
end
local function syncGeluaDiagnostics()
  local values={geluaCaptureInstalled=geluaCapture.captureInstalled,geluaCaptureAvailable=geluaCapture.captureAvailable,
    geluaCaptureFailureReason=geluaCapture.captureFailureReason,geluaSetterSequence=geluaCapture.setterSequence,
    geluaSetterTimestamp=geluaCapture.setterTimestamp,geluaGetterTimestamp=geluaCapture.getterTimestamp,
    geluaPairComplete=geluaCapture.pairComplete,geluaPairAgeMs=geluaCapture.pairAgeMs,
    geluaRawAnchorPosition=geluaCapture.rawAnchorPosition,geluaRawAnchorQuaternion=geluaCapture.rawAnchorQuaternion,
    geluaRawPredictedPosition=geluaCapture.rawPredictedPosition,geluaRawPredictedQuaternion=geluaCapture.rawPredictedQuaternion,
    geluaNativeFinalVrPosition=state.geluaNativeFinalVrPosition,geluaNativeFinalVrQuaternion=state.geluaNativeFinalVrQuaternion,
    geluaNativeLeftControllerWorld=state.geluaNativeLeftControllerWorld,geluaNativeRightControllerWorld=state.geluaNativeRightControllerWorld,
    geluaNativeDiagnosticSphereWorld=state.geluaNativeDiagnosticSphereWorld}
  local fields={'geluaCaptureInstalled','geluaCaptureAvailable','geluaCaptureFailureReason','geluaSetterSequence','geluaSetterTimestamp','geluaGetterTimestamp','geluaPairComplete','geluaPairAgeMs','geluaRawAnchorPosition','geluaRawAnchorQuaternion','geluaRawPredictedPosition','geluaRawPredictedQuaternion','geluaNativeFinalVrPosition','geluaNativeFinalVrQuaternion','geluaNativeLeftControllerWorld','geluaNativeRightControllerWorld','geluaNativeDiagnosticSphereWorld'}
  for _,key in ipairs(fields) do state[key]=values[key]; state.diagnostics[key]=values[key] end
end
local function beamCameraWorld()
  local pos=vec3ToTable(core_camera and core_camera.getPosition and core_camera.getPosition())
  local rawRot=quatToXYZW(core_camera and core_camera.getQuat and core_camera.getQuat())
  if not pos or not rawRot or not rawRot[4] then return nil end
  for i=1,3 do if not finiteNumber(pos[i]) then return nil end end
  for i=1,4 do if not finiteNumber(rawRot[i]) then return nil end end
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
local validHmdTranslationModes={beamngOnly=true,beamngPlusHmdDelta=true,beamngMinusHmdDelta=true,beamngFixedBaseHmdDelta=true,baselineRigidTracking=true,baselineRigidPositionBeamngRotation=true,baselineRigidPositionBeamngRotationRebased=true,baselineRigidPositionBeamngRotationRebasedMovingAnchor=true,geluaNativeCameraComposition=true}
local function hmdCandidates(cameraAnchor, worldDelta, fixedDelta, baselineRigidCandidate)
  -- Keep these as direct calculations from the BeamNG camera.  The test
  -- alternatives must never accumulate or derive from one another.
  local hybridCandidate=baselineRigidCandidate and {
    -- Deliberately preserve the complete PR #26 candidate position verbatim,
    -- while using the already-corrected live BeamNG camera-to-world rotation.
    p={baselineRigidCandidate.p[1],baselineRigidCandidate.p[2],baselineRigidCandidate.p[3]},
    q=qnorm(cameraAnchor.q)
  } or nil
  local rebasedHybridCandidate=lastRebasedRigidCandidate and {
    p={lastRebasedRigidCandidate.p[1],lastRebasedRigidCandidate.p[2],lastRebasedRigidCandidate.p[3]},
    q=qnorm(cameraAnchor.q)
  } or nil
  local movingHybridCandidate=lastMovingRigidCandidate and {
    p={lastMovingRigidCandidate.p[1],lastMovingRigidCandidate.p[2],lastMovingRigidCandidate.p[3]},
    q=qnorm(cameraAnchor.q)
  } or nil
  return {
    beamngOnly={p={cameraAnchor.p[1],cameraAnchor.p[2],cameraAnchor.p[3]},q=cameraAnchor.q},
    beamngPlusHmdDelta={p={cameraAnchor.p[1]+worldDelta[1],cameraAnchor.p[2]+worldDelta[2],cameraAnchor.p[3]+worldDelta[3]},q=cameraAnchor.q},
    beamngMinusHmdDelta={p={cameraAnchor.p[1]-worldDelta[1],cameraAnchor.p[2]-worldDelta[2],cameraAnchor.p[3]-worldDelta[3]},q=cameraAnchor.q},
    beamngFixedBaseHmdDelta={p={cameraAnchor.p[1]+fixedDelta[1],cameraAnchor.p[2]+fixedDelta[2],cameraAnchor.p[3]+fixedDelta[3]},q=cameraAnchor.q},
    baselineRigidTracking=baselineRigidCandidate,
    baselineRigidPositionBeamngRotation=hybridCandidate,
    baselineRigidPositionBeamngRotationRebased=rebasedHybridCandidate,
    baselineRigidPositionBeamngRotationRebasedMovingAnchor=movingHybridCandidate
  }
end
local function diagnosticControllerWorld(name,raw,hmdWorld)
  if not raw or not raw.valid then return nil end
  local rel=mappedPose(raw)
  local offset={p=cfg[name..'PositionOffset'],q=cfg[name..'RotationOffset']}
  return compose(hmdWorld,compose(rel,offset))
end
local function actualHmdWorld(cameraAnchor, hmd)
  local rawDelta,mappedDelta,worldDelta,fixedDelta=nil,nil,{0,0,0},{0,0,0}
  local rawQ,mappedQ=nil,nil
  local mappedTrackingHmd=nil
  state.artificialYawRebaseTriggered=false
  local valid=hmd and hmd.valid and hmd.p and hmd.q
  if valid then
    for i=1,3 do if not finiteNumber(hmd.p[i]) then valid=false end end
    for i=1,4 do if not finiteNumber(hmd.q[i]) then valid=false end end
  end
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
  if rawQ then mappedQ=qnorm(qmul(qmul(basis,rawQ),qinv(basis))) end
  if not rawQ or not mappedQ then valid=false end
  if valid then
  mappedTrackingHmd=mappedTrackingHmdPose(hmd.p,rawQ)
  if not hmdBaseline then
    worldFromBaseQ=qnorm(qmul(qnorm(cameraAnchor.q),qinv(mappedQ)))
    hmdBaseline={
      p={hmd.p[1],hmd.p[2],hmd.p[3]},q=rawQ,mappedQ=mappedQ,
      cameraQ=qnorm(cameraAnchor.q),worldFromBaseQ=worldFromBaseQ,
      session=hmd.session,base=hmd.base,sampleTime=hmd.sampleTime
    }
    hmdSpaceKey=key
    local baselineBeamngCameraWorld={p={cameraAnchor.p[1],cameraAnchor.p[2],cameraAnchor.p[3]},q=qnorm(cameraAnchor.q)}
    rigidBaseline={
      beamngCameraWorld=baselineBeamngCameraWorld,
      trackingHmdRaw={p={hmd.p[1],hmd.p[2],hmd.p[3]},q=rawQ},
      trackingHmdMapped=mappedTrackingHmd
    }
    -- Complete rigid transform: baseline camera world * inverse(mapped tracking HMD).
    rigidBaseline.worldFromTracking=compose(baselineBeamngCameraWorld,inversePose(mappedTrackingHmd))
    rebasedWorldFromTracking={p={rigidBaseline.worldFromTracking.p[1],rigidBaseline.worldFromTracking.p[2],rigidBaseline.worldFromTracking.p[3]},q={rigidBaseline.worldFromTracking.q[1],rigidBaseline.worldFromTracking.q[2],rigidBaseline.worldFromTracking.q[3],rigidBaseline.worldFromTracking.q[4]}}
    movingWorldFromTracking={p={rigidBaseline.worldFromTracking.p[1],rigidBaseline.worldFromTracking.p[2],rigidBaseline.worldFromTracking.p[3]},q={rigidBaseline.worldFromTracking.q[1],rigidBaseline.worldFromTracking.q[2],rigidBaseline.worldFromTracking.q[3],rigidBaseline.worldFromTracking.q[4]}}
    previousBeamngCameraAnchorPosition={cameraAnchor.p[1],cameraAnchor.p[2],cameraAnchor.p[3]}
    local initialMovingHmdWorld=compose(movingWorldFromTracking,mappedTrackingHmd)
    baselineOrangeReferenceHmdWorldPosition={initialMovingHmdWorld.p[1],initialMovingHmdWorld.p[2],initialMovingHmdWorld.p[3]}
  end
  previousRawHmd={hmd.p[1],hmd.p[2],hmd.p[3]}; previousHmdSampleTime=hmd.sampleTime

  -- The baseline removes standing height/tracking-origin placement.  Apply the
  -- confirmed OpenXR -> BeamNG mapping (x,y,z) -> (x,-z,y), then rotate the
  -- room-scale displacement by the corrected camera-to-world quaternion.
  rawDelta={hmd.p[1]-hmdBaseline.p[1],hmd.p[2]-hmdBaseline.p[2],hmd.p[3]-hmdBaseline.p[3]}
  mappedDelta=mappedPosition(rawDelta)
  worldDelta=qrot(cameraAnchor.q,mappedDelta)
  fixedDelta=qrot(worldFromBaseQ,mappedDelta)
  local targetQ=qnorm(qmul(qnorm(cameraAnchor.q),qinv(mappedTrackingHmd.q)))
  local storedQ=rebasedWorldFromTracking.q
  local alignmentDelta=quaternionAngularDifferenceDegrees(storedQ,targetQ)
  state.targetWorldFromTrackingOrientation=targetQ
  state.artificialAlignmentDeltaDegrees=alignmentDelta
  if cfg.hmdTranslationMode=='baselineRigidPositionBeamngRotationRebased' and
      alignmentDelta and alignmentDelta>(cfg.artificialYawRebaseThresholdDegrees or 0.75) then
    local before=compose(rebasedWorldFromTracking,mappedTrackingHmd)
    local rotatedTrackingPosition=qrot(targetQ,mappedTrackingHmd.p)
    local newP={before.p[1]-rotatedTrackingPosition[1],before.p[2]-rotatedTrackingPosition[2],before.p[3]-rotatedTrackingPosition[3]}
    state.storedWorldFromTrackingOrientationBeforeRebase={storedQ[1],storedQ[2],storedQ[3],storedQ[4]}
    rebasedWorldFromTracking={p=newP,q=targetQ}
    local after=compose(rebasedWorldFromTracking,mappedTrackingHmd)
    artificialYawRebaseCount=artificialYawRebaseCount+1
    state.artificialYawRebaseTriggered=true; state.artificialYawRebaseCount=artificialYawRebaseCount
    state.lastArtificialYawRebaseReason='BeamNG camera/tracking HMD alignment exceeded threshold'
    state.lastArtificialYawRebaseTime=socketlib and socketlib.gettime() or os.clock()
    state.hmdWorldPositionBeforeArtificialRebase=before.p; state.hmdWorldPositionAfterArtificialRebase=after.p
    state.artificialRebasePositionDiscontinuityMetres=distance(before.p,after.p)
    if state.lastArtificialYawRebaseTime-lastArtificialRebaseLog>(cfg.logIntervalSeconds or 5) then
      log('I','beamngVRControllerPoses','artificial yaw rebase delta='..tostring(alignmentDelta)..' degrees, position discontinuity='..tostring(state.artificialRebasePositionDiscontinuityMetres))
      lastArtificialRebaseLog=state.lastArtificialYawRebaseTime
    end
  end
  state.artificialYawRebaseCount=artificialYawRebaseCount
  state.rebasedWorldFromTracking=rebasedWorldFromTracking
  if cfg.hmdTranslationMode=='baselineRigidPositionBeamngRotationRebasedMovingAnchor' then
    local currentAnchor={cameraAnchor.p[1],cameraAnchor.p[2],cameraAnchor.p[3]}
    local anchorStepMagnitude=distance(currentAnchor,previousBeamngCameraAnchorPosition)
    state.beamngAnchorJumpThreshold=cfg.beamngAnchorJumpMetres or 5.0
    state.beamngAnchorJumpDetected=anchorStepMagnitude>state.beamngAnchorJumpThreshold
    state.movingAnchorJumpDetected=state.beamngAnchorJumpDetected
    if state.beamngAnchorJumpDetected then
      resetHmdBaseline('BeamNG camera anchor jump')
      local resetSelected,resetCandidates=actualHmdWorld(cameraAnchor,hmd)
      state.beamngAnchorJumpDetected=true
      state.movingAnchorJumpDetected=true
      state.movingAnchorResetReason='BeamNG camera anchor jump'
      state.currentBeamngCameraAnchorPosition=currentAnchor
      return resetSelected,resetCandidates
    end
    local movingTargetQ=qnorm(qmul(qnorm(cameraAnchor.q),qinv(mappedTrackingHmd.q)))
    local movingDelta=quaternionAngularDifferenceDegrees(movingWorldFromTracking.q,movingTargetQ)
    state.movingArtificialYawAlignmentDeltaDegrees=movingDelta
    if movingDelta and movingDelta>(cfg.artificialYawRebaseThresholdDegrees or 0.75) then
      local movingBefore=compose(movingWorldFromTracking,mappedTrackingHmd)
      local rotatedTrackingPosition=qrot(movingTargetQ,mappedTrackingHmd.p)
      movingWorldFromTracking={p={movingBefore.p[1]-rotatedTrackingPosition[1],movingBefore.p[2]-rotatedTrackingPosition[2],movingBefore.p[3]-rotatedTrackingPosition[3]},q=movingTargetQ}
      movingArtificialYawRebaseCount=movingArtificialYawRebaseCount+1
    end
    previousBeamngCameraAnchorPosition=currentAnchor
    state.movingArtificialYawRebaseCount=movingArtificialYawRebaseCount
    state.movingReferenceWorldFromTracking=movingWorldFromTracking
    local currentOrangeReferenceHmdWorld=compose(movingWorldFromTracking,mappedTrackingHmd)
    local physicalOffsetFromRecenter={currentOrangeReferenceHmdWorld.p[1]-baselineOrangeReferenceHmdWorldPosition[1],currentOrangeReferenceHmdWorld.p[2]-baselineOrangeReferenceHmdWorldPosition[2],currentOrangeReferenceHmdWorld.p[3]-baselineOrangeReferenceHmdWorldPosition[3]}
    lastMovingRigidCandidate={p={currentAnchor[1]+physicalOffsetFromRecenter[1],currentAnchor[2]+physicalOffsetFromRecenter[2],currentAnchor[3]+physicalOffsetFromRecenter[3]},q=qnorm(cameraAnchor.q)}
    state.baselineOrangeReferenceHmdWorldPosition=baselineOrangeReferenceHmdWorldPosition
    state.currentOrangeReferenceHmdWorldPosition=currentOrangeReferenceHmdWorld.p
    state.physicalOffsetFromRecenter=physicalOffsetFromRecenter
    state.physicalOffsetFromRecenterMagnitude=distance(physicalOffsetFromRecenter,{0,0,0})
    state.currentBeamngCameraAnchorPosition=currentAnchor
    state.absoluteMovingHmdWorldPosition=lastMovingRigidCandidate.p
    state.absoluteMovingHmdWorldOrientation=lastMovingRigidCandidate.q
    state.absoluteMovingHybridHmdWorld=lastMovingRigidCandidate
    state.movingCandidateHmdWorld=lastMovingRigidCandidate
  end
  -- Complete current pose composition. No position delta is independently added.
  lastBaselineRigidCandidate=compose(rigidBaseline.worldFromTracking,mappedTrackingHmd)
  lastRebasedRigidCandidate=compose(rebasedWorldFromTracking,mappedTrackingHmd)
  end
  end
  local candidates=hmdCandidates(cameraAnchor,worldDelta,fixedDelta,lastBaselineRigidCandidate)
  local nativeCandidate,nativeFailure=geluaNativeCandidate(captureNow())
  candidates.geluaNativeCameraComposition=nativeCandidate
  local requestedMode=cfg.hmdTranslationMode
  local mode=requestedMode
  if not validHmdTranslationModes[mode] then mode='beamngOnly'; cfg.hmdTranslationMode=mode end
  if mode=='baselineRigidPositionBeamngRotation' and not candidates.baselineRigidPositionBeamngRotation then mode='beamngOnly' end
  if mode=='baselineRigidPositionBeamngRotationRebased' and not candidates.baselineRigidPositionBeamngRotationRebased then mode='beamngOnly' end
  if mode=='baselineRigidPositionBeamngRotationRebasedMovingAnchor' and not candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor then mode='beamngOnly' end
  state.selectedModeFallback=nil; state.selectedModeFallbackReason=nil
  if mode=='geluaNativeCameraComposition' and not nativeCandidate then
    if candidates.baselineRigidPositionBeamngRotationRebased then mode='baselineRigidPositionBeamngRotationRebased' else mode='beamngOnly' end
    state.selectedModeFallback=mode; state.selectedModeFallbackReason=nativeFailure
  end
  local selected=candidates[mode] or candidates.beamngOnly
  state.selectedHmdTranslationMode=mode
  state.selectedMode=requestedMode
  state.baselineValid=rigidBaseline~=nil
  state.baselineResetReason=state.diagnostics.baselineResetReason
  state.baselineBeamngCameraWorld=rigidBaseline and rigidBaseline.beamngCameraWorld or nil
  state.baselineTrackingHmdRaw=rigidBaseline and rigidBaseline.trackingHmdRaw or nil
  state.baselineTrackingHmdMapped=rigidBaseline and rigidBaseline.trackingHmdMapped or nil
  state.baselineWorldFromTracking=rigidBaseline and rigidBaseline.worldFromTracking or nil
  state.currentTrackingHmdRaw=valid and {p={hmd.p[1],hmd.p[2],hmd.p[3]},q=rawQ} or state.currentTrackingHmdRaw
  state.currentTrackingHmdMapped=valid and mappedTrackingHmdPose(hmd.p,rawQ) or state.currentTrackingHmdMapped
  state.baselineRigidCandidateHmdWorld=lastBaselineRigidCandidate
  state.baselineRigidPositionBeamngRotationHmdWorld=candidates.baselineRigidPositionBeamngRotation
  state.baselineRigidPosition=lastBaselineRigidCandidate and lastBaselineRigidCandidate.p or nil
  state.baselineRigidTrackingOrientation=lastBaselineRigidCandidate and lastBaselineRigidCandidate.q or nil
  state.beamngLiveCameraOrientation=qnorm(cameraAnchor.q)
  state.selectedHybridOrientation=candidates.baselineRigidPositionBeamngRotation and candidates.baselineRigidPositionBeamngRotation.q or nil
  state.rebasedHybridHmdWorld=candidates.baselineRigidPositionBeamngRotationRebased
  state.movingHybridHmdWorld=candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor
  state.trackingWorldRight=rigidBaseline and qrot(rigidBaseline.worldFromTracking.q,{1,0,0}) or nil
  state.trackingWorldForward=rigidBaseline and qrot(rigidBaseline.worldFromTracking.q,{0,1,0}) or nil
  state.trackingWorldUp=rigidBaseline and qrot(rigidBaseline.worldFromTracking.q,{0,0,1}) or nil
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
  state.diagnostics.requestedHmdTranslationMode=requestedMode
  state.diagnostics.selectedMode=state.selectedMode
  state.diagnostics.selectedModeFallback=state.selectedModeFallback
  state.diagnostics.selectedModeFallbackReason=state.selectedModeFallbackReason
  state.diagnostics.hybridFallbackReason=(requestedMode=='baselineRigidPositionBeamngRotation' or requestedMode=='baselineRigidPositionBeamngRotationRebased' or requestedMode=='baselineRigidPositionBeamngRotationRebasedMovingAnchor') and mode=='beamngOnly' and 'valid baseline-rigid candidate unavailable' or nil
  state.diagnostics.candidateHmdWorldPositions={
    beamngOnly=candidates.beamngOnly.p,
    beamngPlusHmdDelta=candidates.beamngPlusHmdDelta.p,
    beamngMinusHmdDelta=candidates.beamngMinusHmdDelta.p,
    beamngFixedBaseHmdDelta=candidates.beamngFixedBaseHmdDelta.p
  }
  state.diagnostics.candidateHmdWorldPositions.baselineRigidTracking=candidates.baselineRigidTracking and candidates.baselineRigidTracking.p or nil
  state.diagnostics.candidateHmdWorldPositions.baselineRigidPositionBeamngRotation=candidates.baselineRigidPositionBeamngRotation and candidates.baselineRigidPositionBeamngRotation.p or nil
  state.diagnostics.candidateHmdWorldPositions.baselineRigidPositionBeamngRotationRebased=candidates.baselineRigidPositionBeamngRotationRebased and candidates.baselineRigidPositionBeamngRotationRebased.p or nil
  state.diagnostics.candidateHmdWorldPositions.baselineRigidPositionBeamngRotationRebasedMovingAnchor=candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor and candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor.p or nil
  state.diagnostics.candidateHmdWorldPositions.geluaNativeCameraComposition=nativeCandidate and nativeCandidate.p or nil
  state.geluaNativeFinalVrPosition=nativeCandidate and nativeCandidate.p or nil
  state.geluaNativeFinalVrQuaternion=nativeCandidate and nativeCandidate.q or nil
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
    local purple=candidates.baselineRigidTracking and compose(candidates.baselineRigidTracking,{p=localPos,q={0,0,0,1}}) or nil
    local cyan=candidates.baselineRigidPositionBeamngRotation and compose(candidates.baselineRigidPositionBeamngRotation,{p=localPos,q={0,0,0,1}}) or nil
    local orange=candidates.baselineRigidPositionBeamngRotationRebased and compose(candidates.baselineRigidPositionBeamngRotationRebased,{p=localPos,q={0,0,0,1}}) or nil
    local pink=candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor and compose(candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor,{p=localPos,q={0,0,0,1}}) or nil
    local lime=candidates.geluaNativeCameraComposition and compose(candidates.geluaNativeCameraComposition,{p=localPos,q={0,0,0,1}}) or nil
    state.diagnostics.diagnosticSphereWorldPositions={beamngOnly=red.p,beamngPlusHmdDelta=green.p,beamngMinusHmdDelta=yellow.p,beamngFixedBaseHmdDelta=white.p,baselineRigidTracking=purple and purple.p or nil,baselineRigidPositionBeamngRotation=cyan and cyan.p or nil,baselineRigidPositionBeamngRotationRebased=orange and orange.p or nil,baselineRigidPositionBeamngRotationRebasedMovingAnchor=pink and pink.p or nil,geluaNativeCameraComposition=lime and lime.p or nil}
    state.geluaNativeDiagnosticSphereWorld=lime and lime.p or nil
    state.hybridDiagnosticSphereWorldPosition=cyan and cyan.p or nil
    state.diagnostics.hybridDiagnosticSphereWorldPosition=state.hybridDiagnosticSphereWorldPosition
    state.rebasedHybridDiagnosticSphereWorldPosition=orange and orange.p or nil
    state.diagnostics.rebasedHybridDiagnosticSphereWorldPosition=state.rebasedHybridDiagnosticSphereWorldPosition
    state.movingHybridDiagnosticSphereWorldPosition=pink and pink.p or nil
    state.absoluteMovingDiagnosticSphereWorldPosition=pink and pink.p or nil
    state.diagnostics.movingHybridDiagnosticSphereWorldPosition=state.movingHybridDiagnosticSphereWorldPosition
    state.diagnostics.fixedBaseDiagnosticSphereWorldPosition=white.p
    state.diagnostics.cameraTestSphereWorld=red.p -- backward-compatible name
    local radius=(cfg.cameraTestSphere.diameter or cfg.sphereDiameter)/2
    debugDrawer:drawSphere(vec3(red.p),radius,ColorF(1,0,0,1))
    debugDrawer:drawSphere(vec3(green.p),radius,ColorF(0,1,0,1))
    debugDrawer:drawSphere(vec3(yellow.p),radius,ColorF(1,1,0,1))
    debugDrawer:drawSphere(vec3(white.p),radius,ColorF(1,1,1,1))
    if purple then debugDrawer:drawSphere(vec3(purple.p),radius,ColorF(0.65,0,1,1)) end
    if cyan then debugDrawer:drawSphere(vec3(cyan.p),radius,ColorF(0,1,1,1)) end
    if orange then debugDrawer:drawSphere(vec3(orange.p),radius,ColorF(1,0.5,0,1)) end
    if pink then debugDrawer:drawSphere(vec3(pink.p),radius,ColorF(1,0.2,0.6,1)) end
    if lime then debugDrawer:drawSphere(vec3(lime.p),radius,ColorF(0.5,1,0,1)) end
    local diagnosticItems={beamngOnly=red,beamngPlusHmdDelta=green,beamngMinusHmdDelta=yellow,beamngFixedBaseHmdDelta=white}
    if purple then diagnosticItems.baselineRigidTracking=purple end
    if cyan then diagnosticItems.baselineRigidPositionBeamngRotation=cyan end
    if orange then diagnosticItems.baselineRigidPositionBeamngRotationRebased=orange end
    if pink then diagnosticItems.baselineRigidPositionBeamngRotationRebasedMovingAnchor=pink end
    if lime then diagnosticItems.geluaNativeCameraComposition=lime end
    for name,item in pairs(diagnosticItems) do
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
  cameraSourceMode='beamngOnly'
  cfg.cameraSourceMode='beamngOnly'
  state.selectedCameraSourceMode=cameraSourceMode
  socketlib=require('socket'); sock=socketlib.udp(); sock:settimeout(0); assert(sock:setsockname(cfg.listenAddress,cfg.listenPort))
  resetHmdBaseline('extension loaded')
  log('I','beamngVRControllerPoses','listening for pose datagrams on '..cfg.listenAddress..':'..cfg.listenPort)
end
function M.startGeluaCameraAnchorCapture()
  if geluaCapture.captureInstalled then return true end
  if type(OpenXR)~='table' then invalidateGeluaCapture('OpenXR table unavailable'); log('E','beamngVRControllerPoses',geluaCapture.captureFailureReason); return false,geluaCapture.captureFailureReason end
  local setter,getter=OpenXR.setGeluaCameraPosRot,OpenXR.getCameraPosRotPredictedXYZXYZW
  if type(setter)~='function' or type(getter)~='function' then invalidateGeluaCapture('required OpenXR camera functions unavailable'); log('E','beamngVRControllerPoses',geluaCapture.captureFailureReason); return false,geluaCapture.captureFailureReason end
  if geluaOriginalSetter and (setter~=geluaOriginalSetter or getter~=geluaOriginalGetter) then invalidateGeluaCapture('OpenXR camera functions were replaced unexpectedly'); return false,geluaCapture.captureFailureReason end
  geluaOriginalSetter,geluaOriginalGetter=setter,getter
  geluaSetterWrapper=function(...)
    local args={...}; geluaCapture.setterSequence=geluaCapture.setterSequence+1
    geluaCapture.setterTimestamp=captureNow(); geluaCapture.getterTimestamp=nil; geluaCapture.pairAgeMs=nil; geluaCapture.pairComplete=false; geluaCapture.getterSequence=nil
    if select('#',...)==7 and validSeven(unpackValues(args,1,7)) then
      geluaCapture.rawAnchorPosition={args[1],args[2],args[3]}; geluaCapture.rawAnchorQuaternion={args[4],args[5],args[6],args[7]}
      geluaCapture.captureAvailable=false; geluaCapture.captureFailureReason='awaiting predicted getter for sequence '..geluaCapture.setterSequence
    else invalidateGeluaCapture('setter capture malformed or non-finite') end
    local results=packValues(geluaOriginalSetter(...))
    return unpackValues(results,1,results.n)
  end
  geluaGetterWrapper=function(...)
    local sequence=geluaCapture.setterSequence
    local results=packValues(geluaOriginalGetter(...))
    local timestamp=captureNow()
    if results.n==7 and validSeven(unpackValues(results,1,7)) and sequence>0 and sequence==geluaCapture.setterSequence and geluaCapture.setterTimestamp and timestamp>=geluaCapture.setterTimestamp then
      geluaCapture.rawPredictedPosition={results[1],results[2],results[3]}; geluaCapture.rawPredictedQuaternion={results[4],results[5],results[6],results[7]}
      geluaCapture.getterSequence=sequence; geluaCapture.getterTimestamp=timestamp; geluaCapture.pairComplete=true
      geluaCapture.captureAvailable=true; geluaCapture.captureFailureReason=nil
    else invalidateGeluaCapture('getter capture malformed, non-finite, or mismatched') end
    return unpackValues(results,1,results.n)
  end
  local okSetter,errSetter=pcall(function() OpenXR.setGeluaCameraPosRot=geluaSetterWrapper end)
  if not okSetter or OpenXR.setGeluaCameraPosRot~=geluaSetterWrapper then invalidateGeluaCapture('cannot replace OpenXR.setGeluaCameraPosRot: '..tostring(errSetter)); return false,geluaCapture.captureFailureReason end
  local okGetter,errGetter=pcall(function() OpenXR.getCameraPosRotPredictedXYZXYZW=geluaGetterWrapper end)
  if not okGetter or OpenXR.getCameraPosRotPredictedXYZXYZW~=geluaGetterWrapper then
    if OpenXR.setGeluaCameraPosRot==geluaSetterWrapper then pcall(function() OpenXR.setGeluaCameraPosRot=geluaOriginalSetter end) end
    if OpenXR.getCameraPosRotPredictedXYZXYZW==geluaGetterWrapper then pcall(function() OpenXR.getCameraPosRotPredictedXYZXYZW=geluaOriginalGetter end) end
    invalidateGeluaCapture('cannot replace OpenXR.getCameraPosRotPredictedXYZXYZW: '..tostring(errGetter)); return false,geluaCapture.captureFailureReason
  end
  geluaCapture.captureInstalled=true; geluaCapture.captureAvailable=false; geluaCapture.captureFailureReason='awaiting setter/getter pair'
  log('I','beamngVRControllerPoses','installed transparent GE Lua camera anchor capture wrappers')
  return true
end
function M.stopGeluaCameraAnchorCapture()
  if not geluaCapture.captureInstalled then syncGeluaDiagnostics(); return true end
  if OpenXR and OpenXR.setGeluaCameraPosRot==geluaSetterWrapper then pcall(function() OpenXR.setGeluaCameraPosRot=geluaOriginalSetter end) end
  if OpenXR and OpenXR.getCameraPosRotPredictedXYZXYZW==geluaGetterWrapper then pcall(function() OpenXR.getCameraPosRotPredictedXYZXYZW=geluaOriginalGetter end) end
  geluaCapture.captureInstalled=false; invalidateGeluaCapture('capture stopped')
  syncGeluaDiagnostics(); return true
end
function M.getGeluaCameraAnchorCaptureState() geluaNativeCandidate(captureNow()); syncGeluaDiagnostics(); return geluaCapture end
function M.onPreRender(dtReal,dtSim,dtRaw)
  if not sock then return end; receive(); local now=socketlib.gettime(); local cameraAnchor=beamCameraWorld()
  if not latest or not cameraAnchor or (now-latest.received)*1000>cfg.staleAfterMs then state.leftControllerWorld.valid=false; state.rightControllerWorld.valid=false; return end
  -- Packets from older protocol-2 publishers have no hmd member and retain the
  -- previous camera-anchor behavior rather than being rejected.
  local beamngWorld,candidates=actualHmdWorld(cameraAnchor,latest.hmd)
  local hmdWorld=beamngWorld
  state.selectedCameraSourceMode=cameraSourceMode
  state.finalSelectedCameraWorldTransform=hmdWorld
  state.diagnostics.selectedCameraSourceMode=state.selectedCameraSourceMode
  state.diagnostics.finalSelectedCameraWorldTransform=state.finalSelectedCameraWorldTransform
  state.diagnostics.cameraWorld=hmdWorld
  updateHand('left',latest.left,hmdWorld,now); updateHand('right',latest.right,hmdWorld,now)
  state.beamngOnlyLeftControllerWorld=diagnosticControllerWorld('left',latest.left,candidates.beamngOnly)
  state.beamngOnlyRightControllerWorld=diagnosticControllerWorld('right',latest.right,candidates.beamngOnly)
  state.baselineRigidLeftControllerWorld=candidates.baselineRigidTracking and diagnosticControllerWorld('left',latest.left,candidates.baselineRigidTracking) or nil
  state.baselineRigidRightControllerWorld=candidates.baselineRigidTracking and diagnosticControllerWorld('right',latest.right,candidates.baselineRigidTracking) or nil
  state.hybridLeftControllerWorld=candidates.baselineRigidPositionBeamngRotation and diagnosticControllerWorld('left',latest.left,candidates.baselineRigidPositionBeamngRotation) or nil
  state.hybridRightControllerWorld=candidates.baselineRigidPositionBeamngRotation and diagnosticControllerWorld('right',latest.right,candidates.baselineRigidPositionBeamngRotation) or nil
  state.rebasedHybridLeftControllerWorld=candidates.baselineRigidPositionBeamngRotationRebased and diagnosticControllerWorld('left',latest.left,candidates.baselineRigidPositionBeamngRotationRebased) or nil
  state.rebasedHybridRightControllerWorld=candidates.baselineRigidPositionBeamngRotationRebased and diagnosticControllerWorld('right',latest.right,candidates.baselineRigidPositionBeamngRotationRebased) or nil
  state.movingHybridLeftControllerWorld=candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor and diagnosticControllerWorld('left',latest.left,candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor) or nil
  state.movingHybridRightControllerWorld=candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor and diagnosticControllerWorld('right',latest.right,candidates.baselineRigidPositionBeamngRotationRebasedMovingAnchor) or nil
  state.absoluteMovingLeftControllerWorld=state.movingHybridLeftControllerWorld
  state.absoluteMovingRightControllerWorld=state.movingHybridRightControllerWorld
  state.geluaNativeLeftControllerWorld=candidates.geluaNativeCameraComposition and diagnosticControllerWorld('left',latest.left,candidates.geluaNativeCameraComposition) or nil
  state.geluaNativeRightControllerWorld=candidates.geluaNativeCameraComposition and diagnosticControllerWorld('right',latest.right,candidates.geluaNativeCameraComposition) or nil
  state.diagnostics.beamngOnlyLeftControllerWorld=state.beamngOnlyLeftControllerWorld
  state.diagnostics.beamngOnlyRightControllerWorld=state.beamngOnlyRightControllerWorld
  state.diagnostics.baselineRigidLeftControllerWorld=state.baselineRigidLeftControllerWorld
  state.diagnostics.baselineRigidRightControllerWorld=state.baselineRigidRightControllerWorld
  state.diagnostics.hybridLeftControllerWorld=state.hybridLeftControllerWorld
  state.diagnostics.hybridRightControllerWorld=state.hybridRightControllerWorld
  state.diagnostics.rebasedHybridLeftControllerWorld=state.rebasedHybridLeftControllerWorld
  state.diagnostics.rebasedHybridRightControllerWorld=state.rebasedHybridRightControllerWorld
  state.diagnostics.movingHybridLeftControllerWorld=state.movingHybridLeftControllerWorld
  state.diagnostics.movingHybridRightControllerWorld=state.movingHybridRightControllerWorld
  syncGeluaDiagnostics()
  state.artificialYawRebaseThresholdDegrees=cfg.artificialYawRebaseThresholdDegrees or 0.75
  for _,field in ipairs({'artificialYawRebaseThresholdDegrees','targetWorldFromTrackingOrientation','storedWorldFromTrackingOrientationBeforeRebase','artificialAlignmentDeltaDegrees','artificialYawRebaseTriggered','artificialYawRebaseCount','lastArtificialYawRebaseReason','lastArtificialYawRebaseTime','hmdWorldPositionBeforeArtificialRebase','hmdWorldPositionAfterArtificialRebase','artificialRebasePositionDiscontinuityMetres','rebasedWorldFromTracking','rebasedHybridHmdWorld','rebasedHybridLeftControllerWorld','rebasedHybridRightControllerWorld','rebasedHybridDiagnosticSphereWorldPosition'}) do state.diagnostics[field]=state[field] end
  for _,field in ipairs({'baselineValid','baselineResetReason','baselineBeamngCameraWorld','baselineTrackingHmdRaw','baselineTrackingHmdMapped','baselineWorldFromTracking','currentTrackingHmdRaw','currentTrackingHmdMapped','baselineRigidCandidateHmdWorld','baselineRigidPositionBeamngRotationHmdWorld','baselineRigidPosition','baselineRigidTrackingOrientation','beamngLiveCameraOrientation','selectedHybridOrientation','trackingWorldRight','trackingWorldForward','trackingWorldUp'}) do state.diagnostics[field]=state[field] end
  state.beamngAnchorJumpThreshold=cfg.beamngAnchorJumpMetres or 5.0
  for _,field in ipairs({'baselineOrangeReferenceHmdWorldPosition','currentOrangeReferenceHmdWorldPosition','physicalOffsetFromRecenter','physicalOffsetFromRecenterMagnitude','currentBeamngCameraAnchorPosition','absoluteMovingHmdWorldPosition','absoluteMovingHmdWorldOrientation','absoluteMovingHybridHmdWorld','absoluteMovingLeftControllerWorld','absoluteMovingRightControllerWorld','absoluteMovingDiagnosticSphereWorldPosition','movingReferenceWorldFromTracking','beamngAnchorJumpThreshold','beamngAnchorJumpDetected','movingAnchorJumpDetected','movingAnchorResetReason','movingCandidateHmdWorld','movingHybridHmdWorld','movingHybridLeftControllerWorld','movingHybridRightControllerWorld','movingHybridDiagnosticSphereWorldPosition','movingArtificialYawRebaseCount','movingArtificialYawAlignmentDeltaDegrees','anchorSource','physicalOffsetSource','orientationSource'}) do state.diagnostics[field]=state[field] end
  state.diagnostics.finalControllerWorldPositions={
    left=state.leftControllerWorld.valid and state.leftControllerWorld.position or nil,
    right=state.rightControllerWorld.valid and state.rightControllerWorld.position or nil
  }
  state.diagnostics.finalLeftControllerWorldPosition=state.diagnostics.finalControllerWorldPositions.left
  state.diagnostics.finalRightControllerWorldPosition=state.diagnostics.finalControllerWorldPositions.right
  state.finalLeftControllerWorldPosition=state.diagnostics.finalLeftControllerWorldPosition
  state.finalRightControllerWorldPosition=state.diagnostics.finalRightControllerWorldPosition
  drawDiagnostics(candidates,hmdWorld)
  syncGeluaDiagnostics()
  if now-lastLog>cfg.logIntervalSeconds then
    log('I','beamngVRControllerPoses','fixed-base diagnostics='..dumps(state.diagnostics)); lastLog=now
  end
end
function M.setCameraSourceMode(mode)
  if mode~='beamngOnly' then
    cameraSourceMode='beamngOnly'; state.selectedCameraSourceMode=cameraSourceMode
    log('E','beamngVRControllerPoses','unsupported camera source mode: '..tostring(mode)..'; BeamNG world camera remains selected')
    return false
  end
  cameraSourceMode='beamngOnly'; state.selectedCameraSourceMode=cameraSourceMode
  return true
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
  if mode=='geluaNativeCameraComposition' and not geluaCapture.captureInstalled then
    local reason=geluaCapture.captureFailureReason or 'capture not installed'
    log('E','beamngVRControllerPoses','cannot select geluaNativeCameraComposition: '..reason)
    return false,reason
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
function M.getState() geluaNativeCandidate(captureNow()); syncGeluaDiagnostics(); return state end
function M.onExtensionUnloaded() M.stopGeluaCameraAnchorCapture(); if sock then sock:close(); sock=nil end end
return M
