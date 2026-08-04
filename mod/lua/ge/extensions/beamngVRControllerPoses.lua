-- Stage 1 consumer. UDP receive is non-blocking; debug primitives are submitted every frame.
local M = {}
local sock, socketlib, cfg, latest, lastCounter, lastLog = nil, nil, nil, nil, -1, 0
local state = {leftControllerWorld={valid=false}, rightControllerWorld={valid=false}}

local function qmul(a,b) return {
  a[4]*b[1]+a[1]*b[4]+a[2]*b[3]-a[3]*b[2],
  a[4]*b[2]-a[1]*b[3]+a[2]*b[4]+a[3]*b[1],
  a[4]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[4],
  a[4]*b[4]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3]}
end
local function qinv(q) return {-q[1],-q[2],-q[3],q[4]} end
local function qrot(q,p) local r=qmul(qmul(q,{p[1],p[2],p[3],0}),qinv(q)); return {r[1],r[2],r[3]} end
local function compose(a,b) local p=qrot(a.q,b.p); return {p={a.p[1]+p[1],a.p[2]+p[2],a.p[3]+p[3]},q=qmul(a.q,b.q)} end
local function inverse(a) local q=qinv(a.q); local p=qrot(q,{-a.p[1],-a.p[2],-a.p[3]}); return {p=p,q=q} end
local function mappedPose(p)
  local o,s=cfg.axisOrder,cfg.axisSign
  local v={p.p[o[1]]*s[1]*cfg.metresToBeamNGUnit,p.p[o[2]]*s[2]*cfg.metresToBeamNGUnit,p.p[o[3]]*s[3]*cfg.metresToBeamNGUnit}
  local b=cfg.quaternionBasis
  return {p=v,q=qmul(qmul(b,p.q),qinv(b))}
end
local function beamHmd()
  -- The dump exposes this OpenXR-specific predicted camera pose. Exact return convention must be verified in game.
  local x,y,z,qx,qy,qz,qw=OpenXR.getCameraPosRotPredictedXYZXYZW()
  if not qw then return nil end
  return {p={x,y,z},q={qx,qy,qz,qw}}
end
local function updateHand(name, raw, hmdRaw, hmdWorld, now)
  local out=state[name..'ControllerWorld']; out.valid=false
  if not raw or not raw.valid or not hmdRaw or not hmdRaw.valid then return end
  local rel=compose(inverse(mappedPose(hmdRaw)),mappedPose(raw))
  local offset={p=cfg[name..'PositionOffset'],q=cfg[name..'RotationOffset']}
  local world=compose(hmdWorld,compose(rel,offset))
  out.position=world.p; out.orientation=world.q; out.valid=true; out.updateCounter=latest.counter; out.ageMs=(now-latest.received)*1000
  out.relative=rel
end
local function receive()
  while true do
    local data=sock:receive()
    if not data then break end
    local ok,p=pcall(jsonDecode,data)
    if ok and p and p.v==1 and type(p.counter)=='number' and p.counter>lastCounter then p.received=socketlib.gettime(); latest=p; lastCounter=p.counter end
  end
end
function M.onExtensionLoaded()
  cfg=jsonReadFile('settings/beamngVRControllerPoses.json') or jsonReadFile('/settings/beamngVRControllerPoses.json')
  if not cfg then log('E','beamngVRControllerPoses','configuration not found'); return false end
  socketlib=require('socket'); sock=socketlib.udp(); sock:settimeout(0); assert(sock:setsockname(cfg.listenAddress,cfg.listenPort))
  log('I','beamngVRControllerPoses','listening for pose datagrams on '..cfg.listenAddress..':'..cfg.listenPort)
end
function M.onPreRender(dtReal,dtSim,dtRaw)
  if not sock then return end; receive(); local now=socketlib.gettime(); local h=beamHmd()
  if not latest or not h or (now-latest.received)*1000>cfg.staleAfterMs then state.leftControllerWorld.valid=false; state.rightControllerWorld.valid=false; return end
  updateHand('left',latest.left,latest.hmd,h,now); updateHand('right',latest.right,latest.hmd,h,now)
  local c=ColorF(cfg.sphereColour[1],cfg.sphereColour[2],cfg.sphereColour[3],cfg.sphereColour[4]); local radius=cfg.sphereDiameter/2
  for _,hand in ipairs({'left','right'}) do local p=state[hand..'ControllerWorld']; if p.valid then debugDrawer:drawSphere(vec3(p.position),radius,c) end end
  if now-lastLog>cfg.logIntervalSeconds then
    log('I','beamngVRControllerPoses',string.format('counter=%d age=%.1fms mapping=%s/%s left=%s right=%s',latest.counter,(now-latest.received)*1000,table.concat(cfg.axisOrder,','),table.concat(cfg.axisSign,','),dumps(state.leftControllerWorld),dumps(state.rightControllerWorld))); lastLog=now
  end
end
function M.getState() return state end
function M.onExtensionUnloaded() if sock then sock:close(); sock=nil end end
return M
