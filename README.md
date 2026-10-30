# BeamNG VR Controller Poses (v3, BeamNG.drive 0.40+)

World-space VR controller poses in BeamNG.drive, plus a handgun on the right
controller that shoots traffic.

```text
BeamNG ── OpenXR loader ── BeamNGVRPosesLayer.dll ── runtime (VDXR, SteamVR, Oculus…)
                                 │  grip + aim pose of each controller, relative to the
                                 │  headset, at BeamNG's own frame time; trigger/squeeze
                                 ▼  loopback UDP 127.0.0.1:44441
                       vrHands.lua  ×  getCameraPosition()/getCameraQuat()
                                 ▼
                        vrPistol.lua  →  vrPistolHit.lua (vehicle side)
```

## Why v3 is simpler than v2

A dump from BeamNG 0.40.4 (`tools/vrPoseDump`) shows two things:

1. The `OpenXR` Lua table still has **no controller pose functions**. The
   `getSourcePoseStates` helper in `render/openxr.lua` is commented out as a TODO,
   so the API layer is still needed.
2. `core/cameraModes/gameengine.lua` composes the final camera as
   `anchor + OpenXR predicted head pose` and hands it to the engine. In 0.40.4,
   `getCameraPosition()` and `getCameraQuat()` return that **exact rendered head pose**
   (matched to 1e-13 over 1,847 frames of live capture).

So `controllerWorld = headWorld × controllerRelativeToHead`. No tracking-space
baselines, recenter handling, stick-turn rebasing or camera-context tracking are
needed. v2's ~1,450 lines of camera reconstruction are gone.

The layer also fixes a v2 accuracy bug. BeamNG locates **both** the grip and aim
spaces of each controller every frame, and v2 published whichever one it saw
last. v3 tags every space by action name (`*_grip_pose` / `*_aim_pose`) and
publishes both separately. It also ignores spaces from inactive interaction
profiles, and supports the OpenXR 1.1 batch `xrLocateSpaces` call.

## Install

1. Download `BeamNGVRcontrollerPoses-windows-x64.zip` from the latest successful
   **Windows x64 layer** workflow run and extract it to a clean folder, for example
   `C:\BeamNGVRcontrollerPoses`.
2. Zip the `mod` folder's **contents** (`lua/...`) as `vrControllerPoses.zip` and
   put that zip in `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\mods\`.
   Remove any older build of this mod.
3. Optional: install `VRPistol_Visual_v0.2_controller_api_fix.zip` from this repo
   as a mod for the pistol model. Its own renderer waits for the v2 API and stays
   idle, so v3 creates the only pistol. Shooting works without the model.
4. Launch BeamNG through the layer (process-scoped, no registry changes):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
C:\BeamNGVRcontrollerPoses\scripts\Enable-BeamNGVRPoses.ps1 `
  -PackageDirectory C:\BeamNGVRcontrollerPoses `
  -BeamNGExecutable 'C:\Program Files (x86)\Steam\steamapps\common\BeamNG.drive\BeamNG.drive.exe'
```

## Use

In the GE Lua console (`~`), after VR is on:

```lua
extensions.load('vrPistol')        -- loads vrHands too
vrHands.setDebug(true)             -- yellow grip spheres + RGB axes, aim rays
dump(vrHands.getStatus())          -- packets, age, which poses are valid
```

Pull the right trigger to shoot. A red laser shows the aim ray. Orange spheres
mark vehicle hits and grey spheres mark world hits. A hit breaks a few beams
around the struck node, and a hit near a wheel hub deflates that tyre. Your own
vehicle is never hit.

Tuning (console):

```lua
vrPistol.setModelOffset(0, -0.04, -0.03, 0)  -- x right, y along barrel, z up (m), pitch (deg)
vrPistol.setLaser(false)
vrPistol.getConfig().maxBeamsPerShot = 8     -- also hitRadius, damageRadius, range, cooldown
vrPistol.fireFromCamera()                    -- desktop test along the camera view
```

### Lua API

```lua
local aim = vrHands.getPose('right', 'aim')   -- or 'grip'; 'left' works too
-- aim.valid, aim.pos, aim.dir (pointing), aim.up, aim.right  (world vec3)
-- aim.rot  (quatFromDir(dir, up), for TSStatic:setPosRot), aim.ageMs
vrHands.getTrigger('right')                    -- 0..1
vrHands.getSqueeze('right')                    -- 0..1
```

Read poses in `onPreRender`, after the camera update. A pose is invalid when
tracking is lost or no packet has arrived for 150 ms.

## Layer packet (protocol v3)

```json
{"v":3,"seq":12,"t":<XrTime>,
 "hands":{"right":{"grip":{"ok":true,"t":<XrTime>,"p":[x,y,z],"q":[x,y,z,w]},
                   "aim":{...},"trigger":0.0,"squeeze":0.0},
          "left":{...}}}
```

`p`/`q` are in OpenXR VIEW space (+X right, +Y up, -Z forward), so
`inverse(head) × controller` is evaluated at the controller's own XrTime. Set
`BEAMNG_VR_POSES_PORT` to change the port.

## Tests

`python -m pytest` runs the Lua modules under LuaJIT (via `lupa`) with mocked
BeamNG globals. It covers pose composition for both camera-quaternion
conventions, staleness, trigger edges, target selection and vehicle-side damage.
CI also builds the x64 layer DLL and checks its export and manifest.

## Diagnostic dump

`tools/vrPoseDump` is the mod that produced the 0.40.4 findings above. Load it,
then run `extensions.vrPoseDump.start()` in VR. It writes the OpenXR API surface,
the relevant game Lua sources and per-frame camera/OpenXR samples to
`<user folder>/vrPoseDump/`.
