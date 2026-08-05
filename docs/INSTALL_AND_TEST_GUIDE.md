# BeamNG install and setup guide for a mod test

Use this guide to perform a first local test of the BeamNG VR controller pose mod
with Virtual Desktop's VDXR OpenXR runtime. The API layer is an explicit OpenXR
API layer: it must not replace `openxr_loader.dll`, it must not be registered as
an OpenXR runtime, and it must not change the system `ActiveRuntime` registry
value.

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

A source build uses `openxr-layer\dist` as the package directory and may not
contain the documentation or checksum files unless it has also been packaged.

## 2. Confirm VDXR before changing anything

Open a fresh PowerShell in the package root and run:

```powershell
.\scripts\Confirm-VDXRRuntime.ps1
```

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

1. Open the BeamNG launcher.
2. Note the active, versioned user folder shown by the launcher, for example
   `%LOCALAPPDATA%\BeamNG.drive\0.39`.
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

From an extracted workflow artifact, run:

```powershell
.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'C:\Program Files (x86)\Steam\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe'
```

From a source checkout after building and installing the layer to
`openxr-layer\dist`, run:

```powershell
.\openxr-layer\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .\openxr-layer\dist) `
  -BeamNGExecutable 'C:\Program Files (x86)\Steam\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe'
```

The launch script scopes these environment variables to the current PowerShell and
BeamNG child process:

```text
XR_API_LAYER_PATH
XR_ENABLE_API_LAYERS
XR_LOADER_DEBUG
```

It also captures OpenXR loader diagnostics in:

```text
%TEMP%\BeamNG-OpenXR-loader.log
```

## 6. Load the Lua extension in BeamNG

After BeamNG starts and the unpacked mod is mounted, open the GE Lua console and
run:

```lua
extensions.load('beamngVRControllerPoses')
```

You can inspect the live receiver and pose state with:

```lua
extensions.beamngVRControllerPoses.getState()
```

## 7. In-headset test checklist

Run this checklist in order:

1. Enter BeamNG VR through Virtual Desktop/VDXR.
2. Enable BeamNG OpenXR controllers.
3. Confirm `%TEMP%\BeamNG-OpenXR-loader.log` shows
   `XR_APILAYER_BEAMNG_controller_poses` being discovered and enabled from the
   intended manifest.
4. Confirm VR rendering continues through VDXR without an OpenXR instance or
   session creation failure.
5. Move the left controller while keeping the right controller still.
6. Move the right controller while keeping the left controller still.
7. Occlude or disable one controller and confirm only that controller's sphere
   disappears when tracking validity is lost.
8. Rotate your head and confirm controller positions remain stable relative to
   your hands.
9. Move the vehicle and confirm controller positions do not drift with vehicle
   motion.
10. Use BeamNG VR recenter and repeat the hand movement checks.
11. Stop the publisher or close BeamNG and confirm both spheres disappear after
    the configured stale timeout.

Expected result: both controllers render as bright-blue stereo spheres at the
correct controller-relative-to-HMD positions, with no mono overlay effect, no
head-turn offset, no vehicle-motion offset, and no recenter-induced drift.

## 8. Logs and data to save after the test

Save these files before uninstalling or cleaning up:

```text
%TEMP%\BeamNG-OpenXR-loader.log
%TEMP%\BeamNGVRPosesLayer.log
<BeamNG user folder>\temp\beamng.log
<VDXR runtime manifest printed by Confirm-VDXRRuntime.ps1>
<BeamNG user folder>\mods\unpacked\BeamNGVRControllerPoses\settings\beamngVRControllerPoses.json
```

Also copy the output of:

```lua
extensions.beamngVRControllerPoses.getState()
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
