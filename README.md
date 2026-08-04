# BeamNG VR Controller Poses — Stage 1 (OpenXR / VDXR)

The primary architecture is now:

```text
BeamNG → the official OpenXR loader → this API layer → VDXR
                                      ↓ lock-free handoff
                                loopback UDP → BeamNG Lua
```

The layer observes the pose-action spaces which BeamNG already creates. It never
creates an application, instance, session, or competing OpenXR connection, and it
never changes a pose returned to BeamNG. SteamVR/OpenVR is **not required**. The
old Python OpenVR publisher remains only as an unsupported fallback entry point,
`beamng-vr-poses-openvr-fallback`, so the earlier work remains recoverable.

## Build the 64-bit Windows layer

Install Visual Studio 2022 (Desktop C++ workload), CMake, and the official OpenXR
SDK NuGet/CMake package, then use an **x64 Native Tools** prompt:

```powershell
cd openxr-layer
cmake --preset windows-x64 -DOpenXR_DIR=C:\path\to\OpenXR\lib\cmake\openxr
cmake --build --preset windows-x64
cmake --install build\windows-x64 --config Release
```

`dist` must contain `BeamNGVRPosesLayer.dll` and
`XR_APILAYER_BEAMNG_controller_poses.json`. BeamNG is 64-bit, so a Win32 DLL will
not load. This Linux checkout has no Windows compiler; no untested binary is
committed.

## Temporary activation (reversible)

> **Current status:** the former blocking `xrLocateSpace` mapping mutex has been
> replaced by immutable atomic snapshots plus session/space in-flight lifetime
> guards. The checked-in Windows job performs a clean MSVC x64 build, PE/export,
> manifest/package and test verification. Do not install a package from an
> unpassed job, and do not treat those checks as VDXR/Quest 3 validation. The
> first in-headset test and recovery procedure remain in
> `docs/FIRST_WINDOWS_TEST.md`.

Do not replace `openxr_loader.dll`, rename VDXR, or register this as a runtime.
Keep the DLL beside the manifest and launch BeamNG from the same PowerShell:

```powershell
$env:XR_API_LAYER_PATH=(Resolve-Path .\openxr-layer\dist)
$env:XR_ENABLE_API_LAYERS='XR_APILAYER_BEAMNG_controller_poses'
$env:XR_LOADER_DEBUG='all'
Start-Process 'C:\Program Files (x86)\Steam\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe' -Wait -NoNewWindow
```

`XR_LOADER_DEBUG=all` should name `XR_APILAYER_BEAMNG_controller_poses`; successful
VR startup and frames confirm that it chained to the currently selected VDXR
runtime. The layer's packet `source` is `openxr-api-layer`. To disable it fully,
close BeamNG and remove the variables (`Remove-Item Env:XR_API_LAYER_PATH,
Env:XR_ENABLE_API_LAYERS,Env:XR_LOADER_DEBUG`) or set
`BEAMNG_VR_POSES_DISABLE=1`. No registry change is made.

The publisher thread sends UDP to `127.0.0.1:44441`; a missing receiver is harmless.
High-frequency hooks do no file I/O and do not send network packets. Loader output
is the initial discovery/chaining diagnostic source; capture it from the launching
console. The background publisher writes its throttled five-second diagnostic to
`%TEMP%\BeamNGVRPosesLayer.log`.

## Capture rules

The layer tracks action type/name/localised name/subaction paths, action-set and
instance ownership, action spaces, session ownership, VIEW spaces, and destruction.
Only pose actions explicitly associated with `/user/hand/left` or
`/user/hand/right` qualify; creation order and a two-space assumption are never
used. `xrLocateSpace` is passed through first. Both POSITION_VALID and
ORIENTATION_VALID are required (TRACKED bits are preserved in `flags`); invalid
tracking immediately publishes invalidity rather than recycling an old pose.

For every qualifying locate, the layer calls the *downstream* `xrLocateSpace`
directly for its session-owned VIEW space using the exact same `baseSpace` and `XrTime`, then
computes `inverse(hmdInBase) * controllerInBase`. Samples are merged only when the
session/base/time key is compatible. The Lua bridge maps/calibrates that relative
pose and computes `beamngHmdWorld * controllerRelativeToHmd`, retains stale packet
rejection, exposes `getState()`, and draws the two bright-blue stereo spheres.

## Install the BeamNG mod and test

Copy `mod/` as an unpacked mod, preserving
`lua/ge/extensions/beamngVRControllerPoses.lua` and the settings file. In GE Lua:

```lua
extensions.load('beamngVRControllerPoses')
```

Enable BeamNG OpenXR controllers, enter VR through Virtual Desktop/VDXR, and move
each Quest 3 controller independently. Verify head turns, vehicle movement and VR
recenter do not introduce offsets; disabling/occluding either controller hides its
sphere, and stopping publication hides both within `staleAfterMs`. Inspect
`extensions.beamngVRControllerPoses.getState()` for the world transforms. Calibration,
sphere size/colour, port, stale threshold and log cadence remain in
`mod/settings/beamngVRControllerPoses.json`.

## Tests and preserved fallback

Run `python -m pytest`. Tests cover transform composition, identity mapping,
multiple candidate spaces, lifecycle, validity flags, coherent time/base relative
math, protocol-2 encoding/decoding, stale counters, and UDP-without-a-receiver.
The fallback requires `pip install .[steamvr]` and SteamVR, but is not part of the
primary installation or test route.
