# First Windows test and recovery runbook

This layer is an **explicit API layer**, not an OpenXR runtime. It must never
replace `openxr_loader.dll`, change `ActiveRuntime`, or be registered below the
OpenXR runtime registry keys.

## Confirmed test machine paths

```text
Package: C:\BeamNGVRcontrollerPosesTest
BeamNG install: D:\SteamLibrary\steamapps\common\BeamNG.drive
BeamNG launcher: D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe
BeamNG x64 game exe: D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe
BeamNG user folder: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current
Unpacked mod: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses
VDXR registry key: HKLM:\SOFTWARE\Khronos\OpenXR\1
VDXR ActiveRuntime: C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json
```

Required mod files:

```text
C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses\lua\ge\extensions\beamngVRControllerPoses.lua
C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses\settings\beamngVRControllerPoses.json
```

The listed paths are examples from the verified test computer. They must not be universally hard-coded.

## Recommended everyday launch

Use the package-root CMD launcher; do not manually open PowerShell and do not change persistent execution policy.

```text
Double-click Launch-BeamNGVRControllerPoses.cmd
Select Vulkan in the BeamNG launcher
Enter a map
Start BeamNG VR
```

The launcher validates the packaged manifest and DLL, prints the active OpenXR runtime manifest, warns if it does not look like VDXR, finds or asks for the root `BeamNG.drive.exe` launcher, and starts it with `XR_API_LAYER_PATH`, `XR_ENABLE_API_LAYERS`, and `XR_LOADER_DEBUG` set only for the launcher process tree. Use `scripts\Install-DesktopShortcut.ps1` or `scripts\Remove-DesktopShortcut.ps1` for the optional per-user desktop shortcut.

## Manual diagnostic launch

Run BeamNG through its launcher, not directly through `Bin64\BeamNG.drive.x64.exe`.
The launcher is essential on this system because Vulkan must be selected for
BeamNG VR, and the Vulkan child process must inherit the process-scoped OpenXR
API-layer environment.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

Set-Location 'C:\BeamNGVRcontrollerPosesTest'

.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

Then:

1. Wait for the normal BeamNG launcher.
2. Choose the Vulkan launch option.
3. Keep the launching PowerShell window open.
4. Allow the Vulkan BeamNG child process to inherit `XR_API_LAYER_PATH`,
   `XR_ENABLE_API_LAYERS`, and `XR_LOADER_DEBUG`.
5. Enter a map.
6. Start BeamNG VR through Virtual Desktop/VDXR.
7. Open the GE Lua console.
8. Run `extensions.load('beamngVRControllerPoses')`.
9. Inspect `dump(extensions.beamngVRControllerPoses.getState())`.
10. Compare with `dump(getCameraPosition())`.

Starting BeamNG later from Steam, a desktop shortcut, or an unrelated launcher
process will not necessarily inherit the process-scoped API-layer environment.

## What the first Quest 3 + VDXR test proved

Confirmed working: BeamNG starts through its launcher using Vulkan; VDXR remains
active; the explicit OpenXR API layer loads without preventing VR startup; the
layer captures Quest controller poses; protocol-2 UDP packets arrive continuously
at `127.0.0.1:44441`; the BeamNG GE Lua extension loads and receives packets;
packet age is approximately `0.0-0.5 ms`; update counters increase continuously;
and valid position/orientation data has been observed for both controllers.

The Stage 1 defect was the Lua world transform. `dump(getCameraPosition())`
returned approximately `vec3(-715.3673609, 106.5844518, 119.8104916)`, while the
old extension reported controller positions near `(0.48, 1.10, -0.97)`. Therefore
`OpenXR.getCameraPosRotPredictedXYZXYZW()` is not the complete BeamNG game-world
camera transform. The Lua bridge now composes protocol-2
`inverse(hmdInBase) * controllerInBase` with BeamNG `getCameraPosition()` and
`getCameraQuat()` and keeps quaternion order as `(x, y, z, w)`.

The settings include a red camera-relative diagnostic sphere one metre in front
of the BeamNG camera and the existing bright-blue left/right controller spheres.
If `getState()` shows final positions near `(-715, 106, 119)` but all spheres
remain invisible in VR, record that as evidence that `debugDrawer:drawSphere` is
not rendered in the required stereoscopic VR pass. The smallest BeamNG-native
alternative should be transient scene objects or tiny TSStatic/debug mesh objects
whose transforms are updated from the same Lua world poses.

## Logs to retain

```text
%TEMP%\BeamNG-OpenXR-loader.log
%TEMP%\BeamNGVRPosesLayer.log
%TEMP%\BeamNGVRPosesLauncher.log
C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\temp\beamng.log
```

Useful extraction commands:

```powershell
Get-Content "$env:TEMP\BeamNGVRPosesLayer.log" -Tail 200

Select-String `
  -Path "$env:TEMP\BeamNG-OpenXR-loader.log" `
  -Pattern 'XR_APILAYER|BEAMNG|OpenXR|error' `
  -CaseSensitive:$false
```

Also save the active VDXR runtime JSON manifest printed by
`Confirm-VDXRRuntime.ps1` and the settings JSON used for the run.

## Disable and recover

Close BeamNG and run `scripts\Disable-BeamNGVRPoses.ps1`, or simply close the
launching PowerShell. Start BeamNG from its normal launcher to confirm recovery.
No loader DLL needs restoration because the package does not replace it.
