# BeamNG install and setup guide for a mod test

Use this guide to perform a first local test of the BeamNG VR controller pose mod
with Virtual Desktop's VDXR OpenXR runtime. The API layer is an explicit OpenXR
API layer: it must not replace `openxr_loader.dll`, it must not be registered as
an OpenXR runtime, and it must not change the system `ActiveRuntime` registry
value.

## PR #26 stationary-world acceptance test

Park the vehicle and do not move the game camera controls. Load the extension,
then run:

```lua
extensions.beamngVRControllerPoses.setHmdTranslationMode('baselineRigidTracking')
```

Face forward and remain still for the fresh baseline. Move the headset right,
left, forward, backward, up, and down, returning after each movement. Repeat
lateral and forward/back movement at the baseline heading, 90 degrees left,
180 degrees, 90 degrees right, and 360 degrees/baseline. Success means the
purple sphere remains attached to the rendered HMD, blue spheres remain aligned
with independent physical controllers, movement directions do not change with
head yaw, all positions return to baseline, and no drift accumulates.

Save `dump(extensions.beamngVRControllerPoses.getState())` and `beamng.log`. On
failure, do not tune gains, smoothing, or offsets; report constant axis
permutation, constant sign inversion, yaw-dependent rotation, doubled
translation, or failure to return. Restore PR #25 immediately with
`extensions.beamngVRControllerPoses.setHmdTranslationMode('beamngOnly')`.

## 1. Prerequisites

Before installing the mod, prepare the Windows test machine with:

- BeamNG.drive with VR support installed.
- A Quest 3 or compatible OpenXR controller setup connected through Virtual
  Desktop.
- Virtual Desktop's VDXR runtime selected as the active OpenXR runtime.
- A Windows x64 release artifact from a successful `Windows x64 layer` workflow
  run, or a locally built `openxr-layer\dist` directory.
- PowerShell launched from the extracted artifact root or from the repository
  root, depending on the installation route.

The package or build output used for testing must contain these files:

```text
BeamNGVRPosesLayer.dll
XR_APILAYER_BEAMNG_controller_poses.json
scripts\
mod\
docs\
README.md
SHA256SUMS.txt
```

Before extracting a new artifact, rename or delete any previous `C:\BeamNGVRcontrollerPosesTest`, create a clean directory with that name, download the artifact from a successful post-documentation `main` workflow, and extract into the clean directory. Do not extract over the previous PR #8 test package because deleted launcher and shortcut files could remain as stale local files. Confirm the clean package does not contain the removed root `.cmd` launcher.

A source build uses `openxr-layer\dist` as the package directory and may not
contain the documentation or checksum files unless it has also been packaged.

## 2. Confirm VDXR before changing anything

Open a fresh PowerShell in the package root and run:

```powershell
Set-Location 'C:\BeamNGVRcontrollerPosesTest'

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\Confirm-VDXRRuntime.ps1
```

The execution-policy bypass applies only to the current PowerShell process,
disappears when that PowerShell window closes, and does not permanently change
system policy. The expected tested runtime is
`C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json`.
The script reports whether `ActiveRuntime` came from HKCU or HKLM; a missing HKCU
OpenXR key is normal when the runtime is registered under HKLM.

For a source checkout, run:

```powershell
.\openxr-layer\scripts\Confirm-VDXRRuntime.ps1
```

Record the runtime manifest path and runtime library path printed by the script.
If VDXR was installed machine-wide, also inspect:

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -Name ActiveRuntime
```

Start BeamNG once without this layer enabled and confirm BeamNG VR works normally
through VDXR. This baseline makes it easier to separate layer or mod issues from
runtime, headset, or game setup issues.

## 3. Install the BeamNG unpacked mod

Verified example paths from the successful test computer:

```text
Package directory: C:\BeamNGVRcontrollerPosesTest
BeamNG installation: D:\SteamLibrary\steamapps\common\BeamNG.drive
Normal BeamNG launcher: D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe
Direct Bin64 executable that must not be used for this VR test: D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe
BeamNG user folder: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current
Unpacked mod folder: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses
```

1. Open the BeamNG launcher.
2. Note the active, versioned user folder shown by the launcher, for example
   `C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current`.
3. Create this directory below that user folder:

   ```text
   mods\unpacked\BeamNGVRControllerPoses
   ```

4. Copy the **contents** of this repository's `mod` directory into that new
   directory.

The final layout must be:

```text
<BeamNG user folder>\mods\unpacked\BeamNGVRControllerPoses\
  lua\ge\extensions\beamngVRControllerPoses.lua
  settings\beamngVRControllerPoses.json
```

Do not create an extra nested `mod` directory. If the Lua file ends up at
`BeamNGVRControllerPoses\mod\lua\...`, BeamNG will not mount it as intended.

## 4. Review test settings

Open:

```text
<BeamNG user folder>\mods\unpacked\BeamNGVRControllerPoses\settings\beamngVRControllerPoses.json
```

For a first test, keep the default UDP port and stale timeout unless another local
tool already uses the same port. The OpenXR layer publishes pose packets to
`127.0.0.1:44441`, and the Lua extension reads the same port by default.

## 5. Launch BeamNG with the API layer enabled

Use only the manual PowerShell launch procedure. From an extracted workflow
artifact, run:

```powershell
Set-Location 'C:\BeamNGVRcontrollerPosesTest'

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

From a source checkout after building and installing the layer to
`openxr-layer\dist`, run:

```powershell
.\openxr-layer\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .\openxr-layer\dist) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

The launch script scopes these environment variables to the current PowerShell and
BeamNG child process:

```text
XR_API_LAYER_PATH
XR_ENABLE_API_LAYERS
XR_LOADER_DEBUG
```

This launches the normal BeamNG launcher; select Vulkan in that launcher and
keep the PowerShell window open during the test. The launcher and Vulkan game
process inherit the explicit OpenXR API-layer environment. The variables are
process-scoped. This procedure does not change the OpenXR `ActiveRuntime`
registry value, does not replace `openxr_loader.dll`, and leaves VDXR as the
active OpenXR runtime. Do not use
`D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe`
because it bypasses the launcher's Vulkan selection.

It also captures OpenXR loader diagnostics in:

```text
%TEMP%\BeamNG-OpenXR-loader.log
```

## 6. Load the Lua extension in BeamNG

After the BeamNG launcher appears, choose Vulkan, enter a map, start VR through VDXR, and after the unpacked mod is mounted, open the GE Lua console and
run:

```lua
extensions.load('beamngVRControllerPoses')
```

You can inspect the live receiver and pose state with:

```lua
dump(extensions.beamngVRControllerPoses.getState())
```

## 7. In-headset test checklist

### PR #14 room-scale translation diagnostic

This PR is a controlled in-headset coordinate-space diagnostic, not a final
solution based only on model tests. The headset result will determine the one
permanent transform; the temporary alternative spheres and modes can then be
removed.

> **PR #15 headset-test result:** The live-head-vector lateral-step
> accumulation did not correct the controller positions in headset testing and
> was removed. The modes and diagnostics below remain the PR #14 diagnostic
> baseline; no replacement transform, gain, smoothing, or controller offset was
> introduced.

Follow this exact procedure:

1. Download a fresh artifact generated after PR #14.
2. Extract it into a new empty folder.
3. Install the freshly packaged Lua/settings files.
4. Launch using the existing manual PowerShell procedure.
5. Enter VR and recenter while looking straight ahead.
6. Load the Lua extension and remain still while the HMD baseline is established.
7. Observe the red, green and yellow spheres.
8. Move the headset right/left, forward/backward and up/down, holding still at each position.
9. Record which sphere remains rigidly one metre ahead of the physical headset.
10. Test each translation mode using `setHmdTranslationMode`.
11. Verify that blue controller spheres follow the mode selected.
12. Repeat after VR recenter.

Use the GE Lua console to select each mode without rebuilding or restarting:

```lua
extensions.beamngVRControllerPoses.setHmdTranslationMode('beamngOnly')
extensions.beamngVRControllerPoses.setHmdTranslationMode('beamngPlusHmdDelta')
extensions.beamngVRControllerPoses.setHmdTranslationMode('beamngMinusHmdDelta')
```

Changing modes deliberately clears the current HMD baseline. Remain still long
enough for the next valid HMD sample to establish the new baseline before moving.
The red sphere represents `beamngOnly`, green represents
`beamngPlusHmdDelta`, and yellow represents `beamngMinusHmdDelta`; all three are
rendered simultaneously. The blue controller spheres use only the selected
mode.

Three additional spheres isolate the BeamNG camera quaternion from the HMD
translation modes. They are composed only from the `beamngOnly` camera anchor:
red is one metre to camera-right (`[1,0,0]`), green is one metre camera-forward
(`[0,1,0]`), and blue is one metre camera-up (`[0,0,1]`). Translate the headset
at several headings and record whether any sphere swings onto another camera
axis. Their world positions are exposed in
`diagnostics.cameraAxisSphereWorldPositions`. Configure the check with
`cameraAxisSpheres.enabled`, `cameraAxisSpheres.distance`, and
`cameraAxisSpheres.diameter` in `settings/beamngVRControllerPoses.json`.

Run this checklist in order:

1. Connect the Quest 3 through Virtual Desktop.
2. Confirm VDXR with `Confirm-VDXRRuntime.ps1`.
3. Confirm ordinary BeamNG Vulkan VR works without the layer if establishing a
   fresh baseline.
4. Install the unpacked mod in the verified BeamNG user folder.
5. Run the manual `Enable-BeamNGVRPoses.ps1` command.
6. Select Vulkan in the normal BeamNG launcher.
7. Enter a map.
8. Start BeamNG VR.
9. Open the GE Lua console.
10. Run:

    ```lua
    extensions.load('beamngVRControllerPoses')
    ```

11. Inspect:

    ```lua
    dump(extensions.beamngVRControllerPoses.getState())
    dump(core_camera.getPosition())
    ```

12. Check that the red camera diagnostic sphere is approximately one metre from
    the camera; the bright-blue spheres follow the controllers; controller world
    positions are near the non-zero BeamNG camera position rather than near
    `(0,0,0)`; each controller moves independently; and spheres remain correctly
    positioned during head rotation, vehicle movement, and VR recentering.

Expected result: both controllers render as bright-blue stereo spheres, and the
optional red camera test sphere renders one metre in front of the BeamNG camera
at the correct controller-relative-to-HMD positions, with no mono overlay effect,
no head-turn offset, no vehicle-motion offset, and no recenter-induced drift.

## 8. Logs and data to save after the test

Save these files before uninstalling or cleaning up:

```text
%TEMP%\BeamNG-OpenXR-loader.log
%TEMP%\BeamNGVRPosesLayer.log
C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\temp\beamng.log
<VDXR runtime manifest printed by Confirm-VDXRRuntime.ps1>
<BeamNG user folder>\mods\unpacked\BeamNGVRControllerPoses\settings\beamngVRControllerPoses.json
```

Also copy the output of:

```lua
dump(extensions.beamngVRControllerPoses.getState())
```

Capture that state before moving, after moving each hand, after head rotation,
after vehicle movement, and after VR recenter.

## 9. Disable or remove the test setup

Close BeamNG first. To disable the API layer for future launches, either close the
PowerShell window that launched BeamNG or run this from an artifact:

```powershell
.\scripts\Disable-BeamNGVRPoses.ps1
```

From a source checkout, run:

```powershell
.\openxr-layer\scripts\Disable-BeamNGVRPoses.ps1
```

To remove an extracted artifact and its diagnostic log after copying out any logs
you need, leave the package directory and run:

```powershell
.\scripts\Remove-BeamNGVRPoses.ps1 -PackageDirectory C:\path\to\package
```

Start BeamNG from its normal launcher afterward to confirm the base VR setup still
works. If VR does not start, verify the new process no longer has `XR_API_LAYER_PATH`,
`XR_ENABLE_API_LAYERS`, or `XR_LOADER_DEBUG`, and confirm `ActiveRuntime` still
points to the same VDXR JSON recorded before the test.

## 10. Common setup mistakes

- **Mod not loading:** check for an accidental `BeamNGVRControllerPoses\mod\lua`
  nesting error and move `lua` and `settings` directly under the unpacked mod
  directory.
- **Layer not discovered:** confirm `XR_API_LAYER_PATH` points to the directory
  containing both the manifest and `BeamNGVRPosesLayer.dll`.
- **Wrong runtime:** run `Confirm-VDXRRuntime.ps1` again and confirm VDXR is still
  the active OpenXR runtime.
- **No spheres:** confirm the GE Lua extension is loaded, the UDP port in settings
  matches the publisher, and `beamng.log` shows protocol-2 packets from
  `openxr-api-layer`.
- **Spheres drift after recenter or vehicle movement:** save all required logs and
  `getState()` snapshots because this indicates a transform or calibration issue
  that needs investigation.


The loader environment is process scoped. A later BeamNG launch from any unrelated process will not necessarily inherit `XR_API_LAYER_PATH`, `XR_ENABLE_API_LAYERS`, or `XR_LOADER_DEBUG`.

Use these commands to capture the most relevant log sections:

```powershell
Get-Content "$env:TEMP\BeamNGVRPosesLayer.log" -Tail 200

Select-String `
  -Path "$env:TEMP\BeamNG-OpenXR-loader.log" `
  -Pattern 'XR_APILAYER|BEAMNG|OpenXR|error' `
  -CaseSensitive:$false
```

## Axis-tripod headset diagnostic

1. Download a new Windows x64 artifact built from this change and extract it to
   a new, empty folder.
2. Install its updated `lua` and `settings` directories as described above, then
   launch BeamNG with the established manual PowerShell procedure.
3. Enter VR, face straight ahead, recenter, and load
   `extensions.beamngVRControllerPoses`.
4. Confirm all five spheres have tripods whose red X, green Y, and blue Z sticks
   begin at the sphere centres and have visible endpoint markers. On the camera
   tripods, X should point right, Y forward, and Z upward.
5. Rotate your head without translating. The red, green, and yellow tripods
   should rotate consistently with the corrected camera orientation. Controller
   tripods must not copy head rotation unless their physical controllers rotate.
6. Hold both controllers still while rotating your head; each tripod should stay
   attached to its physical controller. Then rotate one controller and confirm
   only that controller tripod rotates.
7. Point each controller naturally forward and record which coloured axis aligns
   most closely with its physical pointing direction.
8. Translate your head right/left, forward/backward, and up/down. Note whether a
   tripod or grey origin line shows lateral motion being interpreted as depth.
9. Without recentering, turn your head 90 degrees left and right and repeat all
   translation checks.
10. Run the following at the centred position, after right translation, after
    forward translation, at 90-degree right yaw, and at 90-degree left yaw; save
    each result with the corresponding logs:

```lua
dump(extensions.beamngVRControllerPoses.getState())
```

Tripods and origin lines can be hidden independently without resetting tracking:

```lua
extensions.beamngVRControllerPoses.setAxisTripodsEnabled(false)
extensions.beamngVRControllerPoses.setDiagnosticTripodsEnabled(false)
extensions.beamngVRControllerPoses.setControllerTripodsEnabled(false)
extensions.beamngVRControllerPoses.setOriginLinesEnabled(false)
```


## PR #34 corrected native GE Lua camera composition headset acceptance

Run this exact first test:

```lua
extensions.load('beamngVRControllerPoses')

extensions.beamngVRControllerPoses.startGeluaCameraAnchorCapture()

extensions.beamngVRControllerPoses.setHmdTranslationMode(
  'geluaNativeCameraComposition'
)

dump(extensions.beamngVRControllerPoses.getGeluaCameraAnchorCaptureState())

dump(extensions.beamngVRControllerPoses.getState())
```

1. Recenter VR while facing forward.
2. Confirm capture is installed/available, the pair is complete, and setter sequence is positive.
3. Identify the vivid electric-violet native sphere one metre forward.
4. Turn left/right, look up/down, and tilt for roll; confirm violet follows rather than moving inversely.
5. Confirm both blue spheres remain aligned with the real controllers.
6. Translate left/right, forward/back, and up/down.
7. Rotate the game camera with the controller stick.
8. Walk using controller-stick locomotion.
9. Combine physical movement and stick movement.
10. Confirm no inverse rotation, double translation, or yaw-dependent drift.
11. Confirm PR #28 orange remains available with `setHmdTranslationMode('baselineRigidPositionBeamngRotationRebased')`.
12. Save `getState()`, capture state, and relevant logs; then call `stopGeluaCameraAnchorCapture()`.

PR #33 live testing already established wrapper replacement, capture pairing, and
the exact `anchor + predicted` position. A successful static/CI test still does
not establish that PR #34's corrected artifact behaves properly in a headset; do
not claim complete success before this acceptance test passes.
