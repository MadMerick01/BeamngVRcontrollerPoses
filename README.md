# BeamngVRcontrollerPoses — Stage 1

This proof of concept publishes role-labelled SteamVR HMD/controller tracking over
localhost UDP and draws two 7 cm blue world-space diagnostics in BeamNG. It has no
hands, IK, input mapping, raycasts, game hooks, or hard-coded offsets. See
[`docs/INSPECTION.md`](docs/INSPECTION.md) for dump evidence, alternatives, and
explicit unknowns.

> **Validation status:** transform and transport code is source-tested here. It is
> not honest to claim the milestone complete until the OpenVR/OpenXR coexistence,
> BeamNG HMD getter convention, and `debugDrawer` stereo behaviour pass the listed
> in-headset tests.

## Components and generated files

* `beamng-vr-poses` is the Python console program. It uses OpenVR's background app
  mode, reads all devices from the same standing tracking universe, labels
  controllers by runtime role, and transmits validity, monotonic time and counter.
* `beamngVRControllerPoses.lua` is the non-blocking GE Lua receiver/converter and
  diagnostic renderer. `getState()` exposes both final poses to later mod code.
* No binary is checked in. `pip` creates the platform launcher
  `beamng-vr-poses.exe` (a Python entry-point wrapper, not a native DLL). A mod ZIP
  is only an archive of the `mod/` tree.

## Build and install

On the Windows PC running BeamNG, with Python 3.10+ and SteamVR installed:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install ".[steamvr]"
python -m pytest                 # also install .[test], or use a dev checkout
Compress-Archive -Path mod\* -DestinationPath beamngVRControllerPoses.zip
```

Copy the ZIP to
`%LOCALAPPDATA%\BeamNG.drive\0.39\mods\unpacked\beamngVRControllerPoses\` and
extract it so that `lua/ge/extensions/beamngVRControllerPoses.lua` and
`settings/beamngVRControllerPoses.json` are directly below that directory. An
unpacked directory is recommended for calibration. In the BeamNG GE Lua console:

```lua
extensions.load('beamngVRControllerPoses')
```

Start SteamVR first, then run `beamng-vr-poses`. The provider defaults to
`127.0.0.1:44441`, 90 Hz and a five-second diagnostic cadence. Run
`beamng-vr-poses --help` for overrides. UDP is loopback-only; do not bind publicly.

## Configuration

`mod/settings/beamngVRControllerPoses.json` documents every calibration value.
Indices are Lua-style: `axisOrder=[1,2,3]`; signs are independently selectable.
`quaternionBasis=[x,y,z,w]` performs `basis * raw * inverse(basis)`. Position
mapping happens before relative composition. Per-hand position (BeamNG units) and
rotation offsets are applied in controller-local space. Defaults intentionally
contain no hidden correction. Sphere diameter is `0.07` BeamNG units and RGBA is
bright blue. Packets older than 150 ms invalidate both hands; individual OpenVR
invalidity hides only that hand.

The packet is UTF-8 JSON with protocol `v=1`, `counter`, provider
`monotonic_ns`, source, and `hmd`/`left`/`right` objects. Each valid pose has
`p=[x,y,z]`, `q=[x,y,z,w]`, and `valid=true`. Provider time is diagnostic only;
the receiver uses local arrival age, avoiding unsynchronised-clock errors.

## Exact in-game Stage 1 test

1. Enable BeamNG VR controllers (`openXRuseControllers`), start SteamVR, BeamNG VR,
   the provider, and load the extension. Confirm five-second logs show three valid
   devices and sub-150 ms age.
2. Move each controller separately; only its sphere may move. Move right, up, and
   toward the HMD. Adjust only the documented axis order/sign/basis if directions
   disagree, record the final values, then reload the extension.
3. Rotate each stationary controller and inspect
   `dump(extensions.beamngVRControllerPoses.getState())`; its orientation must
   change while its centre stays fixed.
4. Turn and translate the head while holding controllers fixed in the room; then
   drive, rotate the vehicle, and use BeamNG VR recenter. Spheres must remain
   coherent without a duplicated offset.
5. Occlude/disable one controller, then stop the provider. Its sphere must vanish;
   both must vanish within 150 ms when the provider stops.
6. Close one eye at a time and verify each sphere occupies the same world point
   with correct parallax (not a desktop overlay).

## Diagnostics and troubleshooting

Logs are throttled by `logIntervalSeconds`. Provider logs device validity. BeamNG
logs update counter, receive age, mapping, validity, relative transform and final
world pose. If nothing appears, check console load errors, matching port, Windows
Firewall, SteamVR roles, and that packets remain loopback-local. If a
`drawSphere` method error occurs, return the log: the dump exposes opaque
`debugDrawer` userdata but not method signatures, and the persistent `TSStatic`
fallback must be selected after confirming the installed build's supported API.

After the first test, return `beamng.log` from the user directory, provider console
output, active OpenXR runtime/headset/controller model, final config, whether each
eye rendered the primitives, and a new OpenXR API dump covering startup, recenter,
tracking loss, and recovery. Do not publish logs without checking them for paths or
personal data.

