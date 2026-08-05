#define XR_USE_PLATFORM_WIN32
#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>
#include <winsock2.h>
#include <windows.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

namespace {
constexpr XrSpaceLocationFlags kValid = XR_SPACE_LOCATION_POSITION_VALID_BIT |
    XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;
enum class Hand : uint8_t { none, left, right };

struct Action {
  XrActionType type{};
  std::string name, localized;
  XrPath subaction[16]{};
  uint32_t count{};
  XrInstance instance{};
};
struct Dispatch {
  PFN_xrGetInstanceProcAddr gipa{};
  PFN_xrDestroyInstance destroyInstance{};
  PFN_xrCreateSession createSession{};
  PFN_xrDestroySession destroySession{};
  PFN_xrStringToPath stringToPath{};
  PFN_xrCreateActionSet createActionSet{};
  PFN_xrDestroyActionSet destroyActionSet{};
  PFN_xrCreateAction createAction{};
  PFN_xrDestroyAction destroyAction{};
  PFN_xrCreateActionSpace createActionSpace{};
  PFN_xrCreateReferenceSpace createReferenceSpace{};
  PFN_xrDestroySpace destroySpace{};
  PFN_xrSyncActions syncActions{};
  PFN_xrGetActionStatePose getActionStatePose{};
  PFN_xrLocateSpace locateSpace{};
  XrPath leftPath{}, rightPath{};
};

// A destroy first sets active=false and removes this session from the published
// registry. It then waits for calls which already acquired an InFlight reference.
// Thus snapshots never turn an OpenXR handle into an unsafe, unowned raw pointer.
struct SessionLifetime {
  std::atomic<bool> active{true};
  std::atomic<uint32_t> inFlight{0};
};
struct Session {
  XrInstance instance{};
  XrSpace layerView{};  // Owned by the layer, never returned to the application.
  Dispatch dispatch{};
  std::shared_ptr<SessionLifetime> lifetime;
};
struct Space {
  XrSession session{};
  XrAction action{};
  XrPath subaction{};
  Hand hand{};
  std::shared_ptr<SessionLifetime> lifetime;
};
struct Registry {
  std::unordered_map<XrInstance, Dispatch> dispatches;
  std::unordered_map<XrSession, Session> sessions;
  std::unordered_map<XrAction, Action> actions;
  std::unordered_map<XrActionSet, XrInstance> actionSets;
  std::unordered_map<XrSpace, Space> spaces;
};

std::mutex writerMutex;
Registry registry;
std::atomic<std::shared_ptr<const Registry>> publishedRegistry{
    std::make_shared<const Registry>()};

void publishRegistryLocked() {
  // All allocation and container copying is confined to lifecycle hooks.
  publishedRegistry.store(std::make_shared<const Registry>(registry),
                          std::memory_order_release);
}
Dispatch *dispatchForLocked(XrInstance instance) {
  auto it = registry.dispatches.find(instance);
  return it == registry.dispatches.end() ? nullptr : &it->second;
}
Dispatch *dispatchForLocked(XrSession session) {
  auto it = registry.sessions.find(session);
  return it == registry.sessions.end() ? nullptr : &it->second.dispatch;
}

class InFlight {
 public:
  explicit InFlight(std::shared_ptr<SessionLifetime> lifetime)
      : lifetime_(std::move(lifetime)) {
    if (!lifetime_ || !lifetime_->active.load(std::memory_order_acquire)) return;
    lifetime_->inFlight.fetch_add(1, std::memory_order_acq_rel);
    if (lifetime_->active.load(std::memory_order_acquire)) acquired_ = true;
    else lifetime_->inFlight.fetch_sub(1, std::memory_order_release);
  }
  ~InFlight() {
    if (acquired_) lifetime_->inFlight.fetch_sub(1, std::memory_order_release);
  }
  explicit operator bool() const { return acquired_; }
 private:
  std::shared_ptr<SessionLifetime> lifetime_;
  bool acquired_{};
};

struct Pose { XrPosef pose{{0,0,0,1},{0,0,0}}; XrSpaceLocationFlags flags{}; bool valid{}; };
struct Sample { uint64_t sequence{}; XrSession session{}; XrSpace base{}; XrTime time{}; Hand hand{}; XrSpace candidate{}; bool destroySession{}; bool destroySpace{}; Pose left{}, right{}; };

// A bounded MPSC sequence queue avoids the data race in an ordinary double
// buffer when runtimes locate spaces concurrently. Producers never wait: a full
// queue merely drops a diagnostic sample. No hot-path allocation is performed.
constexpr size_t kQueueSize = 64;
// A hand remains published for a short 125 ms grace window so BeamNG/VDXR
// packets that alternate left/right locates combine into one stable snapshot
// without latching stale ghost controllers indefinitely.
constexpr auto kHandFreshness = std::chrono::milliseconds(125);
struct SampleCell { std::atomic<uint64_t> sequence{}; Sample sample{}; };
std::array<SampleCell, kQueueSize> sampleQueue{};
std::atomic<uint64_t> enqueuePosition{0};
uint64_t dequeuePosition = 0;
std::atomic<uint64_t> sampleSequence{0};
std::atomic<bool> running{false};
std::thread publisher;
std::mutex publisherMutex;

void initializeQueue() {
  for (uint64_t i=0; i<kQueueSize; ++i) sampleQueue[i].sequence.store(i);
}
void publish(const Sample& source) {
  uint64_t position = enqueuePosition.load(std::memory_order_relaxed);
  for (;;) {
    auto& cell = sampleQueue[position % kQueueSize];
    const uint64_t sequence = cell.sequence.load(std::memory_order_acquire);
    const intptr_t difference = static_cast<intptr_t>(sequence - position);
    if (difference == 0) {
      if (enqueuePosition.compare_exchange_weak(position, position + 1,
            std::memory_order_relaxed)) {
        cell.sample = source;
        cell.sample.sequence = sampleSequence.fetch_add(1, std::memory_order_relaxed) + 1;
        cell.sequence.store(position + 1, std::memory_order_release);
        return;
      }
    } else if (difference < 0) {
      return;
    } else {
      position = enqueuePosition.load(std::memory_order_relaxed);
    }
  }
}
bool consume(Sample& sample) {
  auto& cell = sampleQueue[dequeuePosition % kQueueSize];
  if (cell.sequence.load(std::memory_order_acquire) != dequeuePosition + 1) return false;
  sample = cell.sample;
  cell.sequence.store(dequeuePosition + kQueueSize, std::memory_order_release);
  ++dequeuePosition;
  return true;
}

XrQuaternionf multiply(const XrQuaternionf& a, const XrQuaternionf& b) {
  // OpenXR stores quaternion components in x, y, z, w order.
  return {a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
          a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
          a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,
          a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z};
}
XrPosef inverse(const XrPosef& p) {
  auto q=p.orientation; XrQuaternionf qi{-q.x,-q.y,-q.z,q.w}; auto v=p.position;
  XrQuaternionf t{-v.x,-v.y,-v.z,0.0f}, qc{-qi.x,-qi.y,-qi.z,qi.w};
  auto r=multiply(multiply(qi,t),qc); return {qi,{r.x,r.y,r.z}};
}
XrPosef compose(const XrPosef& a,const XrPosef& b) {
  auto q=multiply(a.orientation,b.orientation);
  XrQuaternionf ac{-a.orientation.x,-a.orientation.y,-a.orientation.z,a.orientation.w};
  XrQuaternionf translation{b.position.x,b.position.y,b.position.z,0.0f};
  auto t=multiply(multiply(a.orientation,translation),ac);
  return {q,{a.position.x+t.x,a.position.y+t.y,a.position.z+t.z}};
}

struct CandidatePose { XrSpace candidate{}; Pose pose{}; XrTime xrTime{}; std::chrono::steady_clock::time_point received{}; uint64_t updates{}; bool everValid{}; };
struct HandCache { std::array<CandidatePose,16> candidates{}; XrSpace selected{}; uint64_t updates{}; };
struct CombinedCache { XrSession session{}; XrSpace base{}; HandCache left{}, right{}; };

CandidatePose* candidateSlot(HandCache& hand, XrSpace candidate) {
  CandidatePose* empty=nullptr; CandidatePose* oldest=&hand.candidates[0];
  for(auto& slot:hand.candidates){
    if(slot.candidate==candidate)return &slot;
    if(slot.candidate==XR_NULL_HANDLE&&!empty)empty=&slot;
    if(slot.received<oldest->received)oldest=&slot;
  }
  auto* slot=empty?empty:oldest; *slot={}; slot->candidate=candidate; return slot;
}
void clearCandidate(HandCache& hand, XrSpace candidate) {
  for(auto& slot:hand.candidates)if(slot.candidate==candidate)slot={};
  if(hand.selected==candidate)hand.selected=XR_NULL_HANDLE;
}
Pose currentPose(HandCache& hand, std::chrono::steady_clock::time_point now, uint64_t& ageMs, XrSpace& selected, uint64_t& updates) {
  auto fresh=[&](const CandidatePose& c){return c.candidate!=XR_NULL_HANDLE&&c.everValid&&now-c.received<=kHandFreshness;};
  CandidatePose* chosen=nullptr;
  for(auto& slot:hand.candidates)if(slot.candidate==hand.selected&&fresh(slot)){chosen=&slot;break;}
  if(!chosen)for(auto& slot:hand.candidates)if(fresh(slot)&&(!chosen||slot.received>chosen->received))chosen=&slot;
  Pose out{}; ageMs=0; selected=XR_NULL_HANDLE; updates=hand.updates;
  if(chosen){hand.selected=chosen->candidate; out=chosen->pose; out.valid=true; ageMs=(uint64_t)std::chrono::duration_cast<std::chrono::milliseconds>(now-chosen->received).count(); selected=chosen->candidate;}
  else hand.selected=XR_NULL_HANDLE;
  return out;
}

void publisherMain() {
  WSADATA wd{}; if(WSAStartup(MAKEWORD(2,2),&wd)!=0) return;
  SOCKET fd=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
  sockaddr_in to{}; to.sin_family=AF_INET; to.sin_port=htons(44441); to.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  uint64_t seen=0; auto lastLog=std::chrono::steady_clock::now(); Sample sample{}; CombinedCache cache{};
  while(running.load(std::memory_order_acquire)) {
    bool haveSample=consume(sample);
    auto now=std::chrono::steady_clock::now();
    if(haveSample) {
      if(sample.destroySession){cache={}; continue;}
      if(sample.destroySpace){clearCandidate(cache.left,sample.candidate);clearCandidate(cache.right,sample.candidate);continue;}
      if(cache.session!=sample.session||cache.base!=sample.base){cache={};cache.session=sample.session;cache.base=sample.base;}
      auto& handCache=sample.hand==Hand::left?cache.left:cache.right;
      Pose incoming=sample.hand==Hand::left?sample.left:sample.right;
      if(incoming.valid){auto* slot=candidateSlot(handCache,sample.candidate);slot->pose=incoming;slot->xrTime=sample.time;slot->received=now;slot->updates++;slot->everValid=true;handCache.updates++;if(handCache.selected==XR_NULL_HANDLE)handCache.selected=sample.candidate;}
      uint64_t leftAge=0,rightAge=0,leftUpdates=0,rightUpdates=0; XrSpace leftCandidate{},rightCandidate{};
      Pose left=currentPose(cache.left,now,leftAge,leftCandidate,leftUpdates), right=currentPose(cache.right,now,rightAge,rightCandidate,rightUpdates);
      char out[1200],l[420],r[420];
      auto hand=[](const Pose&p,uint64_t age,XrSpace candidate,uint64_t updates,char*b,size_t z){return std::snprintf(b,z,"{\"valid\":%s,\"flags\":%llu,\"ageMs\":%llu,\"candidate\":%llu,\"updates\":%llu,\"p\":[%.9g,%.9g,%.9g],\"q\":[%.9g,%.9g,%.9g,%.9g]}",p.valid?"true":"false",(unsigned long long)p.flags,(unsigned long long)age,(unsigned long long)candidate,(unsigned long long)updates,p.pose.position.x,p.pose.position.y,p.pose.position.z,p.pose.orientation.x,p.pose.orientation.y,p.pose.orientation.z,p.pose.orientation.w);};
      hand(left,leftAge,leftCandidate,leftUpdates,l,sizeof l); hand(right,rightAge,rightCandidate,rightUpdates,r,sizeof r);
      int len=std::snprintf(out,sizeof out,"{\"v\":2,\"counter\":%llu,\"xrTime\":%lld,\"source\":\"openxr-api-layer\",\"left\":%s,\"right\":%s}",(unsigned long long)sample.sequence,(long long)sample.time,l,r);
      if(fd!=INVALID_SOCKET && len>0) sendto(fd,out,len,0,(sockaddr*)&to,sizeof to); seen=sample.sequence;
    }
    if(now-lastLog>std::chrono::seconds(5)) {
      uint64_t la=0,ra=0,lu=0,ru=0;XrSpace lc{},rc{};Pose lp=currentPose(cache.left,now,la,lc,lu),rp=currentPose(cache.right,now,ra,rc,ru);
      char path[MAX_PATH]{}; DWORD count=GetTempPathA(MAX_PATH,path);
      if(count&&count+28<MAX_PATH){std::strcat(path,"BeamNGVRPosesLayer.log");if(FILE*f=std::fopen(path,"a")){std::fprintf(f,"published=%llu left valid=%d left age=%llu left selected candidate=%llu left update counter=%llu right valid=%d right age=%llu right selected candidate=%llu right update counter=%llu\n",(unsigned long long)seen,lp.valid,(unsigned long long)la,(unsigned long long)lc,(unsigned long long)lu,rp.valid,(unsigned long long)ra,(unsigned long long)rc,(unsigned long long)ru);std::fclose(f);}}
      lastLog=now;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  if(fd!=INVALID_SOCKET) closesocket(fd); WSACleanup();
}
void startPublisher(){std::lock_guard lock(publisherMutex);if(!running.exchange(true)){enqueuePosition.store(0);dequeuePosition=0;initializeQueue();publisher=std::thread(publisherMain);}}
void stopPublisher(){std::lock_guard lock(publisherMutex);if(running.exchange(false)&&publisher.joinable())publisher.join();}

XRAPI_ATTR XrResult XRAPI_CALL layerDestroyInstance(XrInstance i){
  PFN_xrDestroyInstance fn{}; bool stop{};
  {std::lock_guard lock(writerMutex);auto*d=dispatchForLocked(i);if(!d)return XR_ERROR_HANDLE_INVALID;fn=d->destroyInstance;
   for(auto it=registry.actions.begin();it!=registry.actions.end();)it=it->second.instance==i?registry.actions.erase(it):++it;
   registry.dispatches.erase(i);publishRegistryLocked();stop=registry.dispatches.empty();}
  auto result=fn(i); if(stop)stopPublisher(); return result;
}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateSession(XrInstance i,const XrSessionCreateInfo*c,XrSession*s){
  Dispatch d{};{std::lock_guard lock(writerMutex);auto*p=dispatchForLocked(i);if(!p)return XR_ERROR_HANDLE_INVALID;d=*p;}
  auto result=d.createSession(i,c,s);if(XR_FAILED(result))return result;
  XrSpace view=XR_NULL_HANDLE;XrReferenceSpaceCreateInfo info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};info.referenceSpaceType=XR_REFERENCE_SPACE_TYPE_VIEW;info.poseInReferenceSpace.orientation.w=1.0f;
  if(!d.createReferenceSpace||XR_FAILED(d.createReferenceSpace(*s,&info,&view)))view=XR_NULL_HANDLE;
  {std::lock_guard lock(writerMutex);registry.sessions[*s]={i,view,d,std::make_shared<SessionLifetime>()};publishRegistryLocked();}
  return result;
}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroySession(XrSession s){
  Session session{};
  {std::lock_guard lock(writerMutex);auto it=registry.sessions.find(s);if(it==registry.sessions.end())return XR_ERROR_HANDLE_INVALID;session=it->second;session.lifetime->active.store(false,std::memory_order_release);
   for(auto p=registry.spaces.begin();p!=registry.spaces.end();)p=p->second.session==s?registry.spaces.erase(p):++p;registry.sessions.erase(it);publishRegistryLocked();Sample sample{};sample.session=s;sample.destroySession=true;publish(sample);}
  while(session.lifetime->inFlight.load(std::memory_order_acquire)!=0)std::this_thread::yield();
  if(session.layerView!=XR_NULL_HANDLE)session.dispatch.destroySpace(session.layerView);
  return session.dispatch.destroySession(s);
}
XRAPI_ATTR XrResult XRAPI_CALL layerStringToPath(XrInstance i,const char*str,XrPath*p){Dispatch d{};{std::lock_guard lock(writerMutex);auto*q=dispatchForLocked(i);if(!q)return XR_ERROR_HANDLE_INVALID;d=*q;}auto r=d.stringToPath(i,str,p);if(XR_SUCCEEDED(r)){std::lock_guard lock(writerMutex);auto*q=dispatchForLocked(i);if(q){if(!std::strcmp(str,"/user/hand/left"))q->leftPath=*p;if(!std::strcmp(str,"/user/hand/right"))q->rightPath=*p;for(auto& [_,session]:registry.sessions)if(session.instance==i)session.dispatch=*q;publishRegistryLocked();}}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateActionSet(XrInstance i,const XrActionSetCreateInfo*c,XrActionSet*out){Dispatch d{};{std::lock_guard lock(writerMutex);auto*p=dispatchForLocked(i);if(!p)return XR_ERROR_HANDLE_INVALID;d=*p;}auto r=d.createActionSet(i,c,out);if(XR_SUCCEEDED(r)){std::lock_guard lock(writerMutex);registry.actionSets[*out]=i;publishRegistryLocked();}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroyActionSet(XrActionSet set){PFN_xrDestroyActionSet fn{};{std::lock_guard lock(writerMutex);auto it=registry.actionSets.find(set);if(it==registry.actionSets.end())return XR_ERROR_HANDLE_INVALID;auto*d=dispatchForLocked(it->second);fn=d->destroyActionSet;registry.actionSets.erase(it);publishRegistryLocked();}return fn(set);}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateAction(XrActionSet set,const XrActionCreateInfo*c,XrAction*a){Dispatch d{};XrInstance owner{};{std::lock_guard lock(writerMutex);auto it=registry.actionSets.find(set);if(it==registry.actionSets.end())return XR_ERROR_HANDLE_INVALID;owner=it->second;d=*dispatchForLocked(owner);}auto r=d.createAction(set,c,a);if(XR_SUCCEEDED(r)){Action m{};m.type=c->actionType;m.name=c->actionName;m.localized=c->localizedActionName;m.count=(std::min)(c->countSubactionPaths,16u);std::copy_n(c->subactionPaths,m.count,m.subaction);m.instance=owner;std::lock_guard lock(writerMutex);registry.actions[*a]=std::move(m);publishRegistryLocked();}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroyAction(XrAction a){PFN_xrDestroyAction fn{};{std::lock_guard lock(writerMutex);auto it=registry.actions.find(a);if(it==registry.actions.end())return XR_ERROR_HANDLE_INVALID;fn=dispatchForLocked(it->second.instance)->destroyAction;registry.actions.erase(it);for(auto&[space,s]:registry.spaces)if(s.action==a){Sample sample{};sample.session=s.session;sample.hand=s.hand;sample.candidate=space;sample.destroySpace=true;publish(sample);s.hand=Hand::none;}publishRegistryLocked();}return fn(a);}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateActionSpace(XrSession s,const XrActionSpaceCreateInfo*c,XrSpace*out){Dispatch d{};{std::lock_guard lock(writerMutex);auto*p=dispatchForLocked(s);if(!p)return XR_ERROR_HANDLE_INVALID;d=*p;}auto r=d.createActionSpace(s,c,out);if(XR_SUCCEEDED(r)){std::lock_guard lock(writerMutex);Hand h=c->subactionPath==d.leftPath?Hand::left:c->subactionPath==d.rightPath?Hand::right:Hand::none;auto a=registry.actions.find(c->action);if(a==registry.actions.end()||a->second.type!=XR_ACTION_TYPE_POSE_INPUT)h=Hand::none;registry.spaces[*out]={s,c->action,c->subactionPath,h,std::make_shared<SessionLifetime>()};publishRegistryLocked();}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateReferenceSpace(XrSession s,const XrReferenceSpaceCreateInfo*c,XrSpace*out){Dispatch d{};{std::lock_guard lock(writerMutex);auto*p=dispatchForLocked(s);if(!p)return XR_ERROR_HANDLE_INVALID;d=*p;}auto r=d.createReferenceSpace(s,c,out);if(XR_SUCCEEDED(r)){std::lock_guard lock(writerMutex);registry.spaces[*out]={s,XR_NULL_HANDLE,XR_NULL_PATH,Hand::none,std::make_shared<SessionLifetime>()};publishRegistryLocked();}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroySpace(XrSpace s){PFN_xrDestroySpace fn{};std::shared_ptr<SessionLifetime> lifetime;{std::lock_guard lock(writerMutex);auto it=registry.spaces.find(s);if(it==registry.spaces.end())return XR_ERROR_HANDLE_INVALID;fn=dispatchForLocked(it->second.session)->destroySpace;lifetime=it->second.lifetime;lifetime->active.store(false,std::memory_order_release);XrSession session=it->second.session;Hand hand=it->second.hand;registry.spaces.erase(it);publishRegistryLocked();Sample sample{};sample.session=session;sample.hand=hand;sample.candidate=s;sample.destroySpace=true;publish(sample);}
  while(lifetime->inFlight.load(std::memory_order_acquire)!=0)std::this_thread::yield();return fn(s);
}
XRAPI_ATTR XrResult XRAPI_CALL layerSyncActions(XrSession s,const XrActionsSyncInfo*c){Dispatch d{};{std::lock_guard lock(writerMutex);auto*p=dispatchForLocked(s);if(!p)return XR_ERROR_HANDLE_INVALID;d=*p;}return d.syncActions(s,c);}
XRAPI_ATTR XrResult XRAPI_CALL layerGetActionStatePose(XrSession s,const XrActionStateGetInfo*c,XrActionStatePose*state){
  Dispatch d{};{std::lock_guard lock(writerMutex);auto*p=dispatchForLocked(s);if(!p)return XR_ERROR_HANDLE_INVALID;d=*p;}
  auto r=d.getActionStatePose(s,c,state);
  if(XR_SUCCEEDED(r)&&c&&state&&!state->isActive){std::lock_guard lock(writerMutex);for(auto&[space,meta]:registry.spaces)if(meta.session==s&&meta.action==c->action&&(c->subactionPath==XR_NULL_PATH||meta.subaction==c->subactionPath)){Sample sample{};sample.session=s;sample.hand=meta.hand;sample.candidate=space;sample.destroySpace=true;publish(sample);}}
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL layerLocateSpace(XrSpace space,XrSpace base,XrTime time,XrSpaceLocation*loc){
  // Exact steady-state operations: one atomic shared_ptr load/refcount, immutable
  // hash lookups, atomic in-flight bookkeeping, downstream locate(s), pose math,
  // and a bounded non-waiting queue publication. No mutex, allocation or I/O.
  auto snapshot=publishedRegistry.load(std::memory_order_acquire);
  auto si=snapshot->spaces.find(space);if(si==snapshot->spaces.end())return XR_ERROR_HANDLE_INVALID;
  auto session=snapshot->sessions.find(si->second.session);if(session==snapshot->sessions.end())return XR_ERROR_HANDLE_INVALID;
  InFlight sessionInFlight(session->second.lifetime);if(!sessionInFlight)return XR_ERROR_HANDLE_INVALID;
  InFlight spaceInFlight(si->second.lifetime);if(!spaceInFlight)return XR_ERROR_HANDLE_INVALID;
  const Dispatch d=session->second.dispatch;auto result=d.locateSpace(space,base,time,loc);
  if(XR_FAILED(result)||si->second.hand==Hand::none)return result;
  Sample sample{};sample.session=si->second.session;sample.base=base;sample.time=time;
  XrSpaceLocation head{XR_TYPE_SPACE_LOCATION};XrResult headResult=XR_ERROR_HANDLE_INVALID;
  if(session->second.layerView!=XR_NULL_HANDLE)headResult=d.locateSpace(session->second.layerView,base,time,&head);
  Pose pose{};pose.flags=loc->locationFlags;pose.valid=XR_SUCCEEDED(headResult)&&((loc->locationFlags&kValid)==kValid)&&((head.locationFlags&kValid)==kValid);if(pose.valid)pose.pose=compose(inverse(head.pose),loc->pose);
  sample.hand=si->second.hand;sample.candidate=space;if(si->second.hand==Hand::left)sample.left=pose;else sample.right=pose;publish(sample);return result;
}

XRAPI_ATTR XrResult XRAPI_CALL layerGipa(XrInstance i,const char*n,PFN_xrVoidFunction*f){
  if(!std::strcmp(n,"xrDestroyInstance"))*f=(PFN_xrVoidFunction)layerDestroyInstance;else if(!std::strcmp(n,"xrCreateActionSet"))*f=(PFN_xrVoidFunction)layerCreateActionSet;else if(!std::strcmp(n,"xrDestroyActionSet"))*f=(PFN_xrVoidFunction)layerDestroyActionSet;else if(!std::strcmp(n,"xrCreateSession"))*f=(PFN_xrVoidFunction)layerCreateSession;else if(!std::strcmp(n,"xrDestroySession"))*f=(PFN_xrVoidFunction)layerDestroySession;else if(!std::strcmp(n,"xrStringToPath"))*f=(PFN_xrVoidFunction)layerStringToPath;else if(!std::strcmp(n,"xrCreateAction"))*f=(PFN_xrVoidFunction)layerCreateAction;else if(!std::strcmp(n,"xrDestroyAction"))*f=(PFN_xrVoidFunction)layerDestroyAction;else if(!std::strcmp(n,"xrCreateActionSpace"))*f=(PFN_xrVoidFunction)layerCreateActionSpace;else if(!std::strcmp(n,"xrCreateReferenceSpace"))*f=(PFN_xrVoidFunction)layerCreateReferenceSpace;else if(!std::strcmp(n,"xrDestroySpace"))*f=(PFN_xrVoidFunction)layerDestroySpace;else if(!std::strcmp(n,"xrSyncActions"))*f=(PFN_xrVoidFunction)layerSyncActions;else if(!std::strcmp(n,"xrGetActionStatePose"))*f=(PFN_xrVoidFunction)layerGetActionStatePose;else if(!std::strcmp(n,"xrLocateSpace"))*f=(PFN_xrVoidFunction)layerLocateSpace;else{auto snapshot=publishedRegistry.load(std::memory_order_acquire);auto d=snapshot->dispatches.find(i);return d==snapshot->dispatches.end()?XR_ERROR_HANDLE_INVALID:d->second.gipa(i,n,f);}return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateInstance(const XrInstanceCreateInfo*c,const XrApiLayerCreateInfo*li,XrInstance*i){auto next=*li;next.nextInfo=li->nextInfo->next;auto r=li->nextInfo->nextCreateApiLayerInstance(c,&next,i);if(XR_FAILED(r))return r;Dispatch d{};d.gipa=li->nextInfo->nextGetInstanceProcAddr;auto gp=[&](const char*n,auto&p){d.gipa(*i,n,reinterpret_cast<PFN_xrVoidFunction*>(&p));};gp("xrDestroyInstance",d.destroyInstance);gp("xrCreateActionSet",d.createActionSet);gp("xrDestroyActionSet",d.destroyActionSet);gp("xrCreateSession",d.createSession);gp("xrDestroySession",d.destroySession);gp("xrStringToPath",d.stringToPath);gp("xrCreateAction",d.createAction);gp("xrDestroyAction",d.destroyAction);gp("xrCreateActionSpace",d.createActionSpace);gp("xrCreateReferenceSpace",d.createReferenceSpace);gp("xrDestroySpace",d.destroySpace);gp("xrSyncActions",d.syncActions);gp("xrGetActionStatePose",d.getActionStatePose);gp("xrLocateSpace",d.locateSpace);{std::lock_guard lock(writerMutex);registry.dispatches[*i]=d;publishRegistryLocked();}startPublisher();return r;}
}
extern "C" __declspec(dllexport) XRAPI_ATTR XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(const XrNegotiateLoaderInfo*loader,const char*,XrNegotiateApiLayerRequest*request){if(!loader||!request||loader->structType!=XR_LOADER_INTERFACE_STRUCT_LOADER_INFO||request->structType!=XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST||loader->maxInterfaceVersion<XR_CURRENT_LOADER_API_LAYER_VERSION)return XR_ERROR_INITIALIZATION_FAILED;request->layerInterfaceVersion=XR_CURRENT_LOADER_API_LAYER_VERSION;request->layerApiVersion=XR_CURRENT_API_VERSION;request->getInstanceProcAddr=layerGipa;request->createApiLayerInstance=layerCreateInstance;return XR_SUCCESS;}
