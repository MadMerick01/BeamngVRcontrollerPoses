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
#include <vector>

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
  PFN_xrCreateActionSet createActionSet{};
  PFN_xrSuggestInteractionProfileBindings suggestBindings{};
  PFN_xrAttachSessionActionSets attachActionSets{};
  PFN_xrSyncActions syncActions{};
  PFN_xrApplyHapticFeedback applyHaptic{};
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
  XrPosef pose{{0, 0, 0, 1}, {0, 0, 0}};  // relative to the head
  XrPosef raw{{0, 0, 0, 1}, {0, 0, 0}};   // in BeamNG's base space, for diagnostics
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
XrPosef rawHead{{0, 0, 0, 1}, {0, 0, 0}};  // head in BeamNG's base space, for diagnostics
unsigned long long sequence = 0;

SOCKET sock = INVALID_SOCKET;
sockaddr_in target{};

// Haptics. BeamNG binds no vibration outputs, so the layer adds its own action
// set with one vibration action, merges its bindings into BeamNG's suggested
// bindings, attaches and syncs it alongside BeamNG's sets. Lua asks for a pulse
// with a datagram "pulse <left|right> <amplitude 0-1> <seconds>" to port 44442.
// Every step falls back to BeamNG's original call if the runtime refuses it.
struct Haptics {
  XrInstance instance{};
  XrActionSet set{XR_NULL_HANDLE};
  XrAction action{XR_NULL_HANDLE};
  XrPath hand[2]{XR_NULL_PATH, XR_NULL_PATH};
  bool attached{};
  SOCKET rx{INVALID_SOCKET};
} haptics;

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

void openHapticSocket() {
  haptics.rx = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (haptics.rx == INVALID_SOCKET) return;
  u_long nonBlocking = 1;
  ioctlsocket(haptics.rx, FIONBIO, &nonBlocking);
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<u_short>(ntohs(target.sin_port) + 1));
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (bind(haptics.rx, reinterpret_cast<const sockaddr*>(&addr), sizeof addr) != 0) {
    closesocket(haptics.rx);
    haptics.rx = INVALID_SOCKET;
  }
}

int appendPose(char* out, size_t size, const PoseState& s) {
  const XrPosef& p = s.pose;
  const XrPosef& r = s.raw;
  return std::snprintf(out, size,
                       "{\"ok\":%s,\"t\":%lld,\"p\":[%.6f,%.6f,%.6f],\"q\":[%.6f,%.6f,%.6f,%.6f],"
                       "\"rp\":[%.6f,%.6f,%.6f],\"rq\":[%.6f,%.6f,%.6f,%.6f]}",
                       s.valid ? "true" : "false", static_cast<long long>(s.time), p.position.x,
                       p.position.y, p.position.z, p.orientation.x, p.orientation.y,
                       p.orientation.z, p.orientation.w, r.position.x, r.position.y, r.position.z,
                       r.orientation.x, r.orientation.y, r.orientation.z, r.orientation.w);
}

// Caller holds the mutex.
int buildPacketLocked(char* out, size_t size, XrTime time) {
  const XrPosef& h = rawHead;
  int n = std::snprintf(out, size,
                        "{\"v\":3,\"seq\":%llu,\"t\":%lld,"
                        "\"head\":{\"p\":[%.6f,%.6f,%.6f],\"q\":[%.6f,%.6f,%.6f,%.6f]},\"hands\":{",
                        ++sequence, static_cast<long long>(time), h.position.x, h.position.y,
                        h.position.z, h.orientation.x, h.orientation.y, h.orientation.z,
                        h.orientation.w);
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

  char packet[2048];
  int length = -1;
  {
    std::lock_guard lock(mutex);
    PoseState& state = hands[tagged.hand].pose[tagged.kind];
    // An inactive interaction profile reports invalid; don't let it overwrite
    // a valid pose another profile's space produced for this same frame.
    if (headOk && controllerOk) {
      state.valid = true;
      state.pose = relativeTo(head.pose, pose);
      state.raw = pose;
      rawHead = head.pose;
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

// Creates the haptic action set once BeamNG has started defining its actions.
// Caller holds no lock.
void ensureHapticActions(const Dispatch& d, XrInstance instance) {
  {
    std::lock_guard lock(mutex);
    if (haptics.set != XR_NULL_HANDLE) return;
  }
  if (!d.createActionSet || !d.createAction || !d.stringToPath) return;
  XrPath hand[2];
  if (XR_FAILED(d.stringToPath(instance, "/user/hand/left", &hand[0])) ||
      XR_FAILED(d.stringToPath(instance, "/user/hand/right", &hand[1])))
    return;
  XrActionSetCreateInfo setInfo{XR_TYPE_ACTION_SET_CREATE_INFO};
  std::strcpy(setInfo.actionSetName, "beamng_vr_poses_haptics");
  std::strcpy(setInfo.localizedActionSetName, "BeamNG VR poses haptics");
  XrActionSet set = XR_NULL_HANDLE;
  if (XR_FAILED(d.createActionSet(instance, &setInfo, &set))) return;
  XrActionCreateInfo actionInfo{XR_TYPE_ACTION_CREATE_INFO};
  std::strcpy(actionInfo.actionName, "vibrate");
  std::strcpy(actionInfo.localizedActionName, "Vibrate");
  actionInfo.actionType = XR_ACTION_TYPE_VIBRATION_OUTPUT;
  actionInfo.countSubactionPaths = 2;
  actionInfo.subactionPaths = hand;
  XrAction action = XR_NULL_HANDLE;
  if (XR_FAILED(d.createAction(set, &actionInfo, &action))) return;
  std::lock_guard lock(mutex);
  haptics.instance = instance;
  haptics.set = set;
  haptics.action = action;
  haptics.hand[0] = hand[0];
  haptics.hand[1] = hand[1];
}

XRAPI_ATTR XrResult XRAPI_CALL hookSuggestBindings(
    XrInstance instance, const XrInteractionProfileSuggestedBinding* suggested) {
  Dispatch d;
  if (!dispatchFor(instance, d)) return XR_ERROR_HANDLE_INVALID;
  ensureHapticActions(d, instance);
  XrAction action;
  {
    std::lock_guard lock(mutex);
    action = haptics.action;
  }
  if (!suggested || action == XR_NULL_HANDLE) return d.suggestBindings(instance, suggested);

  XrPath outputs[2];
  if (XR_FAILED(d.stringToPath(instance, "/user/hand/left/output/haptic", &outputs[0])) ||
      XR_FAILED(d.stringToPath(instance, "/user/hand/right/output/haptic", &outputs[1])))
    return d.suggestBindings(instance, suggested);
  std::vector<XrActionSuggestedBinding> merged(
      suggested->suggestedBindings, suggested->suggestedBindings + suggested->countSuggestedBindings);
  merged.push_back({action, outputs[0]});
  merged.push_back({action, outputs[1]});
  XrInteractionProfileSuggestedBinding withHaptics = *suggested;
  withHaptics.countSuggestedBindings = static_cast<uint32_t>(merged.size());
  withHaptics.suggestedBindings = merged.data();
  const XrResult r = d.suggestBindings(instance, &withHaptics);
  // Profiles without haptic outputs reject the whole call: fall back to BeamNG's own.
  return XR_SUCCEEDED(r) ? r : d.suggestBindings(instance, suggested);
}

XRAPI_ATTR XrResult XRAPI_CALL hookAttachActionSets(XrSession session,
                                                    const XrSessionActionSetsAttachInfo* info) {
  Dispatch d;
  if (!dispatchFor(session, d)) return XR_ERROR_HANDLE_INVALID;
  XrActionSet set;
  {
    std::lock_guard lock(mutex);
    set = haptics.set;
  }
  if (!info || set == XR_NULL_HANDLE) return d.attachActionSets(session, info);
  std::vector<XrActionSet> sets(info->actionSets, info->actionSets + info->countActionSets);
  sets.push_back(set);
  XrSessionActionSetsAttachInfo withHaptics = *info;
  withHaptics.countActionSets = static_cast<uint32_t>(sets.size());
  withHaptics.actionSets = sets.data();
  XrResult r = d.attachActionSets(session, &withHaptics);
  const bool attached = XR_SUCCEEDED(r);
  if (!attached) r = d.attachActionSets(session, info);
  std::lock_guard lock(mutex);
  haptics.attached = attached;
  return r;
}

// Applies pulses Lua asked for since the last frame. Called on BeamNG's input thread.
void applyPendingPulses(const Dispatch& d, XrSession session) {
  if (haptics.rx == INVALID_SOCKET || !d.applyHaptic) return;
  char buf[128];
  int n;
  while ((n = recv(haptics.rx, buf, sizeof buf - 1, 0)) > 0) {
    buf[n] = 0;
    char hand[16]{};
    float amplitude = 0, seconds = 0;
    if (std::sscanf(buf, "pulse %15s %f %f", hand, &amplitude, &seconds) != 3) continue;
    XrHapticActionInfo info{XR_TYPE_HAPTIC_ACTION_INFO};
    XrHapticVibration vibration{XR_TYPE_HAPTIC_VIBRATION};
    {
      std::lock_guard lock(mutex);
      info.action = haptics.action;
      info.subactionPath = haptics.hand[std::strcmp(hand, "left") ? 1 : 0];
    }
    vibration.amplitude = amplitude < 0 ? 0 : amplitude > 1 ? 1 : amplitude;
    vibration.duration = static_cast<XrDuration>((seconds > 0 ? seconds : 0.01f) * 1e9f);
    vibration.frequency = XR_FREQUENCY_UNSPECIFIED;
    d.applyHaptic(session, &info, reinterpret_cast<const XrHapticBaseHeader*>(&vibration));
  }
}

XRAPI_ATTR XrResult XRAPI_CALL hookSyncActions(XrSession session, const XrActionsSyncInfo* info) {
  Dispatch d;
  if (!dispatchFor(session, d)) return XR_ERROR_HANDLE_INVALID;
  XrActionSet set;
  bool attached;
  {
    std::lock_guard lock(mutex);
    set = haptics.set;
    attached = haptics.attached;
  }
  if (!info || !attached) return d.syncActions(session, info);
  std::vector<XrActiveActionSet> active(info->activeActionSets,
                                        info->activeActionSets + info->countActiveActionSets);
  active.push_back({set, XR_NULL_PATH});
  XrActionsSyncInfo withHaptics = *info;
  withHaptics.countActiveActionSets = static_cast<uint32_t>(active.size());
  withHaptics.activeActionSets = active.data();
  XrResult r = d.syncActions(session, &withHaptics);
  if (XR_FAILED(r)) return d.syncActions(session, info);
  applyPendingPulses(d, session);
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
      {"xrSuggestInteractionProfileBindings", reinterpret_cast<PFN_xrVoidFunction>(hookSuggestBindings)},
      {"xrAttachSessionActionSets", reinterpret_cast<PFN_xrVoidFunction>(hookAttachActionSets)},
      {"xrSyncActions", reinterpret_cast<PFN_xrVoidFunction>(hookSyncActions)},
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
  get("xrCreateActionSet", d.createActionSet);
  get("xrSuggestInteractionProfileBindings", d.suggestBindings);
  get("xrAttachSessionActionSets", d.attachActionSets);
  get("xrSyncActions", d.syncActions);
  get("xrApplyHapticFeedback", d.applyHaptic);

  std::lock_guard lock(mutex);
  instances[*instance] = d;
  if (sock == INVALID_SOCKET) {
    openSocket();
    openHapticSocket();
  }
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
