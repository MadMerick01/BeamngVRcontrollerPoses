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

## PR #26 stationary-world rigid-transform test

`beamngOnly` remains the default. The explicitly selected
`baselineRigidTracking` candidate captures complete BeamNG-camera-world and
mapped native-HMD tracking-local poses, then evaluates:

```text
baselineWorldFromTracking = baselineBeamngCameraWorld * inverse(baselineTrackingHmdMapped)
candidateHmdWorld = baselineWorldFromTracking * currentTrackingHmdMapped
candidateControllerWorld = candidateHmdWorld * controllerRelativeToHmd * controllerCalibrationOffset
```

The native packet HMD is authoritative because it shares the controller base
space and sample time. The predicted OpenXR getter remains diagnostic-only.
This first test requires a stationary vehicle and stationary game camera; it
does not attempt to solve camera-anchor motion and room-scale motion together.

```lua
extensions.beamngVRControllerPoses.setHmdTranslationMode('baselineRigidTracking')
-- Immediate PR #25 fallback:
extensions.beamngVRControllerPoses.setHmdTranslationMode('beamngOnly')
```

Selecting the candidate establishes a fresh baseline. Existing red, green,
yellow, and white diagnostics remain; purple is the new complete-pose candidate
one metre forward. Blue controller spheres follow the selected mode.

## PR #27 baseline-rigid position with live BeamNG rotation

The additive `baselineRigidPositionBeamngRotation` mode deliberately combines
two independently headset-verified sources: HMD world position comes unchanged
from the PR #26 baseline-rigid tracking calculation, while HMD world orientation
comes from the corrected live BeamNG camera-to-world orientation
(`cameraAnchor.q`). The camera quaternion is normalized but is **not** inverted
again. The mode neither adds nor rotates another translation delta.

This remains a stationary-vehicle, stationary-game-camera test and does not yet
claim vehicle-motion support. Select it explicitly; selection clears the old
rigid baseline so the next valid native HMD sample establishes a fresh one:

```lua
extensions.load('beamngVRControllerPoses')
extensions.beamngVRControllerPoses.setHmdTranslationMode(
  'baselineRigidPositionBeamngRotation'
)
```

Remain still and face forward briefly, then translate on all three axes and
physically turn through approximately 90 and 180 degrees. Repeat translation at
each heading, return to the original pose, use BeamNG VR recenter, and repeat.
The cyan sphere is the hybrid candidate one metre forward; the purple PR #26
candidate remains available for comparison, and blue controller spheres follow
the selected hybrid. If a valid rigid candidate is unavailable, selection falls
back safely to `beamngOnly` for that frame rather than creating an origin pose.

## Choose an installation route

Use a Windows x64 artifact from a successful post-documentation `Windows x64 layer` workflow run for
the simplest installation. **Do not extract a new artifact over the previous PR #8
test package**: deleted launcher and shortcut files could survive as stale local
files and make the package look like it still supports the removed automatic
launcher. Rename or delete `C:\BeamNGVRcontrollerPosesTest`, create a clean
directory with that same name, download the new artifact, and extract it there.
Verify that its root contains:

```text
BeamNGVRPosesLayer.dll
XR_APILAYER_BEAMNG_controller_poses.json
scripts\
mod\
docs\
README.md
SHA256SUMS.txt
```

Also verify that the clean package does **not** contain
the removed root `.cmd` launcher. The removed PR #8 automatic launcher and
shortcut scripts are not the current procedure and are not required.

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

From the tested package directory, first confirm that VDXR is the selected OpenXR runtime:

```powershell
Set-Location 'C:\BeamNGVRcontrollerPosesTest'

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\Confirm-VDXRRuntime.ps1
```

The execution-policy bypass affects only the current PowerShell process,
disappears when that window closes, and does not permanently change system
policy. The tested runtime was
`C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json`.
The script reports whether `ActiveRuntime` came from HKCU or HKLM; a missing HKCU
OpenXR key is normal when the runtime is registered under HKLM.

Launch the normal BeamNG launcher with the supplied script so the launcher and
its Vulkan game process inherit the explicit API-layer environment:

```powershell
Set-Location 'C:\BeamNGVRcontrollerPosesTest'

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

This launches the normal BeamNG launcher. Select Vulkan there, keep the
PowerShell window open during the test, and let both the launcher and Vulkan game
process inherit `XR_API_LAYER_PATH`, `XR_ENABLE_API_LAYERS`, and
`XR_LOADER_DEBUG`. These environment variables are process-scoped. This procedure
does not change the OpenXR `ActiveRuntime` registry value, does not replace
`openxr_loader.dll`, and leaves VDXR as the active OpenXR runtime. For VR on the
test PC, do not use `D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe` because starting the Bin64 executable directly bypasses the launcher's Vulkan selection. From a source checkout built using the preceding section, point the same script at
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
session/base/time key is compatible. Protocol 2 now also carries an optional valid
VIEW pose in that base space, its flags, sample time, and tracking-space identity;
existing protocol-2 consumers can ignore this additive `hmd` member. The Lua
bridge uses the reconstructed BeamNG world camera as the controller parent and
computes `beamngWorld * controllerRelativeToHmd * controllerCalibrationOffset`.
Live headset evidence confirmed that
`OpenXR.getCameraPosRotPredictedXYZXYZW()` is available, but its values are a
tracking-local pose close to the OpenXR origin (about `(-0.013, 0.009, 0.013)` in
the observed run), not a complete BeamNG world camera pose. Directly treating it
as world space moved the diagnostic and both blue controller spheres near the
map origin. The validated and normalized getter is retained in `getState()` and
logs under explicit `predictedOpenXRTrackingLocal*` diagnostic names only; it
cannot select the controller parent and is not drawn as a magenta world sphere.
Tracking-space/session changes, time resets, pose discontinuities, extension
reload, and the exposed `resetHmdBaseline()` hook safely establish a new baseline
without adding standing height. Stale-packet rejection and the existing
red/green/yellow/white diagnostics, controller tripods, and origin lines remain.
This restoration does not claim to solve the original yaw-dependent translation
problem; it restores the last visible BeamNG-camera behavior after the predicted
pose experiment.

## Install the BeamNG mod and test

Create `BeamNGVRControllerPoses` beneath the active BeamNG user
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

For the complete first test: connect the Quest 3 through Virtual Desktop, confirm
VDXR, optionally confirm ordinary BeamNG Vulkan VR without the layer as a fresh
baseline, install the unpacked mod, run the manual PowerShell launch command,
select Vulkan in the normal BeamNG launcher, enter a map, start BeamNG VR, open
the GE Lua console, load `extensions.load('beamngVRControllerPoses')`, then
inspect `dump(extensions.beamngVRControllerPoses.getState())`. Confirm the red,
green, yellow, and white camera diagnostics remain visible and both bright-blue
spheres follow the physical controllers. The
`predictedOpenXRTrackingLocalPosition` field may remain near the OpenXR origin;
it is raw diagnostic evidence and is intentionally never drawn or used as a
world transform. Translate and rotate through the existing test matrix to gather
evidence, but do not interpret this corrective restoration as a fix for the
original yaw-dependent translation issue. Disabling/occluding either controller
hides its sphere, and stopping publication hides both within `staleAfterMs`.
Inspect `extensions.beamngVRControllerPoses.getState()` for the world transforms.
Calibration,
sphere size/colour, port, stale threshold and log cadence remain in
`mod/settings/beamngVRControllerPoses.json`.

## Tests and preserved fallback

Run `python -m pytest`. Tests cover transform composition, identity mapping,
multiple candidate spaces, lifecycle, validity flags, coherent time/base relative
math, protocol-2 encoding/decoding, stale counters, and UDP-without-a-receiver.
The fallback requires `pip install .[steamvr]` and SteamVR, but is not part of the
primary installation or test route.


## First Quest 3 + VDXR result

The first real headset test used package `C:\BeamNGVRcontrollerPosesTest`, BeamNG `D:\SteamLibrary\steamapps\common\BeamNG.drive`, launcher `D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe`, direct Bin64 executable that must not be used for this VR test `D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe`, and user folder `C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current`. VDXR was active from `HKLM:\SOFTWARE\Khronos\OpenXR\1` with `ActiveRuntime=C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json`.

Confirmed working: Vulkan launch through the BeamNG launcher, VDXR chaining, explicit API-layer load, Quest controller pose capture, protocol-2 UDP to `127.0.0.1:44441`, GE Lua receipt, approximately 0.0--0.5 ms packet age, continuously increasing counters, and valid positions/orientations for both controllers. The remaining Stage 1 isolation point is rendering: if blue controller spheres and the red camera test sphere have world coordinates near `core_camera.getPosition()` but are still invisible in VR, treat that as evidence that `debugDrawer:drawSphere` is not submitted to BeamNG's stereoscopic VR pass. The smallest next BeamNG-native fallback should be a pair of transient scene objects or TSStatic/debug mesh objects updated from the same Lua world poses, not a protocol or API-layer redesign.
