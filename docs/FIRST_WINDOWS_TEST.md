# First Windows test and recovery runbook

This layer is an **explicit API layer**, not an OpenXR runtime. It must never
replace `openxr_loader.dll`, change `ActiveRuntime`, or be registered below the
OpenXR runtime registry keys. The only documented first-test path is the manual
PowerShell procedure that launches the normal BeamNG launcher and then selects
Vulkan.

## Confirmed test machine paths

```text
Package directory: C:\BeamNGVRcontrollerPosesTest
BeamNG installation: D:\SteamLibrary\steamapps\common\BeamNG.drive
Normal BeamNG launcher: D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe
Direct Bin64 executable that must not be used for this VR test: D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe
BeamNG user folder: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current
Unpacked mod folder: C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses
Expected VDXR runtime: C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json
```

Required mod layout:

```text
C:\Users\fenci\AppData\Local\BeamNG\BeamNG.drive\current\mods\unpacked\BeamNGVRControllerPoses\
  lua\ge\extensions\beamngVRControllerPoses.lua
  settings\beamngVRControllerPoses.json
```

Do not create an accidental extra directory such as
`BeamNGVRControllerPoses\mod\lua\...`; BeamNG will not mount the extension from
that layout.

## Clean artifact extraction

Do **not** extract a new artifact over the previous PR #8 test package. Deleted
automatic launcher and shortcut files could remain as stale local files and make
the package contents misleading.

For the next test:

1. Rename or delete `C:\BeamNGVRcontrollerPosesTest`.
2. Create a clean directory with that same name.
3. Download the artifact from a successful post-documentation `main` workflow.
4. Extract the new artifact into the clean directory.
5. Confirm the package contains:

   ```text
   BeamNGVRPosesLayer.dll
   XR_APILAYER_BEAMNG_controller_poses.json
   scripts\
   mod\
   docs\
   README.md
   SHA256SUMS.txt
   ```

6. Confirm it does not contain the removed root `.cmd` launcher.

## Confirm VDXR

Run this from a fresh PowerShell window:

```powershell
Set-Location 'C:\BeamNGVRcontrollerPosesTest'

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\Confirm-VDXRRuntime.ps1
```

The execution-policy bypass applies only to the current PowerShell process,
disappears when that PowerShell window closes, and does not permanently change
system policy. The script reports whether `ActiveRuntime` came from HKCU or HKLM;
a missing HKCU OpenXR key is normal when the runtime is registered under HKLM.
The tested runtime was
`C:\Program Files\Virtual Desktop Streamer\OpenXR\virtualdesktop-openxr.json`.

## Exact launch procedure that worked

Run BeamNG through its normal launcher, not directly through
`D:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe`.
The direct Bin64 executable must not be used for this VR test because it bypasses
the launcher's Vulkan selection.

```powershell
Set-Location 'C:\BeamNGVRcontrollerPosesTest'

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory (Resolve-Path .) `
  -BeamNGExecutable 'D:\SteamLibrary\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

This command launches the normal BeamNG launcher. Select Vulkan in that launcher
and keep the PowerShell window open during the test. The launcher and Vulkan game
process inherit the explicit OpenXR API-layer environment. Those environment
variables are process-scoped; the procedure does not change the OpenXR
`ActiveRuntime` registry value, does not replace `openxr_loader.dll`, and leaves
VDXR as the active OpenXR runtime.

## Complete first-test order

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

## What the first Quest 3 + VDXR test proved

Confirmed working: BeamNG starts through its launcher using Vulkan; VDXR remains
active; the explicit OpenXR API layer loads without preventing VR startup; the
layer captures Quest controller poses; protocol-2 UDP packets arrive continuously
at `127.0.0.1:44441`; the BeamNG GE Lua extension loads and receives packets;
packet age is approximately `0.0-0.5 ms`; update counters increase continuously;
and valid position/orientation data has been observed for both controllers.

## Logs to retain

```text
%TEMP%\BeamNG-OpenXR-loader.log
%TEMP%\BeamNGVRPosesLayer.log
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

## Disable and recover

Close BeamNG and either run `scripts\Disable-BeamNGVRPoses.ps1` or close the
launching PowerShell. Start BeamNG from its normal launcher to confirm recovery.
No loader DLL needs restoration because the package does not replace it.

## Fixed tracking-base HMD translation acceptance test

This test answers only whether the 360-degree yaw dependence has been removed.
If unrelated scale or vertical issues appear, record them separately; do not
conceal them with gains.

1. Download a fresh post-PR Windows artifact.
2. Extract it into a new empty folder.
3. Install the updated Lua and settings files.
4. Launch BeamNG through the established manual PowerShell procedure above.
5. Enter a map and enable VR.
6. Face the previously identified perfect map direction.
7. Load the extension and remain still while the baseline is established.
8. Confirm the selected mode with:

   ```lua
   dump(
     extensions.beamngVRControllerPoses.getState()
       .diagnostics.selectedHmdTranslationMode
   )
   ```

   The expected value is `beamngFixedBaseHmdDelta`.
9. Confirm the white sphere and blue controller spheres begin correctly.
10. Translate the headset right/left, forward/backward and up/down.
11. Turn approximately 90 degrees without recentering and repeat.
12. Repeat at 180, 270 and 360 degrees.
13. At every heading, translate the headset laterally and hold still.
14. The correction succeeds if lateral movement no longer becomes depth
    movement; the result no longer depends on map-facing direction; the white
    sphere remains rigidly attached to the physical headset; blue spheres remain
    aligned with the physical controllers; 180 degrees no longer reverses
    translation; and returning to 360 degrees produces no discontinuity.
15. Repeat after BeamNG VR recenter.
16. Repeat from a different spawn position in the same map.
17. Repeat in a second map.

Restore the previous camera-only baseline immediately, if needed, with:

```lua
extensions.beamngVRControllerPoses.setHmdTranslationMode('beamngOnly')
```
