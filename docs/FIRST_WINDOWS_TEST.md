# First Windows test and recovery runbook

This layer is an **explicit API layer**, not an OpenXR runtime. The package must
never replace `openxr_loader.dll`, change `ActiveRuntime`, or be registered below
the OpenXR runtime registry keys.

## Before the test

1. In a fresh PowerShell, run
   `scripts/Confirm-VDXRRuntime.ps1`. Record both the manifest and runtime library
   that it prints. This checks the current-user `ActiveRuntime`; also inspect the
   machine-wide value if VDXR was installed for all users:
   `Get-ItemProperty 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -Name ActiveRuntime`.
2. Start the mod normally once **without** the layer, to establish that VDXR and
   BeamNG VR work.
3. Run `Enable-BeamNGVRPoses.ps1 -PackageDirectory C:\path\to\package
   -BeamNGExecutable 'C:\...\BeamNG.drive.x64.exe'`. The environment variables are
   scoped to that PowerShell and its BeamNG child process. `XR_LOADER_DEBUG=all`
   is captured by `Tee-Object` in `%TEMP%\BeamNG-OpenXR-loader.log`.

## Exact first-test order

1. Verify VDXR is active with `Confirm-VDXRRuntime.ps1`.
2. In the loader log, verify discovery and enablement of
   `XR_APILAYER_BEAMNG_controller_poses` from the intended manifest.
3. Start BeamNG and confirm the loader continues from the layer into the VDXR
   runtime without an instance/session creation failure.
4. Enable controllers and verify diagnostics identify left and right pose action
   spaces (not merely arbitrary spaces).
5. Move and occlude each hand independently; verify valid left/right poses, and
   invalidity while tracking validity bits are absent.
6. In `beamng.log`, verify Lua receives monotonically increasing protocol-2
   packets whose source is `openxr-api-layer`.
7. Verify the Lua state reports a BeamNG predicted HMD world transform.
8. Verify the renderer accepts both bright-blue sphere submissions.
9. Check that both spheres appear in both eyes, rather than as a mono overlay.
10. Test axis direction and scale, then rotate the head, move the vehicle, recenter,
    and repeat. Controller-to-head motion must remain stable through vehicle motion.

Return these complete, unedited files after the run:

* `%TEMP%\BeamNG-OpenXR-loader.log`
* `%TEMP%\BeamNGVRPosesLayer.log`
* `%LOCALAPPDATA%\BeamNG.drive\0.39\temp\beamng.log` (or the `beamng.log` under
  the actual versioned BeamNG user folder printed by the launcher)
* the active VDXR runtime JSON manifest printed by `Confirm-VDXRRuntime.ps1`
* `mod/settings/beamngVRControllerPoses.json` used for the test

Also export `extensions.beamngVRControllerPoses.getState()` before moving, after
moving each hand, after head rotation, after vehicle movement, and after recenter.

## Disable and recover

Close BeamNG and run `scripts/Disable-BeamNGVRPoses.ps1`, or simply close the
launching PowerShell. Start BeamNG from its normal launcher to confirm recovery.
If VR still fails, confirm `XR_API_LAYER_PATH`, `XR_ENABLE_API_LAYERS`, and
`XR_LOADER_DEBUG` are absent in the new process and that `ActiveRuntime` still
points to the same VDXR JSON. No loader DLL needs restoration because the package
does not replace it. To uninstall the test package, run
`Remove-BeamNGVRPoses.ps1 -PackageDirectory C:\path\to\package` after copying out
the logs.

## Build status and security inspection

The repository contains source only. The Windows x64 GitHub Actions job now
performs the clean build, Python tests, PE machine/export checks, manifest/package
validation, and hashes before uploading an artifact. A package is verified only
when that job passes. A local equivalent is:

```powershell
Remove-Item openxr-layer\build\windows-x64 -Recurse -Force -ErrorAction Ignore
cmake --preset windows-x64 -DOpenXR_DIR=C:\path\to\OpenXR\lib\cmake\openxr
cmake --build --preset windows-x64 --clean-first
cmake --install openxr-layer\build\windows-x64 --config Release
python -m pytest
dumpbin /headers openxr-layer\dist\BeamNGVRPosesLayer.dll | Select-String 'machine \(x64\)'
Get-FileHash openxr-layer\dist\BeamNGVRPosesLayer.dll -Algorithm SHA256
Get-FileHash openxr-layer\dist\XR_APILAYER_BEAMNG_controller_poses.json -Algorithm SHA256
Compress-Archive openxr-layer\dist\* BeamNGVRcontrollerPoses-windows-x64.zip
```

Static inspection confirms that the sole exported negotiation entry point is
`extern "C"`, `__declspec(dllexport)`, `XRAPI_ATTR`, and `XRAPI_CALL`; the next
`xrGetInstanceProcAddr` is taken from `nextInfo`, stored per instance, and used to
populate downstream dispatch. The internal VIEW locate calls that stored
downstream pointer, not the hook. Intercepts return the downstream result and do
not alter BeamNG's location. Position and orientation VALID bits are both required.
The UDP representation is JSON protocol 2 with named scalar fields, so it has no
C/C++ padding or ABI dependence.

The former global-mutex release blocker is corrected: `xrLocateSpace` reads an
immutable atomically published registry and uses atomic session/space in-flight
guards. Destruction unpublishes and retires handles before waiting outside the
writer lock, and downstream calls occur without project locks. See
`docs/CONCURRENCY.md` for the full before/after audit. This is approval to produce
the CI artifact after every automated check passes; it is **not** a claim of VDXR
or Quest 3 validation. Do not install artifacts from failed or incomplete jobs.
