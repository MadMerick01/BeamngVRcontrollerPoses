-- Stage 1 consumer. UDP receive is non-blocking; debug primitives are submitted every frame.
local M = {}
local sock, socketlib, cfg, latest, lastCounter, lastLog = nil, nil, nil, nil, -1, 0
local state = {leftControllerWorld={valid=false}, rightControllerWorld={valid=false}, diagnostics={}}
local hmdBaseline, hmdSpaceKey, previousRawHmd, previousHmdSampleTime = nil, nil, nil, nil

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
local function resetHmdBaseline(reason)
  hmdBaseline=nil; hmdSpaceKey=nil; previousRawHmd=nil; previousHmdSampleTime=nil
  state.diagnostics.hmdBaselineResetReason=reason
end
local function vec3ToTable(v)
  if not v then return nil end
  if type(v)=='table' then return {v.x or v[1], v.y or v[2], v.z or v[3]} end
  return {v.x, v.y, v.z}
end
local function quatToXYZW(q)
  if not q then return nil end
  -- BeamNG's getCameraQuat()/getQuatXYZW convention is explicitly XYZW; keep project-internal XYZW.
  if type(q)=='table' then return {q.x or q[1], q.y or q[2], q.z or q[3], q.w or q[4]} end
  return {q.x, q.y, q.z, q.w}
end
local function beamCameraWorld()
  local pos=vec3ToTable(getCameraPosition and getCameraPosition())
  local rawRot=quatToXYZW(getCameraQuat and getCameraQuat())
  if not pos or not rawRot or not rawRot[4] then return nil end
  -- getCameraQuat() is the world-to-camera/view rotation.  The rigid-transform
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
local function actualHmdWorld(cameraAnchor, hmd)
  if not hmd or not hmd.valid or not hmd.p then return nil end
  local key=tostring(hmd.session or '')..':'..tostring(hmd.base or '')
  local jump=cfg.hmdRecenterJumpMetres or 0.35
  local sampleWentBack=previousHmdSampleTime and hmd.sampleTime and hmd.sampleTime<previousHmdSampleTime
  if hmdSpaceKey and hmdSpaceKey~=key then resetHmdBaseline('tracking space changed')
  elseif sampleWentBack then resetHmdBaseline('sample time reset')
  elseif previousRawHmd and distance(previousRawHmd,hmd.p)>jump then resetHmdBaseline('HMD pose discontinuity') end
  if not hmdBaseline then hmdBaseline={hmd.p[1],hmd.p[2],hmd.p[3]}; hmdSpaceKey=key end
  previousRawHmd={hmd.p[1],hmd.p[2],hmd.p[3]}; previousHmdSampleTime=hmd.sampleTime

  -- The baseline removes standing height/tracking-origin placement.  Apply the
  -- confirmed OpenXR -> BeamNG mapping (x,y,z) -> (x,-z,y), then rotate the
  -- room-scale displacement by the corrected camera-to-world quaternion.
  local rawDelta={hmd.p[1]-hmdBaseline[1],hmd.p[2]-hmdBaseline[2],hmd.p[3]-hmdBaseline[3]}
  local mappedDelta=mappedPosition(rawDelta)
  local worldDelta=qrot(cameraAnchor.q,mappedDelta)
  local actual={p={cameraAnchor.p[1]+worldDelta[1],cameraAnchor.p[2]+worldDelta[2],cameraAnchor.p[3]+worldDelta[3]},q=cameraAnchor.q}
  state.diagnostics.rawHmdPose=hmd
  state.diagnostics.hmdBaseline={hmdBaseline[1],hmdBaseline[2],hmdBaseline[3]}
  state.diagnostics.mappedHmdDelta=mappedDelta
  state.diagnostics.beamngCameraAnchor=cameraAnchor
  state.diagnostics.actualHmdWorldPosition=actual.p
  return actual
end
local function receive()
  while true do
    local data=sock:receive()
    if not data then break end
    local ok,p=pcall(jsonDecode,data)
    if ok and p and p.v==2 and type(p.counter)=='number' and p.counter>lastCounter then p.received=socketlib.gettime(); latest=p; lastCounter=p.counter end
  end
end
local function drawDiagnostics(cameraWorld)
  if cfg.cameraTestSphere and cfg.cameraTestSphere.enabled then
    local localPos=cfg.cameraTestSphere.offset or {0,1,0}
    local world=compose(cameraWorld,{p=localPos,q={0,0,0,1}})
    state.diagnostics.cameraTestSphereWorld=world.p
    debugDrawer:drawSphere(vec3(world.p),(cfg.cameraTestSphere.diameter or cfg.sphereDiameter)/2,ColorF(1,0,0,1))
  end
  local c=ColorF(cfg.sphereColour[1],cfg.sphereColour[2],cfg.sphereColour[3],cfg.sphereColour[4]); local radius=cfg.sphereDiameter/2
  for _,hand in ipairs({'left','right'}) do local p=state[hand..'ControllerWorld']; if p.valid then debugDrawer:drawSphere(vec3(p.position),radius,c) end end
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
  local hmdWorld=actualHmdWorld(cameraAnchor,latest.hmd) or cameraAnchor
  state.diagnostics.cameraWorld=hmdWorld
  updateHand('left',latest.left,hmdWorld,now); updateHand('right',latest.right,hmdWorld,now)
  drawDiagnostics(hmdWorld)
  if now-lastLog>cfg.logIntervalSeconds then
    log('I','beamngVRControllerPoses',string.format('counter=%d age=%.1fms cameraAnchor=%s rawHmd=%s hmdBaseline=%s mappedHmdDelta=%s actualHmdWorld=%s rawLeft=%s rawRight=%s leftWorld=%s rightWorld=%s',latest.counter,(now-latest.received)*1000,dumps(cameraAnchor),dumps(latest.hmd),dumps(state.diagnostics.hmdBaseline),dumps(state.diagnostics.mappedHmdDelta),dumps(hmdWorld),dumps(latest.left),dumps(latest.right),dumps(state.leftControllerWorld),dumps(state.rightControllerWorld))); lastLog=now
  end
end
function M.resetHmdBaseline() resetHmdBaseline('explicit reset') end
function M.getState() return state end
function M.onExtensionUnloaded() if sock then sock:close(); sock=nil end end
return M
