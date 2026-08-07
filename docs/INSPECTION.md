# BeamNG 0.39 dump inspection

## Inputs and method

The repository contains exactly `api_dump_0.39` (JSON, 3.6 MiB),
`api_dump_0.39text` (the same dump rendered as text, 1.5 MiB), and
`openxr_dump.zip` (one member, `openxr_dump.txt`, 113,169,906 bytes). Searches
covered camera/VR/OpenXR, scene/SimObject, debug drawing, Lua, sockets, ray casts,
and native/extension terms. The dump records names and traced calls, not C++/Lua
signatures or semantic guarantees.

## Findings

* Global camera bindings include `getCameraTransform`, `getCameraPosition`,
  `getCameraQuat`, direction vectors and projection/FOV functions. The `OpenXR`
  table exposes `getCameraPosRotPredictedXYZXYZW`, and live headset testing
  confirms that this getter is available. Its observed position was approximately
  `(-0.013, 0.009, 0.013)` while the BeamNG world camera was approximately
  `(913.884, 774.990, 237.515)`: it is a tracking-local pose close to the OpenXR
  origin, not a complete BeamNG world camera transform. Direct world-space use
  placed the magenta diagnostic sphere and both blue controller spheres near the
  map origin. The consumer still calls the getter safely, validates all seven
  scalars, and normalizes its XYZW quaternion, but exposes it only through
  `predictedOpenXRTrackingLocal*` diagnostics. It is never a controller parent and
  is not drawn directly in BeamNG world coordinates. BeamNG camera reconstruction
  remains the active controller world transform. This restores visible behavior;
  it does not establish that the original yaw-dependent translation problem is
  solved.
* `createObject`, `SimObject`, `scenetree`, `TSStatic`, `StaticShapeData`, and
  prefab functions exist. A `debugDrawer` userdata and `DebugDrawer` binding exist,
  but its methods are opaque in this dump. Consequently `drawSphere` is a
  deliberately isolated, in-game validation point rather than a dump-proven
  signature. A world debug primitive submitted during pre-render should be
  stereoscopic because it participates in world rendering; the dump does not prove
  per-eye lifetime. A persistent `TSStatic` mesh is the fallback if this call is
  absent or mono-only.
* LuaSocket is present (`socket.udp`, `select`, `gettime`, TCP variants), alongside
  Lua command queue/binding functions. Loopback UDP is supported evidence for the
  least-coupled bridge. Ray APIs include `castRay`, `castRayStatic`,
  `rayCastStartPosEndPos`, `containerRayCast`, camera mouse rays and debug casts;
  Stage 1 does not use them. No stable public native plug-in ABI was identified.
* The trace creates VIEW, LOCAL and STAGE reference spaces (five reference-space
  creations total in this capture), calls `xrLocateViews` approximately 5,002
  times, and calls `xrLocateSpace` approximately 49,426 times. BeamNG creates one
  action set, attaches/synchronizes it, creates pose actions named `aim_pose` and
  `grip_pose` for many interaction profiles, creates 68 action spaces with left and
  right subaction paths, queries pose state, and locates those spaces against the
  same base space and predicted time. Thus controller action spaces genuinely do
  exist in this capture. It would be wrong to generalize this to versions/settings
  where `openXRuseControllers` is disabled without another acquisition mechanism.

## Route comparison and decision

| Route | Assessment |
|---|---|
| Existing action spaces | Best temporal/spatial match. An implicit API layer could observe the already-located grip spaces without creating actions or another session. It is the preferred future provider, but requires a correctly installed, runtime-agnostic Windows layer and more live traces to map BeamNG's per-profile action objects safely. |
| OpenXR API layer | Does not need to alter the game when observation-only. It must correlate `xrCreateAction`, `xrCreateActionSpace`, subaction paths and successful valid `xrLocateSpace` results; intercepting arbitrary locate calls is not sufficient. Higher packaging and loader risk than the Stage 1 external provider. |
| SteamVR/OpenVR | Can retrieve HMD and role-labelled controllers in one standing universe without touching BeamNG's OpenXR session. Smallest implementable provider. It only works when the active headset/runtime exposes tracking through SteamVR concurrently. |
| Separate provider | Architecturally safe if it supplies HMD and both controllers in one coordinate system. Vendor choice and runtime coexistence remain deployment-specific. |
| Shared memory / UDP | Transport, not acquisition. Named shared memory is fast but needs a native BeamNG reader. Non-blocking localhost UDP is directly supported by dumped LuaSocket, naturally drops backlog, and is simplest. |

Stage 1 therefore uses a background OpenVR provider -> versioned loopback UDP ->
GE Lua consumer. Raw tracking remains raw until Lua computes
`beamngHmdWorld * inverse(externalHmd) * externalController`. No second OpenXR
session, injection, offsets, render hooks, or BeamNG thread blocking are involved.

## Unknowns requiring the first live test

Live testing established the predicted getter's tracking-local position semantics,
but the static dumps still cannot prove its units or quaternion direction. They
also cannot prove OpenVR/BeamNG basis alignment, BeamNG unit scale, quaternion handedness,
the debug sphere method/signature/lifetime/stereo behaviour, which reference-space
reset events correspond to BeamNG recentering, SteamVR availability alongside the
user's selected OpenXR runtime, or callbacks permitted in a packed mod. Logs and a
fresh API dump with controllers enabled should be returned if any assumption fails.
