# BeamNG VR Controller Poses (v3, BeamNG.drive 0.40+)

World-space VR controller poses in BeamNG.drive, plus an animated revolver on the
right controller that shoots traffic.

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

So `controllerWorld = headWorld × controllerRelativeToHead`. One catch: gameengine.lua
adds the tracked head offset to the anchor without turning it by the anchor's yaw,
so `getCameraPosition()` only follows your real head while the anchor faces north.
`vrHands` records the anchor rotation and offset from those two OpenXR calls and
can use `anchor + anchorRot × offset` instead (`vrHands.getConfig().fixHeadOffset = true`).
That correction is still experimental and off by default. No tracking-space
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
3. Start BeamNG and turn on VR. The revolver and laser appear on your right
   controller. Pull the trigger to shoot.

`Uninstall.cmd` undoes both steps. To skip the layer for one launch, set the
environment variable `BEAMNG_VR_POSES_DISABLE=1`.

The revolver model ships inside the mod (`mod/art/shapes/vrrevolver`). The older
`VRPistol_Visual_v0.2_controller_api_fix.zip` is no longer used.

Releases are automatic. Every merge to `main` builds, tests and publishes
`v3.0.<run>` with the zip attached. A manual run of the **Windows x64 layer**
workflow (Actions tab, any branch) publishes a `-beta` pre-release. Pushing a
`v*` tag releases that tag.

## Use

Everything loads automatically. For diagnostics, open the GE Lua console (`~`):

```lua
vrHands.setDebug(true)             -- yellow grip spheres + RGB axes, aim rays
dump(vrHands.getStatus())          -- packets, age, which poses are valid
vrHands.startCapture(20)           -- record 20 s of camera + pose data to <user folder>/vrHandsCapture/
```

Pull the right trigger to shoot. The revolver is double action: as you squeeze,
the hammer comes back and the cylinder turns, and at the break the hammer drops,
the gun goes bang (the Bolides player gunshot), the muzzle flashes and it
kicks up and back in your hand. A red laser from
the muzzle shows the aim ray. Orange spheres
mark vehicle hits and grey spheres mark world hits. A hit uses the same
impact model as Bolides' `BulletDamage.lua`: a mass-scaled push on the struck node
along the bullet's path, plus a 1.3 m falloff blast that dents the panel around
it. A hit near a wheel hub also deflates that tyre. Your own
vehicle is never hit.

Tuning (console):

```lua
vrPistol.setModelOffset(0, -0.02, 0.05, 0)   -- gun origin (bore, rear of cylinder) in aim space: x right, y along barrel, z up (m), pitch (deg)
vrPistol.getConfig().recoilPitchDeg = 22     -- also recoilBack, hammerCockDeg, triggerTravelDeg, flashTime
vrPistol.getConfig().shotVolume = 12         -- shotSound = '' mutes it
vrPistol.setLaser(false)
vrPistol.getConfig().impactForce = 9000      -- also blastRadius, blastForce, deflateTyres, range, cooldown
vrPistol.fireFromCamera()                    -- desktop test along the camera view
```

### Revolver model

A 1960s-style large-frame .44 with a 6.5" barrel: blued steel, colour
case-hardened hammer and trigger, checkered wood target grips, red ramp front
sight, six rounds visible in the cylinder. It was modelled for this mod, so there
are no third-party asset rights to worry about.

`tools/revolverModel/build_revolver.py` builds it with Blender's Python module
(`pip install bpy`, then run the script). It writes the shapes, textures and
`main.materials.json` into `mod/art/shapes/vrrevolver`. The frame, cylinder,
hammer, trigger and muzzle flash are separate shapes, each with its origin on its
own pivot. `vrPistol.lua` places all five every frame.

### Lua API

```lua
local aim = vrHands.getPose('right', 'aim')   -- or 'grip'; 'left' works too
-- aim.valid, aim.pos, aim.dir (pointing), aim.up, aim.right  (world vec3)
-- aim.rot  (quatFromDir(dir, up), for TSStatic:setPosRot), aim.ageMs
vrHands.getTrigger('right')                    -- 0..1
vrHands.getSqueeze('right')                    -- 0..1
vrHands.pulse('right', 0.8, 0.05)              -- vibrate (amplitude 0..1, seconds)
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

Haptics go the other way: `vrHands.pulse` sends `pulse <left|right> <amplitude> <seconds>`
to the port + 1 (44442). BeamNG binds no vibration outputs, so the layer adds
its own action set with one vibration action. It merges those bindings into
BeamNG's suggested bindings, attaches the set, and keeps it active in
`xrSyncActions`. Any call the runtime rejects falls back to BeamNG's original,
so BeamNG's own input never depends on the haptics working. The pistol pulses
the right controller on every shot (`vrPistol.getConfig().hapticAmplitude`,
0 disables).

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
