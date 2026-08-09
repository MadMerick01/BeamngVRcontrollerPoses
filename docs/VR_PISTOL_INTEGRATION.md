# External VR pistol integration review

## Review result

`BeamngVRcontrollerPoses` now contains no pistol renderer. The old
`vrMockPistol.lua` extension, its cube settings, and its source-contract test
were removed, so failure to load an external model cannot fall back to the old
two-`TSStatic` cube pistol. Controller tracking, diagnostics, pose calculations,
and the public API remain unchanged.

The separate `VRPistol_Visual_v0.2_controller_api_fix` files were not present in
this repository or elsewhere in the supplied workspace, so its model path,
materials, transform code, lifecycle, and logging could not be inspected or
corrected here. In particular, a Lua file merely placed in
`lua/ge/extensions/vrPistolVisual.lua` is **available** to BeamNG's extension
manager but this project provides no evidence that it is automatically loaded.
The separate mod must include a BeamNG-supported autoload entry point (or the
user must explicitly call `extensions.load('vrPistolVisual')`). Do not treat the
extension's `dependencies` table as proof that the consumer itself autoloads;
dependencies only order/load dependencies after the consumer is requested.

## Public pose contract

The supported call is:

```lua
local provider = extensions and extensions.beamngVRControllerPoses
local pose = provider and provider.getControllerWorldPose and
  provider.getControllerWorldPose('right')
```

For `left` or `right`, the accessor always returns a table. A valid result is:

```lua
{
  valid = true,
  ageMs = number,
  position = {x = number, y = number, z = number},
  orientation = {x = number, y = number, z = number, w = number},
  updateCounter = number
}
```

An invalid result has `valid = false`, nil `position` and `orientation`, and may
still report `ageMs` and `updateCounter`. An unsupported hand returns nil. The
orientation is a BeamNG-world XYZW quaternion; it is not Euler angles and its
component order must not be changed.

## Required external-mod checks

Before the external mod is considered ready, verify all of the following against
its actual ZIP contents:

1. `vrPistolVisual.lua` declares
   `dependencies = {'beamngVRControllerPoses'}` and has an autoload entry point.
2. `shapeName` is the mounted, root-relative path to the `.dae` using `/`
   separators. It must match the archive's case exactly. Log that resolved value
   before calling `registerObject`.
3. Create exactly one `TSStatic`. If creation, shape assignment, or registration
   fails, log an error, delete any partial object, and retry only the imported
   `.dae`; never create primitive fallback objects.
4. On every render/update, accept the pose only when `pose.valid == true`, all
   seven position/quaternion components are finite, and `ageMs` is finite and no
   greater than the consumer's stale threshold.
5. Apply the complete transform with
   `object:setPosRot(p.x, p.y, p.z, q.x, q.y, q.z, q.w)`. If the model needs an
   authored grip offset, compose that rigid transform with the controller pose;
   do not add Euler rotations to quaternion components.
6. Hide the object immediately for a missing provider, invalid pose, stale pose,
   transform failure, or model creation failure. Delete it on extension unload.
7. Rate-limit transition logging rather than logging every frame. Log extension
   load, provider found/missing, first valid right-hand pose (and recovery), the
   resolved model path, model creation success/failure, and each transition to a
   hidden invalid/stale state.

Because model and material loading is performed by BeamNG, a final in-engine
test is still required to confirm archive mounting, material-name matching,
stereoscopic rendering, scale, grip offset, and orientation.
