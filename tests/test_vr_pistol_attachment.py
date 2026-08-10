import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
ATTACHMENT = ROOT / "mod/lua/ge/extensions/vrPistolAttachment.lua"
PROVIDER = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"
SETTINGS = ROOT / "mod/settings/beamngVRControllerPoses.json"


def source() -> str:
    return ATTACHMENT.read_text(encoding="utf-8")


def test_uses_right_controller_public_pose_api_and_expected_external_model():
    text = source()
    assert "pcall(poseProvider.getControllerWorldPose,'right')" in text
    assert "'/art/shapes/vrpistol/vr_pistol.dae'" in text


def test_head_camera_aids_default_off_without_disabling_controller_visuals_or_pistol_pose():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    provider = PROVIDER.read_text(encoding="utf-8")
    assert settings["cameraTestSphere"]["enabled"] is False
    assert "if legacyControllerSpheresVisible then debugDrawer:drawSphere" in provider
    assert "function M.getControllerWorldPose(hand)" in provider


def test_validates_complete_fresh_pose():
    text = source()
    assert "pose.valid~=true" in text
    assert "pose.ageMs>maximumPoseAgeMs" in text
    assert "pose.ageMs<0" in text
    for field in ("p.x", "p.y", "p.z", "q.x", "q.y", "q.z", "q.w"):
        assert f"finite({field})" in text
    assert "q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w==0" in text


def test_complete_xyzw_transform_and_named_grip_offsets_are_used():
    text = source()
    assert "local gripPositionOffset" in text
    assert "local gripRotationOffset" in text
    assert "composePistolAndMuzzleTransform(pose.position,pose.orientation)" in text
    assert "orientation.x,orientation.y,orientation.z,orientation.w" in text


def test_single_instance_hiding_recovery_and_cleanup_contract():
    text = source()
    assert text.count("createObject('TSStatic')") == 1
    assert "if pistol then return true end" in text
    assert "setHidden(true); transitionTracking(state)" in text
    assert "right-controller tracking recovered" in text
    assert "function M.onExtensionUnloaded() cleanup('extension unloaded') end" in text
    assert "function M.onProviderUnloaded()" in text
    assert "pistol:delete()" in text


def test_no_primitive_or_fallback_pistol_construction():
    text = source().lower()
    assert "createobject('tsstatic')" in text
    for forbidden in ("cube.dae", "primitive", "fallback pistol", "vrmockpistol"):
        assert forbidden not in text


def test_provider_lazy_loads_attachment_only_after_a_fresh_complete_right_pose():
    provider = PROVIDER.read_text(encoding="utf-8")
    loaded = provider.split("function M.onExtensionLoaded()", 1)[1].split(
        "function M.startNativeSourcePoseDiagnostics()", 1
    )[0]
    assert "extensions.load" not in loaded
    assert "freshCompleteControllerPose(pose)" in provider
    assert "pistolAttachmentLoadAttempted=true" in provider
    assert "pcall(extensions.load,'vrPistolAttachment')" in provider
    assert "not validSeven(raw.p[1],raw.p[2],raw.p[3]" in provider
    assert provider.index("updateHand('left'") < provider.index("loadPistolAttachmentAfterTracking()", provider.index("function M.onPreRender"))
    assert "dependencies" not in source()


def test_transform_failure_is_logged_once_until_an_update_succeeds():
    text = source()
    assert "if not transformFailureActive then" in text
    assert "transformFailureActive=true" in text
    assert "transformFailureActive=false" in text
    assert "scenetree.findObject(objectName)" in text


def test_external_renderer_is_disabled_to_avoid_duplicate_object():
    text = source()
    assert "extensions.vrPistolVisual" in text
    assert "pcall(current.setEnabled,false)" in text


def test_external_renderer_disappearance_and_reload_reset_suppression_state():
    text = source()
    suppression = text.split("local function disableBundledRenderer()", 1)[1].split(
        "\nend\n\nlocal function cleanup", 1
    )[0]
    assert "if not current then" in suppression
    assert "externalRendererReference=nil" in suppression
    assert "externalRendererDisabled=false" in suppression
    assert "if current~=externalRendererReference then" in suppression
    assert "externalRendererReference=current" in suppression
    assert "pcall(current.isEnabled)" in suppression
    assert "pcall(current.setEnabled,false)" in suppression


def _multiply(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _rotate(q, v):
    return _multiply(_multiply(q, (*v, 0)), (-q[0], -q[1], -q[2], q[3]))[:3]


def _axis_angle(axis, degrees):
    radians = math.radians(degrees) / 2
    length = math.sqrt(sum(component * component for component in axis))
    sine = math.sin(radians)
    return tuple(component / length * sine for component in axis) + (math.cos(radians),)


def _normalise(q):
    length = math.sqrt(sum(component * component for component in q))
    return tuple(component / length for component in q)


def test_muzzle_api_uses_shared_authoritative_pistol_transform_and_fresh_results():
    text = source()
    assert "function M.getMuzzleWorldPose()" in text
    assert "composePistolAndMuzzleTransform(pose.position,pose.orientation)" in text
    assert "transform.pistolPosition,transform.pistolOrientation" in text
    assert "position={x=pose.position.x,y=pose.position.y,z=pose.position.z}" in text
    assert "orientation={x=pose.orientation.x,y=pose.orientation.y,z=pose.orientation.z,w=pose.orientation.w}" in text


def test_numeric_xyzw_order_rotates_offset_and_axis_for_pitch_yaw_and_roll():
    # Non-commuting pitch/yaw/roll composition detects swapped multiplication order.
    q = _multiply(_multiply(_axis_angle((0, 0, 1), 37), _axis_angle((1, 0, 0), 23)), _axis_angle((0, 1, 0), -41))
    offset = _rotate(q, (0, 0.32, 0.08))
    direction = _rotate(q, (0, 1, 0))
    assert offset == pytest.approx((-0.204990, 0.184821, 0.180611), abs=1e-6)
    assert direction == pytest.approx((-0.553974, 0.735148, 0.390731), abs=1e-6)
    assert math.sqrt(sum(component * component for component in direction)) == pytest.approx(1)


def test_muzzle_rotation_correction_changes_direction_without_moving_opening():
    pistol_orientation = _multiply(
        _axis_angle((0, 0, 1), 31), _axis_angle((1, 0, 0), -19)
    )
    muzzle_correction = _axis_angle((0, 0, 1), 17)
    muzzle_orientation = _multiply(pistol_orientation, muzzle_correction)
    local_position = (0, 0.32, 0.08)
    local_forward = (0, 1, 0)

    position_without_correction = _rotate(pistol_orientation, local_position)
    position_with_correction = _rotate(pistol_orientation, local_position)
    incorrectly_rotated_position = _rotate(muzzle_orientation, local_position)
    direction_without_correction = _rotate(pistol_orientation, local_forward)
    direction_with_correction = _rotate(muzzle_orientation, local_forward)

    assert position_with_correction == pytest.approx(position_without_correction)
    assert incorrectly_rotated_position != pytest.approx(position_without_correction)
    assert direction_with_correction != pytest.approx(direction_without_correction)
    text = source()
    assert "worldOffset=rotateVector(pistolOrientation,muzzleLocalPositionOffset)" in text
    assert "direction=normaliseVector(rotateVector(muzzleOrientation,localDirection))" in text


def test_non_unit_controller_quaternion_cannot_scale_grip_offset():
    unit_orientation = _axis_angle((0, 1, 0), 63)
    scaled_orientation = tuple(component * 4.5 for component in unit_orientation)
    grip_offset = (0.12, -0.07, 0.03)
    assert _rotate(_normalise(scaled_orientation), grip_offset) == pytest.approx(
        _rotate(unit_orientation, grip_offset)
    )
    text = source()
    assert "controllerOrientation=normaliseQuaternion(orientation)" in text
    assert "rotateVector(controllerOrientation,gripPositionOffset)" in text


def test_invalid_stale_and_bad_configuration_never_expose_transform():
    text = source()
    assert "invalidateMuzzlePose(rawPose and rawPose.ageMs or nil" in text
    assert "if not runtimeActive then invalidateMuzzlePose" in text
    assert "if not poseProvider then invalidateMuzzlePose" in text
    assert "lengthSquared<=1e-12" in text
    assert "not finite(debugRayLength) or debugRayLength<=0" in text
    invalid_return = text.split("function M.getMuzzleWorldPose()", 1)[1]
    assert "return {valid=false,position=nil,direction=nil,orientation=nil" in invalid_return


def test_debug_ray_is_transient_valid_only_and_disabled_by_default():
    text = source()
    assert "local debugRayEnabled = false" in text
    assert "if debugRayEnabled then" in text
    assert "debugDrawer:drawLine(vec3(startPoint),vec3(endPoint),ColorF" in text
    assert text.index("publishMuzzlePose(transform,pose)") < text.index("if debugRayEnabled then")
    assert text.count("createObject('TSStatic')") == 1


def test_no_firing_damage_fallback_aim_or_persistent_debug_primitive():
    text = source().lower()
    for forbidden in ("castray", "bulletdamage", "applydamage", "breakbeam", "core_camera", "getcamera", "mouse", "target snapping"):
        assert forbidden not in text
    assert "createobject('tsstatic')" in text
    assert "drawsphere" not in text
