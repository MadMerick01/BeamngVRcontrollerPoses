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

## Choose an installation route

Use a Windows x64 artifact from a successful `Windows x64 layer` workflow run for
the simplest installation. Extract the archive to a temporary directory and verify
that its root contains:

```text
BeamNGVRPosesLayer.dll
XR_APILAYER_BEAMNG_controller_poses.json
scripts\
mod\
docs\
README.md
SHA256SUMS.txt
```

The workflow verifies the x64 PE image, loader negotiation export, manifest/DLL
pair, Python tests, and package hashes before uploading the archive. Those checks
do **not** constitute an in-headset VDXR or Quest 3 validation. Do not install an
artifact from a failed or incomplete workflow run.

Developers who prefer to build from source can use the following section instead.

## Build the 64-bit Windows layer from source

Install Visual Studio 2022 with the **Desktop development with C++** workload,
CMake, Git, and Python 3.10 or newer. In an **x64 Native Tools** PowerShell, build
and install the same official OpenXR SDK release used by CI:

```powershell
git clone --depth 1 --branch release-1.1.41 https://github.com/KhronosGroup/OpenXR-SDK-Source.git C:\src\OpenXR-SDK-Source
cmake -S C:\src\OpenXR-SDK-Source -B C:\src\openxr-build -G "Visual Studio 17 2022" -A x64 -DBUILD_TESTS=OFF -DBUILD_API_LAYERS=OFF -DBUILD_LOADER=ON -DCMAKE_INSTALL_PREFIX=C:\src\openxr-install
cmake --build C:\src\openxr-build --config Release --target install
```

Then, from the repository root, build this layer:

```powershell
cd openxr-layer
cmake --preset windows-x64 -DOpenXR_DIR=C:\src\openxr-install\cmake
cmake --build --preset windows-x64
cmake --install build\windows-x64 --config Release
```

`dist` must contain `BeamNGVRPosesLayer.dll` and
`XR_APILAYER_BEAMNG_controller_poses.json`. BeamNG is 64-bit, so a Win32 DLL will
not load. This Linux checkout has no Windows compiler; no untested binary is
committed.

## Install and temporarily activate the layer

Do not replace `openxr_loader.dll`, rename VDXR, or register this as a runtime.
Keep the DLL beside the manifest; activation is process-scoped and makes no
registry changes.

First confirm that VDXR is the selected OpenXR runtime:

```powershell
.\scripts\Confirm-VDXRRuntime.ps1
```

If Windows blocks unsigned local scripts, first run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This affects only the current PowerShell process and disappears when that window closes. From the tested package directory `C:\BeamNGVRcontrollerPosesTest`, launch the BeamNG launcher with the supplied script so its Vulkan child process inherits the API-layer environment:

```powershell
.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

The `-BeamNGExecutable` argument may point to the BeamNG launcher; for VR on the test PC it must be `D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe`, not `Bin64\BeamNG.drive.x64.exe`, because the launcher is where Vulkan is selected. From a source checkout built using the preceding section, point the same script at
`openxr-layer\dist`:

```powershell
.\openxr-layer\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .\openxr-layer\dist) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

The script captures OpenXR loader diagnostics in
`%TEMP%\BeamNG-OpenXR-loader.log`. Confirm that the log names
`XR_APILAYER_BEAMNG_controller_poses` and that VR starts and continues rendering
through the selected VDXR runtime. The layer's packet `source` is
`openxr-api-layer`.

For manual troubleshooting, the equivalent source-checkout launch is:

```powershell
$env:XR_API_LAYER_PATH=(Resolve-Path .\openxr-layer\dist)
$env:XR_ENABLE_API_LAYERS='XR_APILAYER_BEAMNG_controller_poses'
$env:XR_LOADER_DEBUG='all'
Start-Process 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe' -Wait -NoNewWindow
```

To disable the layer, close BeamNG and either close the launching PowerShell or run
`.\scripts\Disable-BeamNGVRPoses.ps1` from an artifact. From a source checkout, use
`.\openxr-layer\scripts\Disable-BeamNGVRPoses.ps1`. To remove an extracted artifact
and the layer diagnostic log, first copy out any logs you need, leave the package
directory, and run `Remove-BeamNGVRPoses.ps1 -PackageDirectory C:\path\to\package`.
See `docs/INSTALL_AND_TEST_GUIDE.md` for the step-by-step install/setup guide and `docs/FIRST_WINDOWS_TEST.md` for the complete first-test and recovery checklist.

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
pose and computes `beamngCameraWorld * controllerRelativeToHmd` using `getCameraPosition()` plus `getCameraQuat()`; `OpenXR.getCameraPosRotPredictedXYZXYZW()` is deliberately not used as the game-world camera transform, retains stale packet
rejection, exposes `getState()`, and draws the two bright-blue stereo spheres.

## Install the BeamNG mod and test

Create a directory such as `BeamNGVRControllerPoses` beneath the active BeamNG user
folder's `mods\unpacked` directory, then copy the **contents** of this repository's
`mod` directory into it. The resulting layout must be:

```text
<BeamNG user folder>\mods\unpacked\BeamNGVRControllerPoses\
  lua\ge\extensions\beamngVRControllerPoses.lua
  settings\beamngVRControllerPoses.json
```

The tested BeamNG user folder was `C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current`, making the tested unpacked-mod folder `C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses`. The active, versioned BeamNG user folder is shown by the launcher. Do not add an
extra nested `mod` directory. After BeamNG mounts the unpacked mod, load it in GE
Lua:

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


## First Quest 3 + VDXR result

The first real headset test used package `C:\BeamNGVRcontrollerPosesTest`, BeamNG `D:\SteamLibrary\steamapps\common\BeamNG.drive`, launcher `D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe`, game executable `D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe`, and user folder `C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current`. VDXR was active from `HKLM:\SOFTWARE\Khronos\OpenXR\1` with `ActiveRuntime=C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json`.

Confirmed working: Vulkan launch through the BeamNG launcher, VDXR chaining, explicit API-layer load, Quest controller pose capture, protocol-2 UDP to `127.0.0.1:44441`, GE Lua receipt, approximately 0.0--0.5 ms packet age, continuously increasing counters, and valid positions/orientations for both controllers. The remaining Stage 1 isolation point is rendering: if blue controller spheres and the red camera test sphere have world coordinates near `getCameraPosition()` but are still invisible in VR, treat that as evidence that `debugDrawer:drawSphere` is not submitted to BeamNG's stereoscopic VR pass. The smallest next BeamNG-native fallback should be a pair of transient scene objects or TSStatic/debug mesh objects updated from the same Lua world poses, not a protocol or API-layer redesign.
