-- Stage 1 consumer. UDP receive is non-blocking; debug primitives are submitted every frame.
local M = {}
local sock, socketlib, cfg, latest, lastCounter, lastLog = nil, nil, nil, nil, -1, 0
local state = {leftControllerWorld={valid=false}, rightControllerWorld={valid=false}, diagnostics={}}

local function qmul(a,b) return {
  a[4]*b[1]+a[1]*b[4]+a[2]*b[3]-a[3]*b[2],
  a[4]*b[2]-a[1]*b[3]+a[2]*b[4]+a[3]*b[1],
  a[4]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[4],
  a[4]*b[4]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3]}
end
local function qinv(q) return {-q[1],-q[2],-q[3],q[4]} end
local function qrot(q,p) local r=qmul(qmul(q,{p[1],p[2],p[3],0}),qinv(q)); return {r[1],r[2],r[3]} end
local function compose(a,b) local p=qrot(a.q,b.p); return {p={a.p[1]+p[1],a.p[2]+p[2],a.p[3]+p[3]},q=qmul(a.q,b.q)} end
local function mappedPose(p)
  local o,s=cfg.axisOrder,cfg.axisSign
  local v={p.p[o[1]]*s[1]*cfg.metresToBeamNGUnit,p.p[o[2]]*s[2]*cfg.metresToBeamNGUnit,p.p[o[3]]*s[3]*cfg.metresToBeamNGUnit}
  local b=cfg.quaternionBasis
  return {p=v,q=qmul(qmul(b,p.q),qinv(b))}
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
  local rot=quatToXYZW(getCameraQuat and getCameraQuat())
  if not pos or not rot or not rot[4] then return nil end
  return {p=pos,q=rot}
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
  log('I','beamngVRControllerPoses','listening for pose datagrams on '..cfg.listenAddress..':'..cfg.listenPort)
end
function M.onPreRender(dtReal,dtSim,dtRaw)
  if not sock then return end; receive(); local now=socketlib.gettime(); local cameraWorld=beamCameraWorld()
  if not latest or not cameraWorld or (now-latest.received)*1000>cfg.staleAfterMs then state.leftControllerWorld.valid=false; state.rightControllerWorld.valid=false; return end
  state.diagnostics.cameraWorld=cameraWorld
  updateHand('left',latest.left,cameraWorld,now); updateHand('right',latest.right,cameraWorld,now)
  drawDiagnostics(cameraWorld)
  if now-lastLog>cfg.logIntervalSeconds then
    log('I','beamngVRControllerPoses',string.format('counter=%d age=%.1fms cameraWorld=%s rawLeft=%s rawRight=%s leftWorld=%s rightWorld=%s',latest.counter,(now-latest.received)*1000,dumps(cameraWorld),dumps(latest.left),dumps(latest.right),dumps(state.leftControllerWorld),dumps(state.rightControllerWorld))); lastLog=now
  end
end
function M.getState() return state end
function M.onExtensionUnloaded() if sock then sock:close(); sock=nil end end
return M
