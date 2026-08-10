# External VR pistol attachment

## Investigation and incompatibilities

The supplied `VRPistol_Visual_v0.2_controller_api_fix.zip` was inspected only as
temporary reference material. Its case-sensitive mounted model path is
`/art/shapes/vrpistol/vr_pistol.dae`; the material definition is
`/art/shapes/vrpistol/main.materials.json`. The model material names are
`wpn_p55_pi_mike2011_griptac_v0`, `wpn_p55_pi_mike2011_mag_v0`,
`att_ammo_9p.002`, `wpn_p55_pi_mike2011_frame_v0`,
`wpn_p55_pi_mike2011_barlong_v0`, and
`wpn_p55_pi_mike2011_trigger_v0`. All model, texture, and material files remain
in the separately installed visual mod; this repository only refers to their
mounted virtual-filesystem path.

The ZIP's `lua/ge/extensions/auto/vrPistolVisual.lua` does have a BeamNG autoload
route. It queries the correct provider API and calls seven-argument
`setPosRot(x, y, z, qx, qy, qz, qw)`, but it has no grip transform, does not
distinguish invalid from stale transitions, and deletes the object when merely
disabled. More importantly, leaving that renderer active beside this integration
would create a second `TSStatic`. The attachment therefore calls its public
`setEnabled(false)` when it appears. The attachment retains the actual extension
reference, resets suppression when that reference disappears, and disables a new
instance after an extension reload. It also rechecks the instance's `isEnabled`
state without repeating transition logs. This makes the supplied v0.2 visual
package an asset provider, while this repository owns the sole attachment lifecycle.

BeamNG's mod manager mounts installed mods into one virtual filesystem. A GE Lua
extension loaded from this controller mod can therefore resolve the absolute
model path mounted by the visual mod. The attachment verifies the path with
`FS:fileExists` before object creation and fails without a graphical fallback
when the other mod is absent. Creation follows the required order: create one
`TSStatic`, assign `shapeName` and non-collision fields, then register the object.

## Loading and lifecycle

Files in `lua/ge/extensions` are not presumed to autoload. After the pose
provider has initialized successfully, it makes an isolated optional
`extensions.load('vrPistolAttachment')` call. The call is protected by `pcall`,
checks whether the attachment is already loaded, and is deliberately absent
from tracking, transport, pose calculation, and diagnostic paths. The attachment
has no dependency declaration back to the provider, so there is no dependency
cycle. The provider continues normally if loading or asset creation fails.

On each `onPreRender`, the attachment obtains
`extensions.beamngVRControllerPoses.getControllerWorldPose('right')`. It accepts
only a true `valid` flag, a finite `ageMs` no greater than 125 ms, present finite
XYZ position, and a present finite nonzero XYZW orientation. Invalid, stale,
missing-provider, and mission-end states hide the existing object immediately.
Valid tracking shows that same instance again; unload deletes it.

`gripPositionOffset` and `gripRotationOffset` are clearly named controller-local
rigid-transform corrections in `vrPistolAttachment.lua`. The position offset is
rotated by the controller quaternion and the rotation offset is quaternion-
composed before all seven values are sent to `setPosRot`. Defaults are zero
translation and identity rotation.

## Authoritative muzzle pose and calibration ray

`vrPistolAttachment.getMuzzleWorldPose()` exposes the current barrel pose. The
attachment obtains the current right-controller world pose, composes the existing
controller-local grip offsets once, and uses that same final displayed-pistol
transform as the basis for the model-local muzzle correction. It rotates the
local muzzle position into world space, adds it to the displayed pistol position,
and rotates and normalises the local barrel-forward axis. Thus camera, headset,
mouse, vehicle direction, and the last valid sample are never aiming fallbacks.

For a current pose the function returns a fresh table (including fresh nested
tables) with this contract:

```lua
{
  valid = true,
  position = {x = ..., y = ..., z = ...},
  direction = {x = ..., y = ..., z = ...}, -- normalised world direction
  orientation = {x = ..., y = ..., z = ..., w = ...},
  ageMs = ...,
  updateCounter = ...
}
```

`orientation` is the complete XYZW world quaternion after the pistol grip and
muzzle-local rotation corrections. `ageMs` and `updateCounter` are copied from
the right-controller sample. If the provider, runtime, tracking, transform, or
configuration is unavailable, invalid, or stale, the result is instead:

```lua
{
  valid = false,
  position = nil,
  direction = nil,
  orientation = nil,
  ageMs = ...,             -- retained only when supplied by the rejected sample
  updateCounter = ...
}
```

No cached transform is exposed after tracking is lost. The provisional constants
are together near the top of `vrPistolAttachment.lua`:

* `muzzleLocalPositionOffset = {x=0, y=0.32, z=0.08}` locates the barrel opening
  relative to the pistol model origin.
* `barrelLocalForwardAxis = {x=0, y=1, z=0}` identifies the barrel's model-space
  forward direction.
* `muzzleLocalRotationOffset = {x=0, y=0, z=0, w=1}` is an identity correction
  ready for a small model-axis adjustment.
* `debugRayLength = 5` is the temporary world-space calibration line length.

Set `debugRayEnabled` to `true` in that same text block to enable the calibration
line. It defaults to `false`. While a current muzzle pose exists, `onPreRender`
submits one transient line with BeamNG GE Lua's existing
`debugDrawer:drawLine(vec3(startPoint), vec3(endPoint), ColorF(...))` facility.
The repository's captured BeamNG API inspection confirms the `debugDrawer`
userdata/`DebugDrawer` binding, and the controller extension already exercises
the same world-debug-drawing facility for transient spheres. The line creates no
scene object, collision, raycast, firing action, or damage and stops being
submitted immediately when the pose is invalid.

The exact offset, model-space axis, and any small rotational correction remain
provisional until BeamNG/headset calibration. Test pitch, yaw, and roll against
the visible barrel; then adjust only the three muzzle constants, not the verified
grip offsets. This stage deliberately has no shooting or JBeam damage behavior.
The future damage stage will initially preserve the original Bolides
`BulletDamage.lua` strength and behavior; the Bolides repository was consulted
read-only and no part of its damage implementation is integrated here.

## Manual validation still required

Source tests cannot establish runtime archive mounting, debug-line stereoscopic
visibility, or visual correctness.
With both mods installed, BeamNG and headset testing must still confirm model
scale, grip position, grip orientation, all six material mappings and textures,
and stereoscopic rendering. Logs should also be checked through loss/recovery of
right-controller tracking, mission exit/re-entry, and extension reload. There is
no primitive, procedural, cube, collision, firing, ammunition, damage, physics,
or JBeam fallback.
