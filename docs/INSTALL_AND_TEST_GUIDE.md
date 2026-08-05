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
    dump(getCameraPosition())
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
