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
Launch-BeamNGVRControllerPoses.cmd
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

## Recommended everyday launch

Do not replace `openxr_loader.dll`, rename VDXR, register this project as an
OpenXR runtime, or install it as a global implicit layer. For normal use, extract
the Windows x64 package and double-click the package-root launcher:

```text
Launch-BeamNGVRControllerPoses.cmd
```

The CMD launcher finds its own package directory, starts PowerShell with
`-NoLogo -NoProfile -ExecutionPolicy Bypass`, and applies that bypass only to the
temporary launcher process. It does not run `Set-ExecutionPolicy`, does not need
administrator privileges, and does not make persistent registry or environment
changes.

After double-clicking:

1. The launcher validates `BeamNGVRPosesLayer.dll` and
   `XR_APILAYER_BEAMNG_controller_poses.json`.
2. It prints the active OpenXR `ActiveRuntime` manifest from HKCU or, if HKCU is
   absent, HKLM.
3. It warns if the runtime does not look like Virtual Desktop VDXR, but it never
   changes the runtime automatically.
4. It finds the root BeamNG launcher, `BeamNG.drive.exe`, preferring a saved
   per-user path in `%LOCALAPPDATA%\BeamNGVRControllerPoses\launcher.json`, then
   Steam library discovery, common Steam locations, and finally a file-selection
   dialog.
5. It sets `XR_API_LAYER_PATH`, `XR_ENABLE_API_LAYERS`, and `XR_LOADER_DEBUG` only
   inside that PowerShell process so the BeamNG launcher and Vulkan child process
   inherit them. The variables disappear when the process tree closes.

Everyday procedure:

```text
Double-click Launch-BeamNGVRControllerPoses.cmd
Select Vulkan in the BeamNG launcher
Enter a map
Start BeamNG VR
```

The launcher intentionally starts the root `BeamNG.drive.exe`, not
`Bin64\BeamNG.drive.x64.exe`, because selecting Vulkan happens in the normal
BeamNG launcher. To select a different BeamNG installation later, run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\Launch-BeamNGVRControllerPoses.ps1 -ResetLauncherPath
```

Optional per-user desktop shortcut helpers are available and require no
administrator privileges:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-DesktopShortcut.ps1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\Remove-DesktopShortcut.ps1
```

Diagnostics are written to:

```text
%TEMP%\BeamNG-OpenXR-loader.log
%TEMP%\BeamNGVRPosesLayer.log
%TEMP%\BeamNGVRPosesLauncher.log
```

Example paths from the verified test computer are shown below. They are examples
only and must not be hard-coded for other users:

```text
Package: C:\BeamNGVRcontrollerPosesTest
BeamNG launcher: D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe
BeamNG x64 executable: D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe
BeamNG user folder: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current
Unpacked mod: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses
VDXR manifest: C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json
```

## Manual diagnostic launch

The older PowerShell path remains useful for troubleshooting and development. It
uses process-scoped environment variables and makes no registry changes. If you
are manually typing commands in a blocked PowerShell window, use a process-only
policy bypass for that window, never a persistent user or machine policy change:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

From the tested package directory `C:\BeamNGVRcontrollerPosesTest`:

```powershell
.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

From a source checkout built using the preceding section, point the same script at
`openxr-layer\dist`:

```powershell
.\openxr-layer\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .\openxr-layer\dist) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

The equivalent source-checkout diagnostic launch is:

```powershell
$env:XR_API_LAYER_PATH=(Resolve-Path .\openxr-layer\dist)
$env:XR_ENABLE_API_LAYERS='XR_APILAYER_BEAMNG_controller_poses'
$env:XR_LOADER_DEBUG='all'
Start-Process 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe' -Wait -NoNewWindow
```

To disable the layer, close BeamNG and either close the launching PowerShell or run
`Disable-BeamNGVRPoses.ps1`. To remove an extracted artifact and the layer
diagnostic log, first copy out any logs you need, leave the package directory, and
run `Remove-BeamNGVRPoses.ps1 -PackageDirectory C:\path\to\package`. See
`docs/INSTALL_AND_TEST_GUIDE.md` for the step-by-step install/setup guide and
`docs/FIRST_WINDOWS_TEST.md` for the complete first-test and recovery checklist.

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
