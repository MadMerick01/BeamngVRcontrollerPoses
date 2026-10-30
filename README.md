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

1. Download `BeamNGVRcontrollerPoses-windows-x64.zip` from the
   [latest release](https://github.com/MadMerick01/BeamngVRcontrollerPoses/releases)
   and extract it anywhere.
2. Double-click **`Install.cmd`** and accept the admin prompt. It:
   - copies the layer to `%ProgramData%\BeamNGVRControllerPoses` and registers it as
     an implicit OpenXR layer, so it loads on its own. The layer only activates when
     the app is BeamNG.drive and passes every other VR app straight through;
   - puts `vrControllerPoses.zip` in `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\mods`.
     The mod loads itself (`scripts/vrControllerPoses/modScript.lua`).
3. Start BeamNG and turn on VR. The pistol and laser appear on your right
   controller. Pull the trigger to shoot.

`Uninstall.cmd` undoes both steps. To skip the layer for one launch, set the
environment variable `BEAMNG_VR_POSES_DISABLE=1`.

Optional: install `VRPistol_Visual_v0.2_controller_api_fix.zip` from this repo as
a mod for the pistol model. Its own renderer waits for the v2 API and stays idle,
so v3 creates the only pistol. Shooting works without the model.

Releases are automatic. Every merge to `main` builds, tests and publishes
`v3.0.<run>` with the zip attached. A manual run of the **Windows x64 layer**
workflow (Actions tab, any branch) publishes a `-beta` pre-release. Pushing a
`v*` tag releases that tag.

## Use

Everything loads automatically. For diagnostics, open the GE Lua console (`~`):

```lua
vrHands.setDebug(true)             -- yellow grip spheres + RGB axes, aim rays
dump(vrHands.getStatus())          -- packets, age, which poses are valid
```

Pull the right trigger to shoot. A red laser shows the aim ray. Orange spheres
mark vehicle hits and grey spheres mark world hits. A hit uses the same
impact model as Bolides' `BulletDamage.lua`: a mass-scaled push on the struck node
along the bullet's path, plus a 1.3 m falloff blast that dents the panel around
it. A hit near a wheel hub also deflates that tyre. Your own
vehicle is never hit.

Tuning (console):

```lua
vrPistol.setModelOffset(0, -0.04, -0.03, 0)  -- x right, y along barrel, z up (m), pitch (deg)
vrPistol.setLaser(false)
vrPistol.getConfig().impactForce = 9000      -- also blastRadius, blastForce, deflateTyres, range, cooldown
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
