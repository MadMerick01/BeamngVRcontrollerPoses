# BeamNG VR Controller Poses (v3, BeamNG.drive 0.40+)

World-space VR controller poses in BeamNG.drive, plus an animated handgun (a revolver or a big .50 semi-auto) on the
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
uses `anchor + anchorRot × offset` instead (`vrHands.getConfig().fixHeadOffset`).
BeamNG's camera quaternions also need conjugating before use (`cameraQuat = 'conjugate'`).
Captures from Rowan's headset, on foot and in the car, confirm both: a controller
resting on a desk stays within 1 cm while the head turns, tilts and leans
(`tests/data`).

Since the layer also sends each controller's pose in BeamNG's tracking space,
`vrHands` now places it the way BeamNG places its own controller models:
`anchor + anchorRot × pose`, with no head pose involved (`useTrackingSpace`). It
runs after `core_camera` so the anchor is this frame's. A frame late, the gun
trailed the car by one frame of travel and jittered at speed.
`vrHands.getStatus().cameraLagMs` should read close to 0. No tracking-space
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
3. Start BeamNG and turn on VR. Hold the right grip to draw the pistol and
   pull the trigger to shoot; let go of the grip to put it away.

`Uninstall.cmd` undoes both steps. To skip the layer for one launch, set the
environment variable `BEAMNG_VR_POSES_DISABLE=1`.

Both gun models ship inside the mod (`mod/art/shapes/vrmagnum` and `vrrevolver`). The older
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

By default the pistol mod leaves BeamNG's own controller pointers (the black
line and orange ball) alone. The only way to hide them is BeamNG's "use
controllers" setting (`openXRuseControllers`), and switching that off also
turns off the controllers' sticks and buttons in the game, so you can't walk or
drive with the sticks. If you want them hidden anyway,
`vrPistol.getConfig().hideGamePointer = true` hides them (and the stick input)
the whole time the mod is loaded, and `'drawn'` only while the gun is drawn.
Unloading the mod puts the setting back.

Hold the right grip to draw the gun. Drawing it plays a holster sound. Pull the right trigger to shoot. The revolver is double action: as you squeeze,
the hammer comes back and the cylinder turns, and at the break the hammer drops,
the gun goes bang (the Bolides player gunshot) and it
kicks up and back in your hand. An optional red laser from
the muzzle (`vrPistol.setLaser(true)`) shows the aim ray. With
`vrPistol.getConfig().hitMarkers = true`, orange spheres
mark vehicle hits and grey spheres mark world hits. A hit uses the same
impact model as Bolides' `BulletDamage.lua`: a mass-scaled push on the struck node
along the bullet's path, plus a 1.3 m falloff blast that dents the panel around
it. A hit near a wheel hub also deflates that tyre. Your own
vehicle is never hit.

One shot at an AI driver stops their car. Each vehicle has a driver box, a
50 cm cube hung 15 cm below its driver camera (the in-car view from the
vehicle's `camerasInternal` data) and turned with the body, which covers the
head, neck and upper chest. If a shot's path goes through that box, even
when it would stop at the door or the glass first, the vehicle's AI is
switched off, the throttle is released and the brake is held at 50%, so the car slows to a stop. It
only affects AI-driven vehicles. To see where the boxes sit, run
`vrPistol.showDriverBoxes(30)` in the console: for 30 s every vehicle's box is
drawn, red while its AI is driving and grey once it is off. A vehicle with no
driver camera gets no box and is named in the log. `driverBoxSize`,
`driverBoxDrop` and `driverBrake` in `vrPistol.getConfig()` change the size,
the drop and the brake (`driverBrake = 0` just coasts, `driverBoxSize = 0`
turns driver shots off).

### Choosing the gun

There are two guns: the magnum (used by default) and the revolver. Pick one in the console; the choice is saved in
`<user folder>/settings/vrPistol.json` and used every time after:

```lua
vrPistol.setGun('magnum')     -- big .50 semi-auto, the default ('deagle' works too)
vrPistol.setGun('revolver')   -- the .44 revolver
```

The magnum is single action. It is carried cocked, so the trigger pull is short
and light. At the break the hammer drops, the slide blows back and slams home,
re-cocking the hammer, and the gun kicks about 20% harder than the revolver. It
holds seven rounds. After the last one the slide locks open, and the trigger
does nothing until you reload the same way as the revolver (put it away for
1.5 s and draw it again), which closes the slide. `slideTravel` and
`slideCycle` in `vrPistol.getConfig()` change how far and how fast the slide
moves. Shots, sounds, hits, driver shots, ammo and hooks work the same for both
guns.

The cylinder holds six rounds. When it is empty the trigger still turns the
cylinder and drops the hammer, but nothing fires. To reload, put the gun away
(let go of the grip) for 1.5 s and draw it again. Spare rounds are unlimited
unless another mod sets them (`vrPistol.getConfig().cylinderSize = 0` turns
ammo off, `reloadSeconds` sets the reload time).

### For other mods

The pistol works on its own. Another mod (such as a game built on it) can
manage its ammo and listen to what it does, without changing its code:

```lua
vrPistol.getAmmo()                -- rounds in the cylinder, cylinder size, spare rounds (nil = unlimited)
vrPistol.setAmmo(rounds, spare)   -- e.g. setAmmo(6, 18); leave spare out for unlimited
vrPistol.reload()                 -- refill now from the spares, returns rounds loaded
vrPistol.setShotFilter(fn)        -- fn() returning false refuses a shot (the gun just clicks)
```

It raises these hooks with `extensions.hook`, so any loaded extension that
defines a function of the same name hears them:

| Hook | When | Arguments |
|---|---|---|
| `onVrPistolShot` | a round is fired | origin, direction (`vec3`), rounds left |
| `onVrPistolDryFire` | the trigger breaks but nothing fires | `'empty'` or `'refused'` |
| `onVrPistolReload` | the cylinder is refilled | rounds, spare rounds |
| `onVrPistolHit` | a shot hits a vehicle | vehicle id, hit position (`vec3`), nodes pushed |
| `onVrPistolDriverHit` | a shot hits an AI driver and turns the AI off | vehicle id, where the shot entered the driver box (`vec3`) |

A listener that throws an error is logged and never stops the gun.

Tuning (console):

```lua
vrPistol.setModelOffset(0, -0.02, 0.05, 0)   -- gun origin (bore, rear of cylinder) in aim space: x right, y along barrel, z up (m), pitch (deg)
vrPistol.getConfig().recoilPitchDeg = 22     -- also recoilBack, hammerCockDeg, triggerTravelDeg
vrPistol.getConfig().shotVolume = 18         -- shotSound = '' mutes it
vrPistol.testSound()                         -- plays the gunshot once (check the console for "created SFX source")
vrPistol.testSound('equip')                  -- plays the equip sound (equipSound, equipVolume)
vrPistol.setLaser(true)                      -- aiming laser (off by default)
vrPistol.getConfig().equipOnGrip = false     -- gun always drawn; hideGamePointer = true or 'drawn' hides BeamNG's pointers (and stick input)
vrPistol.getConfig().impactForce = 9000      -- also blastRadius, blastForce, deflateTyres, range, cooldown
vrPistol.fireFromCamera()                    -- desktop test along the camera view
vrPistol.startModelTest()                     -- gun smoothness test, see below
```

By default the gun is a low-poly copy of the revolver (about 1,400 triangles,
one colour per material) drawn with the debug drawer, painted fresh every frame
like the laser, so it stays locked to your hand at any speed
(`vrPistol.getConfig().modelStyle = 'simple'`). `tools/revolverModel/build_lowpoly.py`
builds it into `mod/lua/ge/extensions/vrPistolMesh.lua` from the full model. Set
`modelStyle = 'textured'` for the full textured revolver, which looks better but
lags at speed: the laser is drawn straight to the screen each frame, but the
textured revolver is a scene object. Lua moves the engine's server copy, and the copy that gets drawn follows
over the engine's internal network, which can lag. `vrPistol.startModelTest()`
spends 8 s on each of five ways of moving the model while you drive (A as now,
B also moving the drawn copy, C/D leading the car by 1/2 frames, E both). It
announces each letter and records every frame to
`<user folder>/vrHandsCapture/model_*.jsonl`. The options are also in
`getConfig()` as `placeMode` ('server'/'client') and `predictSeconds`.

### Magnum model

A big gas-operated .50 semi-automatic in the classic "hand cannon" style, built from
a reference photo: a 6" barrel with a slotted sight rail and recessed flats, a
short slide with slanted rear serrations and ambidextrous safety levers, a ring
hammer, a squared trigger guard, pebbled black grips and an extended magazine
floor plate, all in matte black. It has no maker's markings or logos, and it
was modelled for this mod, so there are no third-party asset rights to worry
about.

`tools/magnumModel/build_magnum.py` builds it the same way as the revolver and
reuses the revolver script's helpers. It writes `mod/art/shapes/vrmagnum`
(frame, slide, hammer and trigger). The grip sits where the revolver's grip
sits, so one `setModelOffset` fits both guns.
`python tools/revolverModel/build_lowpoly.py magnum` builds its low-poly copy
(`vrPistolMeshMagnum.lua`).

### Revolver model

A 1960s-style large-frame .44 with a 6.5" barrel: blued steel, colour
case-hardened hammer and trigger, checkered wood target grips, red ramp front
sight, six rounds visible in the cylinder. It was modelled for this mod, so there
are no third-party asset rights to worry about.

`tools/revolverModel/build_revolver.py` builds it with Blender's Python module
(`pip install bpy`, then run the script). It writes the shapes, textures and
`main.materials.json` into `mod/art/shapes/vrrevolver`. The frame, cylinder,
hammer and trigger are separate shapes, each with its origin on its
own pivot. `vrPistol.lua` places all four every frame.

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
