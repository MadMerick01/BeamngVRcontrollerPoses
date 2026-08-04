#define XR_USE_PLATFORM_WIN32
#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>
#include <winsock2.h>
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

namespace {
constexpr XrSpaceLocationFlags kValid = XR_SPACE_LOCATION_POSITION_VALID_BIT |
    XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;
enum class Hand : uint8_t { none, left, right };
struct Action { XrActionType type{}; std::string name, localized; XrPath subaction[16]{}; uint32_t count{}; XrInstance instance{}; };
struct Space { XrSession session{}; XrAction action{}; XrPath subaction{}; Hand hand{}; bool view{}; };
struct Session { XrInstance instance{}; XrSpace view{}; };
struct Dispatch { PFN_xrGetInstanceProcAddr gipa{}; PFN_xrDestroyInstance destroyInstance{};
  PFN_xrCreateSession createSession{}; PFN_xrDestroySession destroySession{};
  PFN_xrStringToPath stringToPath{}; PFN_xrCreateActionSet createActionSet{}; PFN_xrDestroyActionSet destroyActionSet{}; PFN_xrCreateAction createAction{};
  PFN_xrDestroyAction destroyAction{}; PFN_xrCreateActionSpace createActionSpace{};
  PFN_xrCreateReferenceSpace createReferenceSpace{}; PFN_xrDestroySpace destroySpace{};
  PFN_xrSyncActions syncActions{}; PFN_xrLocateSpace locateSpace{}; XrPath leftPath{}, rightPath{}; };
struct Pose { XrPosef pose{{0,0,0,1},{0,0,0}}; XrSpaceLocationFlags flags{}; bool valid{}; };
struct Sample { uint64_t sequence{}; XrSession session{}; XrSpace base{}; XrTime time{}; Pose left{}, right{}; };

std::mutex mu;
std::unordered_map<XrInstance, Dispatch> dispatches;
std::unordered_map<XrSession, Session> sessions;
std::unordered_map<XrAction, Action> actions;
std::unordered_map<XrActionSet, XrInstance> actionSets;
std::unordered_map<XrSpace, Space> spaces;
std::atomic<bool> running{false};
std::atomic<uint64_t> published{0};
Sample slots[2]{};
std::thread publisher;

Dispatch *dispatchFor(XrInstance i) { auto it=dispatches.find(i); return it==dispatches.end()?nullptr:&it->second; }
Dispatch *dispatchFor(XrSession s) { auto it=sessions.find(s); return it==sessions.end()?nullptr:dispatchFor(it->second.instance); }

XrPosef inverse(const XrPosef& p) {
  auto q=p.orientation; XrQuaternionf qi{-q.x,-q.y,-q.z,q.w}; auto v=p.position;
  XrQuaternionf t{ -v.x,-v.y,-v.z,0 }, qc{-qi.x,-qi.y,-qi.z,qi.w};
  auto mul=[](auto a,auto b){return XrQuaternionf{a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z};};
  auto r=mul(mul(qi,t),qc); return {qi,{r.x,r.y,r.z}};
}
XrPosef compose(const XrPosef& a,const XrPosef& b) {
  auto mul=[](auto x,auto y){return XrQuaternionf{x.w*y.x+x.x*y.w+x.y*y.z-x.z*y.y,x.w*y.y-x.x*y.z+x.y*y.w+x.z*y.x,x.w*y.z+x.x*y.y-x.y*y.x+x.z*y.w,x.w*y.w-x.x*y.x-x.y*y.y-x.z*y.z};};
  auto q=mul(a.orientation,b.orientation), ac=XrQuaternionf{-a.orientation.x,-a.orientation.y,-a.orientation.z,a.orientation.w};
  auto t=mul(mul(a.orientation,{b.position.x,b.position.y,b.position.z,0}),ac);
  return {q,{a.position.x+t.x,a.position.y+t.y,a.position.z+t.z}};
}
void publish(const Sample& s) { auto n=published.load(std::memory_order_relaxed)+1; slots[n&1]=s; slots[n&1].sequence=n; published.store(n,std::memory_order_release); }
void publisherMain() {
  WSADATA wd{}; if(WSAStartup(MAKEWORD(2,2),&wd)!=0) return; SOCKET fd=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
  sockaddr_in to{}; to.sin_family=AF_INET; to.sin_port=htons(44441); to.sin_addr.s_addr=htonl(INADDR_LOOPBACK); uint64_t seen=0;
  auto lastLog=std::chrono::steady_clock::now();
  while(running.load(std::memory_order_acquire)) {
    uint64_t n=published.load(std::memory_order_acquire); if(n!=seen) { Sample s=slots[n&1]; if(s.sequence==n) {
      char out[1024]; auto hand=[&](const Pose&p,char*b,size_t z){return std::snprintf(b,z,"{\"valid\":%s,\"flags\":%llu,\"p\":[%.9g,%.9g,%.9g],\"q\":[%.9g,%.9g,%.9g,%.9g]}",p.valid?"true":"false",(unsigned long long)p.flags,p.pose.position.x,p.pose.position.y,p.pose.position.z,p.pose.orientation.x,p.pose.orientation.y,p.pose.orientation.z,p.pose.orientation.w);};
      char l[320],r[320]; hand(s.left,l,sizeof l); hand(s.right,r,sizeof r); int len=std::snprintf(out,sizeof out,"{\"v\":2,\"counter\":%llu,\"xrTime\":%lld,\"source\":\"openxr-api-layer\",\"left\":%s,\"right\":%s}",(unsigned long long)n,(long long)s.time,l,r);
      if(len>0) sendto(fd,out,len,0,(sockaddr*)&to,sizeof to); seen=n;
    }}
    auto now=std::chrono::steady_clock::now();
    if(now-lastLog>std::chrono::seconds(5)) {
      char path[MAX_PATH]{}; DWORD count=GetTempPathA(MAX_PATH,path);
      if(count && count+28<MAX_PATH) { std::strcat(path,"BeamNGVRPosesLayer.log"); if(FILE*f=std::fopen(path,"a")){std::fprintf(f,"published=%llu\n",(unsigned long long)seen);std::fclose(f);} }
      lastLog=now;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  } closesocket(fd); WSACleanup();
}
void startPublisher(){ if(!running.exchange(true)) publisher=std::thread(publisherMain); }
void stopPublisher(){ if(running.exchange(false) && publisher.joinable()) publisher.join(); }

template<class T> void get(Dispatch& d,const char*n,T& p){ d.gipa(XR_NULL_HANDLE,n,reinterpret_cast<PFN_xrVoidFunction*>(&p)); }

XRAPI_ATTR XrResult XRAPI_CALL layerDestroyInstance(XrInstance i){ std::lock_guard l(mu); auto*d=dispatchFor(i); if(!d)return XR_ERROR_HANDLE_INVALID; auto fn=d->destroyInstance; for(auto it=actions.begin();it!=actions.end();) it=it->second.instance==i?actions.erase(it):++it; dispatches.erase(i); auto r=fn(i); if(dispatches.empty())stopPublisher(); return r; }
XRAPI_ATTR XrResult XRAPI_CALL layerCreateSession(XrInstance i,const XrSessionCreateInfo*c,XrSession*s){ std::lock_guard l(mu); auto*d=dispatchFor(i); auto r=d->createSession(i,c,s); if(XR_SUCCEEDED(r))sessions[*s]={i,XR_NULL_HANDLE}; return r; }
XRAPI_ATTR XrResult XRAPI_CALL layerDestroySession(XrSession s){ std::lock_guard l(mu); auto*d=dispatchFor(s); if(!d)return XR_ERROR_HANDLE_INVALID; auto fn=d->destroySession; for(auto it=spaces.begin();it!=spaces.end();)it=it->second.session==s?spaces.erase(it):++it;sessions.erase(s);return fn(s);}
XRAPI_ATTR XrResult XRAPI_CALL layerStringToPath(XrInstance i,const char*str,XrPath*p){std::lock_guard l(mu);auto*d=dispatchFor(i);auto r=d->stringToPath(i,str,p);if(XR_SUCCEEDED(r)){if(!std::strcmp(str,"/user/hand/left"))d->leftPath=*p;if(!std::strcmp(str,"/user/hand/right"))d->rightPath=*p;}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateActionSet(XrInstance i,const XrActionSetCreateInfo*c,XrActionSet*out){std::lock_guard l(mu);auto*d=dispatchFor(i);auto r=d->createActionSet(i,c,out);if(XR_SUCCEEDED(r))actionSets[*out]=i;return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroyActionSet(XrActionSet set){std::lock_guard l(mu);auto it=actionSets.find(set);if(it==actionSets.end())return XR_ERROR_HANDLE_INVALID;auto*d=dispatchFor(it->second);auto fn=d->destroyActionSet;actionSets.erase(it);return fn(set);}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateAction(XrActionSet set,const XrActionCreateInfo*c,XrAction*a){
  std::lock_guard l(mu);auto owner=actionSets.find(set);if(owner==actionSets.end())return XR_ERROR_HANDLE_INVALID;Dispatch*d=dispatchFor(owner->second);auto r=d->createAction(set,c,a);if(XR_SUCCEEDED(r)){Action m{};m.type=c->actionType;m.name=c->actionName;m.localized=c->localizedActionName;m.count=(std::min)(c->countSubactionPaths,16u);std::copy_n(c->subactionPaths,m.count,m.subaction);m.instance=owner->second;actions[*a]=m;}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroyAction(XrAction a){std::lock_guard l(mu);auto it=actions.find(a);if(it==actions.end())return XR_ERROR_HANDLE_INVALID;auto*d=dispatchFor(it->second.instance);auto fn=d->destroyAction;actions.erase(it);for(auto&s:spaces)if(s.second.action==a)s.second.hand=Hand::none;return fn(a);}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateActionSpace(XrSession s,const XrActionSpaceCreateInfo*c,XrSpace*out){std::lock_guard l(mu);auto*d=dispatchFor(s);auto r=d->createActionSpace(s,c,out);if(XR_SUCCEEDED(r)){Hand h=c->subactionPath==d->leftPath?Hand::left:c->subactionPath==d->rightPath?Hand::right:Hand::none;auto a=actions.find(c->action);if(a==actions.end()||a->second.type!=XR_ACTION_TYPE_POSE_INPUT)h=Hand::none;spaces[*out]={s,c->action,c->subactionPath,h,false};}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateReferenceSpace(XrSession s,const XrReferenceSpaceCreateInfo*c,XrSpace*out){std::lock_guard l(mu);auto*d=dispatchFor(s);auto r=d->createReferenceSpace(s,c,out);if(XR_SUCCEEDED(r)){bool v=c->referenceSpaceType==XR_REFERENCE_SPACE_TYPE_VIEW;spaces[*out]={s,XR_NULL_HANDLE,XR_NULL_PATH,Hand::none,v};if(v)sessions[s].view=*out;}return r;}
XRAPI_ATTR XrResult XRAPI_CALL layerDestroySpace(XrSpace s){std::lock_guard l(mu);auto it=spaces.find(s);if(it==spaces.end())return XR_ERROR_HANDLE_INVALID;auto*d=dispatchFor(it->second.session);auto fn=d->destroySpace;if(it->second.view&&sessions[it->second.session].view==s)sessions[it->second.session].view=XR_NULL_HANDLE;spaces.erase(it);return fn(s);}
XRAPI_ATTR XrResult XRAPI_CALL layerSyncActions(XrSession s,const XrActionsSyncInfo*c){std::lock_guard l(mu);auto*d=dispatchFor(s);return d->syncActions(s,c);}
XRAPI_ATTR XrResult XRAPI_CALL layerLocateSpace(XrSpace space,XrSpace base,XrTime time,XrSpaceLocation*loc){
  std::lock_guard l(mu);auto si=spaces.find(space);if(si==spaces.end())return XR_ERROR_HANDLE_INVALID;auto*d=dispatchFor(si->second.session);auto r=d->locateSpace(space,base,time,loc);if(XR_FAILED(r)||si->second.hand==Hand::none)return r;
  Sample sample{};sample.session=si->second.session;sample.base=base;sample.time=time;auto view=sessions[si->second.session].view;XrSpaceLocation h{XR_TYPE_SPACE_LOCATION};XrResult hr=view?d->locateSpace(view,base,time,&h):XR_ERROR_HANDLE_INVALID;
  Pose p{};p.flags=loc->locationFlags;p.valid=XR_SUCCEEDED(hr)&&((loc->locationFlags&kValid)==kValid)&&((h.locationFlags&kValid)==kValid);if(p.valid)p.pose=compose(inverse(h.pose),loc->pose);
  uint64_t n=published.load(std::memory_order_acquire);if(n){auto old=slots[n&1];if(old.session==si->second.session&&old.base==base&&old.time==time)sample=old;}sample.session=si->second.session;sample.base=base;sample.time=time;if(si->second.hand==Hand::left)sample.left=p;else sample.right=p;publish(sample);return r;
}

XRAPI_ATTR XrResult XRAPI_CALL layerGipa(XrInstance i,const char*n,PFN_xrVoidFunction*f){
  if(!std::strcmp(n,"xrDestroyInstance"))*f=(PFN_xrVoidFunction)layerDestroyInstance;else if(!std::strcmp(n,"xrCreateActionSet"))*f=(PFN_xrVoidFunction)layerCreateActionSet;else if(!std::strcmp(n,"xrDestroyActionSet"))*f=(PFN_xrVoidFunction)layerDestroyActionSet;else if(!std::strcmp(n,"xrCreateSession"))*f=(PFN_xrVoidFunction)layerCreateSession;else if(!std::strcmp(n,"xrDestroySession"))*f=(PFN_xrVoidFunction)layerDestroySession;else if(!std::strcmp(n,"xrStringToPath"))*f=(PFN_xrVoidFunction)layerStringToPath;else if(!std::strcmp(n,"xrCreateAction"))*f=(PFN_xrVoidFunction)layerCreateAction;else if(!std::strcmp(n,"xrDestroyAction"))*f=(PFN_xrVoidFunction)layerDestroyAction;else if(!std::strcmp(n,"xrCreateActionSpace"))*f=(PFN_xrVoidFunction)layerCreateActionSpace;else if(!std::strcmp(n,"xrCreateReferenceSpace"))*f=(PFN_xrVoidFunction)layerCreateReferenceSpace;else if(!std::strcmp(n,"xrDestroySpace"))*f=(PFN_xrVoidFunction)layerDestroySpace;else if(!std::strcmp(n,"xrSyncActions"))*f=(PFN_xrVoidFunction)layerSyncActions;else if(!std::strcmp(n,"xrLocateSpace"))*f=(PFN_xrVoidFunction)layerLocateSpace;else {std::lock_guard l(mu);auto*d=dispatchFor(i);return d?d->gipa(i,n,f):XR_ERROR_HANDLE_INVALID;}return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL layerCreateInstance(const XrInstanceCreateInfo*c,const XrApiLayerCreateInfo*li,XrInstance*i){
  auto next=*li;next.nextInfo=li->nextInfo->next;auto r=li->nextInfo->nextCreateApiLayerInstance(c,&next,i);if(XR_FAILED(r))return r;Dispatch d{};d.gipa=li->nextInfo->nextGetInstanceProcAddr;
  auto gp=[&](const char*n,auto&p){d.gipa(*i,n,reinterpret_cast<PFN_xrVoidFunction*>(&p));};gp("xrDestroyInstance",d.destroyInstance);gp("xrCreateActionSet",d.createActionSet);gp("xrDestroyActionSet",d.destroyActionSet);gp("xrCreateSession",d.createSession);gp("xrDestroySession",d.destroySession);gp("xrStringToPath",d.stringToPath);gp("xrCreateAction",d.createAction);gp("xrDestroyAction",d.destroyAction);gp("xrCreateActionSpace",d.createActionSpace);gp("xrCreateReferenceSpace",d.createReferenceSpace);gp("xrDestroySpace",d.destroySpace);gp("xrSyncActions",d.syncActions);gp("xrLocateSpace",d.locateSpace);{std::lock_guard l(mu);dispatches[*i]=d;}startPublisher();return r;
}
}
extern "C" __declspec(dllexport) XRAPI_ATTR XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(const XrNegotiateLoaderInfo*loader,const char*,XrNegotiateApiLayerRequest*request){
 if(!loader||!request||loader->structType!=XR_LOADER_INTERFACE_STRUCT_LOADER_INFO||request->structType!=XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST||loader->maxInterfaceVersion<XR_CURRENT_LOADER_API_LAYER_VERSION)return XR_ERROR_INITIALIZATION_FAILED;
 request->layerInterfaceVersion=XR_CURRENT_LOADER_API_LAYER_VERSION;request->layerApiVersion=XR_CURRENT_API_VERSION;request->getInstanceProcAddr=layerGipa;request->createApiLayerInstance=layerCreateInstance;return XR_SUCCESS;
}
