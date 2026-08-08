# PR #38 artificial-motion source and headset evidence

## PR #37 headset evidence

The headset run established all seven observations that govern this rework:

1. After recenter, dark blue coincides with orange.
2. Dark blue has a world-facing-dependent error during natural physical head translation.
3. Dark blue handles natural head rotation correctly.
4. Dark blue moves with stick-controlled locomotion.
5. Dark blue does not follow stick-controlled rotation.
6. During combined movement, it follows stick locomotion but not physical translation correctly.
7. Recenter returns dark blue exactly to orange.

Consequently, the former subtraction of an orange physical delta from a final
BeamNG camera delta is not valid: those values are not guaranteed to share a
camera stage or frame.

## Source inspection and decision

The repository Lua, API dump, camera-mode inventory, OpenXR capture, and existing
moving-anchor experiments were inspected. The API dump exposes locomotion and
rotation entry points and the `core/cameraModes/unicycle` mode, but it does not
expose a documented transform guaranteed to exclude OpenXR prediction. In
particular, `core_camera.getPosition()` and its quaternion are final/general
camera observations and are **not** assumed to be pre-VR.

The selected authoritative source is the position and quaternion passed to
`OpenXR.setGeluaCameraPosRot`. BeamNG supplies this anchor before it requests and
applies the predicted OpenXR camera pose. The existing transparent capture
wrapper records those arguments without changing them. Its complete rigid delta
therefore represents observed game-anchor translation and yaw; input values are
not integrated. A yaw delta already contains the translation needed to preserve
the camera pivot, so no second pivot correction is applied.

If capture is absent, stale, malformed, or unavailable, dark blue fails closed
to orange and the failure reason remains available in diagnostics. Raw
`core_camera` and captured predicted-pose observations remain diagnostic only;
they are never combined speculatively.
