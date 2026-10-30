// BeamNG VR controller pose layer (protocol v3).
//
// An OpenXR API layer that sits between BeamNG and the runtime. It never changes
// what BeamNG sees. Whenever BeamNG locates one of its own controller pose spaces,
// the layer also locates the headset VIEW space at the same XrTime and publishes
// the controller pose relative to the head, separately for grip and aim, plus the
// trigger and squeeze values BeamNG reads. Lua multiplies that by the final
// camera pose (getCameraPosition/getCameraQuat), which BeamNG 0.40 sets to the
// exact rendered head pose every frame, so no tracking-space bookkeeping is needed.
//
// Packets are small JSON datagrams sent to 127.0.0.1:44441 (BEAMNG_VR_POSES_PORT
// overrides the port).

#define XR_USE_PLATFORM_WIN32
#include <winsock2.h>
#include <windows.h>
#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <mutex>
#include <string>
#include <unordered_map>

namespace {

constexpr XrSpaceLocationFlags kPoseValid =
    XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;

enum Hand { kLeft = 0, kRight = 1, kNoHand = 2 };
enum PoseKind { kGrip = 0, kAim = 1, kNoKind = 2 };
enum FloatKind { kTrigger = 0, kSqueeze = 1, kNoFloat = 2 };

struct Dispatch {
  PFN_xrGetInstanceProcAddr gipa{};
  PFN_xrDestroyInstance destroyInstance{};
  PFN_xrCreateSession createSession{};
  PFN_xrDestroySession destroySession{};
  PFN_xrStringToPath stringToPath{};
  PFN_xrCreateAction createAction{};
  PFN_xrCreateActionSpace createActionSpace{};
  PFN_xrCreateReferenceSpace createReferenceSpace{};
  PFN_xrDestroySpace destroySpace{};
  PFN_xrLocateSpace locateSpace{};
  PFN_xrLocateSpaces locateSpaces{};
  PFN_xrLocateSpacesKHR locateSpacesKHR{};
  PFN_xrGetActionStateFloat getActionStateFloat{};
  XrPath leftPath{XR_NULL_PATH}, rightPath{XR_NULL_PATH};
};

struct ActionInfo {
  XrInstance instance{};
  XrActionType type{};
  PoseKind pose{kNoKind};
  FloatKind value{kNoFloat};
  Hand onlyHand{kNoHand};  // set when the action has exactly one hand subaction path
};

struct SpaceInfo {
  XrSession session{};
  Hand hand{kNoHand};
  PoseKind kind{kNoKind};
};

struct SessionInfo {
  XrInstance instance{};
  XrSpace view{XR_NULL_HANDLE};  // owned by the layer, never handed to BeamNG
};

struct PoseState {
  bool valid{};
  XrPosef pose{{0, 0, 0, 1}, {0, 0, 0}};
  XrTime time{};
};

struct HandState {
  PoseState pose[2];
  float value[2]{};
};

std::mutex mutex;  // guards everything below; a handful of locks per frame
std::unordered_map<XrInstance, Dispatch> instances;  // BeamNG instances we observe
// Every instance's next GetInstanceProcAddr. Installed as an implicit layer the
// DLL loads into every OpenXR app; anything that isn't BeamNG is passed straight
// through.
std::unordered_map<XrInstance, PFN_xrGetInstanceProcAddr> nextGipa;
std::unordered_map<XrSession, SessionInfo> sessions;
std::unordered_map<XrActionSet, XrInstance> actionSets;
std::unordered_map<XrAction, ActionInfo> actions;
std::unordered_map<XrSpace, SpaceInfo> spaces;
HandState hands[2];
unsigned long long sequence = 0;

SOCKET sock = INVALID_SOCKET;
sockaddr_in target{};

// ------------------------------------------------------------------ helpers --

bool contains(const char* text, const char* part) { return std::strstr(text, part) != nullptr; }

PoseKind poseKindFromName(const char* name) {
  if (contains(name, "aim_pose") || contains(name, "aim")) return kAim;
  if (contains(name, "grip_pose") || contains(name, "grip")) return kGrip;
  return kNoKind;
}

FloatKind floatKindFromName(const char* name) {
  if (contains(name, "trigger_value")) return kTrigger;
  if (contains(name, "squeeze_value")) return kSqueeze;
  return kNoFloat;
}

Hand handFromPath(const Dispatch& d, XrPath path) {
  if (path == XR_NULL_PATH) return kNoHand;
  if (path == d.leftPath) return kLeft;
  if (path == d.rightPath) return kRight;
  return kNoHand;
}

XrQuaternionf multiply(const XrQuaternionf& a, const XrQuaternionf& b) {
  return {a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
          a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
          a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
          a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
}

XrVector3f rotate(const XrQuaternionf& q, const XrVector3f& v) {
  const XrQuaternionf p{v.x, v.y, v.z, 0};
  const XrQuaternionf c{-q.x, -q.y, -q.z, q.w};
  const XrQuaternionf r = multiply(multiply(q, p), c);
  return {r.x, r.y, r.z};
}

// inverse(head) * controller: the controller pose expressed in head (VIEW) space.
XrPosef relativeTo(const XrPosef& head, const XrPosef& controller) {
  const XrQuaternionf inv{-head.orientation.x, -head.orientation.y, -head.orientation.z,
                          head.orientation.w};
  const XrVector3f d{controller.position.x - head.position.x,
                     controller.position.y - head.position.y,
                     controller.position.z - head.position.z};
  return {multiply(inv, controller.orientation), rotate(inv, d)};
}

void openSocket() {
  WSADATA data{};
  if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return;
  sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (sock == INVALID_SOCKET) return;
  u_long nonBlocking = 1;
  ioctlsocket(sock, FIONBIO, &nonBlocking);
  int port = 44441;
  if (const char* env = std::getenv("BEAMNG_VR_POSES_PORT")) {
    const int p = std::atoi(env);
    if (p > 0 && p < 65536) port = p;
  }
  target.sin_family = AF_INET;
  target.sin_port = htons(static_cast<u_short>(port));
  target.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
}

int appendPose(char* out, size_t size, const PoseState& s) {
  const XrPosef& p = s.pose;
  return std::snprintf(out, size,
                       "{\"ok\":%s,\"t\":%lld,\"p\":[%.6f,%.6f,%.6f],\"q\":[%.6f,%.6f,%.6f,%.6f]}",
                       s.valid ? "true" : "false", static_cast<long long>(s.time), p.position.x,
                       p.position.y, p.position.z, p.orientation.x, p.orientation.y,
                       p.orientation.z, p.orientation.w);
}

// Caller holds the mutex.
int buildPacketLocked(char* out, size_t size, XrTime time) {
  int n = std::snprintf(out, size, "{\"v\":3,\"seq\":%llu,\"t\":%lld,\"hands\":{", ++sequence,
                        static_cast<long long>(time));
  const char* names[2] = {"left", "right"};
  for (int h = 0; h < 2 && n > 0 && static_cast<size_t>(n) < size; ++h) {
    n += std::snprintf(out + n, size - n, "%s\"%s\":{\"grip\":", h ? "," : "", names[h]);
    n += appendPose(out + n, size - n, hands[h].pose[kGrip]);
    n += std::snprintf(out + n, size - n, ",\"aim\":");
    n += appendPose(out + n, size - n, hands[h].pose[kAim]);
    n += std::snprintf(out + n, size - n, ",\"trigger\":%.4f,\"squeeze\":%.4f}",
                       hands[h].value[kTrigger], hands[h].value[kSqueeze]);
  }
  if (n > 0 && static_cast<size_t>(n) < size) n += std::snprintf(out + n, size - n, "}}");
  return (n > 0 && static_cast<size_t>(n) < size) ? n : -1;
}

void send(const char* data, int length) {
  if (sock != INVALID_SOCKET && length > 0)
    sendto(sock, data, length, 0, reinterpret_cast<const sockaddr*>(&target), sizeof target);
}

bool dispatchFor(XrInstance instance, Dispatch& out) {
  std::lock_guard lock(mutex);
  auto it = instances.find(instance);
  if (it == instances.end()) return false;
  out = it->second;
  return true;
}

bool dispatchFor(XrSession session, Dispatch& out, SessionInfo* info = nullptr) {
  std::lock_guard lock(mutex);
  auto s = sessions.find(session);
  if (s == sessions.end()) return false;
  auto it = instances.find(s->second.instance);
  if (it == instances.end()) return false;
  out = it->second;
  if (info) *info = s->second;
  return true;
}

// ------------------------------------------------------------------- hooks --

XRAPI_ATTR XrResult XRAPI_CALL hookDestroyInstance(XrInstance instance) {
  Dispatch d;
  if (!dispatchFor(instance, d)) return XR_ERROR_HANDLE_INVALID;
  {
    std::lock_guard lock(mutex);
    instances.erase(instance);
    nextGipa.erase(instance);
    for (auto it = actions.begin(); it != actions.end();)
      it = it->second.instance == instance ? actions.erase(it) : std::next(it);
    for (auto it = actionSets.begin(); it != actionSets.end();)
      it = it->second == instance ? actionSets.erase(it) : std::next(it);
  }
  return d.destroyInstance(instance);
}

XRAPI_ATTR XrResult XRAPI_CALL hookStringToPath(XrInstance instance, const char* text,
                                                XrPath* path) {
  Dispatch d;
  if (!dispatchFor(instance, d)) return XR_ERROR_HANDLE_INVALID;
  const XrResult r = d.stringToPath(instance, text, path);
  if (XR_SUCCEEDED(r) && text) {
    std::lock_guard lock(mutex);
    auto it = instances.find(instance);
    if (it != instances.end()) {
      if (!std::strcmp(text, "/user/hand/left")) it->second.leftPath = *path;
      if (!std::strcmp(text, "/user/hand/right")) it->second.rightPath = *path;
    }
  }
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookCreateSession(XrInstance instance,
                                                 const XrSessionCreateInfo* info,
                                                 XrSession* session) {
  Dispatch d;
  if (!dispatchFor(instance, d)) return XR_ERROR_HANDLE_INVALID;
  const XrResult r = d.createSession(instance, info, session);
  if (XR_FAILED(r)) return r;
  XrReferenceSpaceCreateInfo viewInfo{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
  viewInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
  viewInfo.poseInReferenceSpace.orientation.w = 1.0f;
  XrSpace view = XR_NULL_HANDLE;
  if (XR_FAILED(d.createReferenceSpace(*session, &viewInfo, &view))) view = XR_NULL_HANDLE;
  std::lock_guard lock(mutex);
  sessions[*session] = {instance, view};
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookDestroySession(XrSession session) {
  Dispatch d;
  SessionInfo info;
  if (!dispatchFor(session, d, &info)) return XR_ERROR_HANDLE_INVALID;
  {
    std::lock_guard lock(mutex);
    sessions.erase(session);
    for (auto it = spaces.begin(); it != spaces.end();)
      it = it->second.session == session ? spaces.erase(it) : std::next(it);
    for (auto& h : hands) h = {};
  }
  if (info.view != XR_NULL_HANDLE) d.destroySpace(info.view);
  return d.destroySession(session);
}

XRAPI_ATTR XrResult XRAPI_CALL hookCreateActionSet(XrInstance instance,
                                                   const XrActionSetCreateInfo* info,
                                                   XrActionSet* set);

XRAPI_ATTR XrResult XRAPI_CALL hookCreateAction(XrActionSet set, const XrActionCreateInfo* info,
                                                XrAction* action) {
  Dispatch d;
  XrInstance instance{};
  {
    std::lock_guard lock(mutex);
    auto it = actionSets.find(set);
    if (it == actionSets.end()) return XR_ERROR_HANDLE_INVALID;
    instance = it->second;
    d = instances[instance];
  }
  const XrResult r = d.createAction(set, info, action);
  if (XR_SUCCEEDED(r) && info) {
    ActionInfo a;
    a.instance = instance;
    a.type = info->actionType;
    if (info->actionType == XR_ACTION_TYPE_POSE_INPUT) a.pose = poseKindFromName(info->actionName);
    if (info->actionType == XR_ACTION_TYPE_FLOAT_INPUT) a.value = floatKindFromName(info->actionName);
    if (info->countSubactionPaths == 1) a.onlyHand = handFromPath(d, info->subactionPaths[0]);
    std::lock_guard lock(mutex);
    actions[*action] = a;
  }
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookCreateActionSpace(XrSession session,
                                                     const XrActionSpaceCreateInfo* info,
                                                     XrSpace* space) {
  Dispatch d;
  if (!dispatchFor(session, d)) return XR_ERROR_HANDLE_INVALID;
  const XrResult r = d.createActionSpace(session, info, space);
  if (XR_SUCCEEDED(r) && info) {
    std::lock_guard lock(mutex);
    auto a = actions.find(info->action);
    if (a != actions.end() && a->second.pose != kNoKind) {
      Hand hand = handFromPath(d, info->subactionPath);
      if (hand == kNoHand) hand = a->second.onlyHand;
      if (hand != kNoHand) spaces[*space] = {session, hand, a->second.pose};
    }
  }
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookDestroySpace(XrSpace space) {
  PFN_xrDestroySpace destroy{};
  {
    std::lock_guard lock(mutex);
    spaces.erase(space);
    // Any live instance's dispatch works; spaces are session children.
    for (auto& [_, d] : instances) { destroy = d.destroySpace; break; }
  }
  return destroy ? destroy(space) : XR_ERROR_HANDLE_INVALID;
}

// Looks up whether a space is one of BeamNG's controller pose spaces.
bool lookupTagged(XrSpace space, SpaceInfo& tagged, XrSpace& view) {
  auto s = spaces.find(space);
  if (s == spaces.end()) return false;
  auto session = sessions.find(s->second.session);
  if (session == sessions.end() || session->second.view == XR_NULL_HANDLE) return false;
  tagged = s->second;
  view = session->second.view;
  return true;
}

// Locates the head at the same time and base, stores the head-relative pose
// and publishes a packet.
void record(const Dispatch& d, const SpaceInfo& tagged, XrSpace view, XrSpace base, XrTime time,
            XrSpaceLocationFlags flags, const XrPosef& pose) {
  XrSpaceLocation head{XR_TYPE_SPACE_LOCATION};
  const bool headOk = XR_SUCCEEDED(d.locateSpace(view, base, time, &head)) &&
                      (head.locationFlags & kPoseValid) == kPoseValid;
  const bool controllerOk = (flags & kPoseValid) == kPoseValid;

  char packet[1024];
  int length = -1;
  {
    std::lock_guard lock(mutex);
    PoseState& state = hands[tagged.hand].pose[tagged.kind];
    // An inactive interaction profile reports invalid; don't let it overwrite
    // a valid pose another profile's space produced for this same frame.
    if (headOk && controllerOk) {
      state.valid = true;
      state.pose = relativeTo(head.pose, pose);
      state.time = time;
    } else if (state.time != time) {
      state.valid = false;
      state.time = time;
    }
    length = buildPacketLocked(packet, sizeof packet, time);
  }
  send(packet, length);
}

Dispatch anyDispatchLocked() { return instances.empty() ? Dispatch{} : instances.begin()->second; }

XRAPI_ATTR XrResult XRAPI_CALL hookLocateSpace(XrSpace space, XrSpace base, XrTime time,
                                               XrSpaceLocation* location) {
  SpaceInfo tagged;
  XrSpace view = XR_NULL_HANDLE;
  Dispatch d;
  bool isTagged;
  {
    std::lock_guard lock(mutex);
    d = anyDispatchLocked();
    isTagged = lookupTagged(space, tagged, view);
  }
  if (!d.locateSpace) return XR_ERROR_HANDLE_INVALID;
  const XrResult r = d.locateSpace(space, base, time, location);
  if (isTagged && XR_SUCCEEDED(r))
    record(d, tagged, view, base, time, location->locationFlags, location->pose);
  return r;
}

// OpenXR 1.1 batch locate (and its KHR twin); BeamNG may switch to it.
XrResult locateSpacesCommon(PFN_xrLocateSpaces next, XrSession session,
                            const XrSpacesLocateInfo* info, XrSpaceLocations* out) {
  if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
  const XrResult r = next(session, info, out);
  if (XR_FAILED(r) || !info || !out || !out->locations) return r;
  Dispatch d;
  {
    std::lock_guard lock(mutex);
    d = anyDispatchLocked();
  }
  const uint32_t count = info->spaceCount < out->locationCount ? info->spaceCount : out->locationCount;
  for (uint32_t i = 0; i < count; ++i) {
    SpaceInfo tagged;
    XrSpace view = XR_NULL_HANDLE;
    bool isTagged;
    {
      std::lock_guard lock(mutex);
      isTagged = lookupTagged(info->spaces[i], tagged, view);
    }
    if (isTagged)
      record(d, tagged, view, info->baseSpace, info->time, out->locations[i].locationFlags,
             out->locations[i].pose);
  }
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookLocateSpaces(XrSession session, const XrSpacesLocateInfo* info,
                                                XrSpaceLocations* out) {
  Dispatch d;
  {
    std::lock_guard lock(mutex);
    d = anyDispatchLocked();
  }
  return locateSpacesCommon(d.locateSpaces, session, info, out);
}

XRAPI_ATTR XrResult XRAPI_CALL hookLocateSpacesKHR(XrSession session, const XrSpacesLocateInfo* info,
                                                   XrSpaceLocations* out) {
  Dispatch d;
  {
    std::lock_guard lock(mutex);
    d = anyDispatchLocked();
  }
  return locateSpacesCommon(d.locateSpacesKHR, session, info, out);
}

XRAPI_ATTR XrResult XRAPI_CALL hookGetActionStateFloat(XrSession session,
                                                       const XrActionStateGetInfo* info,
                                                       XrActionStateFloat* state) {
  Dispatch d;
  if (!dispatchFor(session, d)) return XR_ERROR_HANDLE_INVALID;
  const XrResult r = d.getActionStateFloat(session, info, state);
  if (XR_SUCCEEDED(r) && info && state) {
    std::lock_guard lock(mutex);
    auto a = actions.find(info->action);
    if (a != actions.end() && a->second.value != kNoFloat) {
      Hand hand = handFromPath(d, info->subactionPath);
      if (hand == kNoHand) hand = a->second.onlyHand;
      // Several profiles share a hand; only the active binding reports isActive.
      if (hand != kNoHand && state->isActive) hands[hand].value[a->second.value] = state->currentState;
    }
  }
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookCreateActionSet(XrInstance instance,
                                                   const XrActionSetCreateInfo* info,
                                                   XrActionSet* set) {
  Dispatch d;
  if (!dispatchFor(instance, d)) return XR_ERROR_HANDLE_INVALID;
  PFN_xrCreateActionSet next{};
  d.gipa(instance, "xrCreateActionSet", reinterpret_cast<PFN_xrVoidFunction*>(&next));
  if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
  const XrResult r = next(instance, info, set);
  if (XR_SUCCEEDED(r)) {
    std::lock_guard lock(mutex);
    actionSets[*set] = instance;
  }
  return r;
}

XRAPI_ATTR XrResult XRAPI_CALL hookGetInstanceProcAddr(XrInstance instance, const char* name,
                                                       PFN_xrVoidFunction* function) {
  struct Entry { const char* name; PFN_xrVoidFunction fn; };
  static const Entry hooks[] = {
      {"xrDestroyInstance", reinterpret_cast<PFN_xrVoidFunction>(hookDestroyInstance)},
      {"xrStringToPath", reinterpret_cast<PFN_xrVoidFunction>(hookStringToPath)},
      {"xrCreateSession", reinterpret_cast<PFN_xrVoidFunction>(hookCreateSession)},
      {"xrDestroySession", reinterpret_cast<PFN_xrVoidFunction>(hookDestroySession)},
      {"xrCreateActionSet", reinterpret_cast<PFN_xrVoidFunction>(hookCreateActionSet)},
      {"xrCreateAction", reinterpret_cast<PFN_xrVoidFunction>(hookCreateAction)},
      {"xrCreateActionSpace", reinterpret_cast<PFN_xrVoidFunction>(hookCreateActionSpace)},
      {"xrDestroySpace", reinterpret_cast<PFN_xrVoidFunction>(hookDestroySpace)},
      {"xrLocateSpace", reinterpret_cast<PFN_xrVoidFunction>(hookLocateSpace)},
      {"xrLocateSpaces", reinterpret_cast<PFN_xrVoidFunction>(hookLocateSpaces)},
      {"xrLocateSpacesKHR", reinterpret_cast<PFN_xrVoidFunction>(hookLocateSpacesKHR)},
      {"xrGetActionStateFloat", reinterpret_cast<PFN_xrVoidFunction>(hookGetActionStateFloat)},
  };
  bool observed;
  PFN_xrGetInstanceProcAddr next{};
  {
    std::lock_guard lock(mutex);
    observed = instances.count(instance) != 0;
    auto it = nextGipa.find(instance);
    if (it != nextGipa.end()) next = it->second;
    else if (!nextGipa.empty()) next = nextGipa.begin()->second;
  }
  if (observed && name) {
    for (const Entry& e : hooks) {
      if (!std::strcmp(name, e.name)) {
        *function = e.fn;
        return XR_SUCCESS;
      }
    }
  }
  return next ? next(instance, name, function) : XR_ERROR_HANDLE_INVALID;
}

XRAPI_ATTR XrResult XRAPI_CALL hookCreateApiLayerInstance(const XrInstanceCreateInfo* info,
                                                          const XrApiLayerCreateInfo* layerInfo,
                                                          XrInstance* instance) {
  XrApiLayerCreateInfo next = *layerInfo;
  next.nextInfo = layerInfo->nextInfo->next;
  const XrResult r =
      layerInfo->nextInfo->nextCreateApiLayerInstance(info, &next, instance);
  if (XR_FAILED(r)) return r;

  const bool isBeamNG = info && std::strstr(info->applicationInfo.applicationName, "BeamNG");
  {
    std::lock_guard lock(mutex);
    nextGipa[*instance] = layerInfo->nextInfo->nextGetInstanceProcAddr;
  }
  if (!isBeamNG && !std::getenv("BEAMNG_VR_POSES_ANY_APP")) return r;

  Dispatch d;
  d.gipa = layerInfo->nextInfo->nextGetInstanceProcAddr;
  auto get = [&](const char* fn, auto& out) {
    d.gipa(*instance, fn, reinterpret_cast<PFN_xrVoidFunction*>(&out));
  };
  get("xrDestroyInstance", d.destroyInstance);
  get("xrCreateSession", d.createSession);
  get("xrDestroySession", d.destroySession);
  get("xrStringToPath", d.stringToPath);
  get("xrCreateAction", d.createAction);
  get("xrCreateActionSpace", d.createActionSpace);
  get("xrCreateReferenceSpace", d.createReferenceSpace);
  get("xrDestroySpace", d.destroySpace);
  get("xrLocateSpace", d.locateSpace);
  get("xrLocateSpaces", d.locateSpaces);
  get("xrLocateSpacesKHR", d.locateSpacesKHR);
  get("xrGetActionStateFloat", d.getActionStateFloat);

  std::lock_guard lock(mutex);
  instances[*instance] = d;
  if (sock == INVALID_SOCKET) openSocket();
  return r;
}

}  // namespace

extern "C" __declspec(dllexport) XRAPI_ATTR XrResult XRAPI_CALL
xrNegotiateLoaderApiLayerInterface(const XrNegotiateLoaderInfo* loader, const char*,
                                   XrNegotiateApiLayerRequest* request) {
  if (!loader || !request || loader->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
      request->structType != XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST ||
      loader->maxInterfaceVersion < XR_CURRENT_LOADER_API_LAYER_VERSION)
    return XR_ERROR_INITIALIZATION_FAILED;
  request->layerInterfaceVersion = XR_CURRENT_LOADER_API_LAYER_VERSION;
  request->layerApiVersion = XR_CURRENT_API_VERSION;
  request->getInstanceProcAddr = hookGetInstanceProcAddr;
  request->createApiLayerInstance = hookCreateApiLayerInstance;
  return XR_SUCCESS;
}
