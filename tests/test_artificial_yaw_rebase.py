"""PR #28 pivot-preserving tracking-alignment rebase tests."""
from math import cos, pi, sin
from pathlib import Path

import pytest

from beamng_vr_poses.math3d import (
    Pose, baseline_rigid_controller_world, compose,
    pivot_preserving_world_from_tracking_rebase,
    quaternion_angular_difference_degrees, qmul, qnorm, qrotate,
    target_world_from_tracking_orientation,
)

I = (0., 0., 0., 1.)
ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def yaw(degrees):
    radians = degrees * pi / 360
    return (0., 0., sin(radians), cos(radians))


def close(a, b, tolerance=1e-9):
    assert a == pytest.approx(b, abs=tolerance)


def maybe_rebase(stored, hmd, camera_q, threshold=.75):
    target = target_world_from_tracking_orientation(camera_q, hmd.orientation)
    delta = quaternion_angular_difference_degrees(stored.orientation, target)
    return ((*pivot_preserving_world_from_tracking_rebase(stored, hmd, target), True)
            if delta > threshold else (stored, compose(stored, hmd), compose(stored, hmd), False))


def test_natural_matching_hmd_and_camera_yaw_does_not_rebase():
    stored = Pose((10., 20., 2.), I)
    _, _, _, triggered = maybe_rebase(stored, Pose((0., 0., 0.), yaw(90)), yaw(90))
    assert not triggered


def test_camera_yaw_without_hmd_yaw_triggers_rebase():
    *_, triggered = maybe_rebase(Pose((0., 0., 0.), I), Pose((0., 0., 0.), I), yaw(10))
    assert triggered


def test_quaternion_signs_have_zero_angular_difference():
    q = qnorm((.2, -.3, .4, .8))
    assert quaternion_angular_difference_degrees(q, tuple(-v for v in q)) == pytest.approx(0.)


@pytest.mark.parametrize("degrees", [90, 180])
def test_snap_turn_rebases_tracking_axes(degrees):
    rebased, _, _, triggered = maybe_rebase(Pose((0., 0., 0.), I), Pose((0., 0., 0.), I), yaw(degrees))
    assert triggered
    close(qrotate(rebased.orientation, (0., 1., 0.)), qrotate(yaw(degrees), (0., 1., 0.)))


def test_smooth_turn_increments_eventually_cross_threshold():
    stored = Pose((0., 0., 0.), I)
    assert not maybe_rebase(stored, Pose((0., 0., 0.), I), yaw(.4))[3]
    assert maybe_rebase(stored, Pose((0., 0., 0.), I), yaw(.8))[3]


def test_hmd_position_is_identical_across_rebase():
    _, before, after = pivot_preserving_world_from_tracking_rebase(
        Pose((12., -4., 3.), yaw(20)), Pose((.4, 1.2, -.2), yaw(5)), yaw(110))
    close(before.position, after.position)


def test_controller_position_does_not_jump_when_world_hmd_pivot_is_preserved():
    stored, hmd = Pose((2., 4., 1.), I), Pose((.3, -.2, 1.6), I)
    rebased, before, after = pivot_preserving_world_from_tracking_rebase(stored, hmd, yaw(90))
    # At the rebase instant the relative pose expressed in the newly attached
    # tracking axes counter-rotates; normal HMD-relative composition stays fixed.
    controller_world_before = baseline_rigid_controller_world(before, Pose((.2, .4, -.1), I), Pose((0., 0., 0.), I))
    relative_after = Pose(qrotate(yaw(-90), (.2, .4, -.1)), yaw(-90))
    controller_world_after = baseline_rigid_controller_world(after, relative_after, Pose((0., 0., 0.), I))
    close(controller_world_before.position, controller_world_after.position)
    assert rebased.orientation == pytest.approx(yaw(90))


@pytest.mark.parametrize("degrees", [90, 180])
def test_translation_after_artificial_turn_follows_new_heading(degrees):
    stored = Pose((0., 0., 0.), I); at_turn = Pose((0., 0., 0.), I)
    rebased, _, _ = pivot_preserving_world_from_tracking_rebase(stored, at_turn, yaw(degrees))
    moved = compose(rebased, Pose((0., 1., 0.), I)).position
    close(moved, qrotate(yaw(degrees), (0., 1., 0.)))


def test_full_artificial_turn_does_not_accumulate_position_drift():
    stored, hmd = Pose((5., 7., 2.), I), Pose((.3, -.4, 1.7), I)
    original = compose(stored, hmd).position
    for degrees in (90, 180, 270, 360):
        stored, _, after = pivot_preserving_world_from_tracking_rebase(stored, hmd, yaw(degrees))
        close(after.position, original)


def test_natural_physical_360_rotation_never_rebases():
    stored = Pose((0., 0., 0.), I)
    for degrees in range(0, 361, 15):
        assert not maybe_rebase(stored, Pose((0., 0., 0.), yaw(degrees)), yaw(degrees))[3]


def test_mode_change_and_recenter_clear_artificial_state():
    lua = LUA.read_text()
    reset = lua.split("local function resetHmdBaseline", 1)[1].split("local function vec3ToTable", 1)[0]
    assert "artificialYawRebaseCount=0" in reset
    assert "state.artificialYawRebaseTriggered=false" in reset
    assert "resetHmdBaseline('translation mode changed to '..mode)" in lua


def test_invalid_hmd_cannot_reach_artificial_rebase_block():
    lua = LUA.read_text()
    valid_block = lua.split("if valid then\n  mappedTrackingHmd", 1)[1].split("local candidates=hmdCandidates", 1)[0]
    assert "cfg.hmdTranslationMode=='baselineRigidPositionBeamngRotationRebased'" in valid_block


def test_beamng_only_remains_default():
    import json
    settings = json.loads((ROOT / "mod/settings/beamngVRControllerPoses.json").read_text())
    assert settings["hmdTranslationMode"] == "beamngOnly"


def test_pr26_and_pr27_candidate_formulas_remain_present():
    lua = LUA.read_text()
    assert "lastBaselineRigidCandidate=compose(rigidBaseline.worldFromTracking,mappedTrackingHmd)" in lua
    assert "p={baselineRigidCandidate.p[1],baselineRigidCandidate.p[2],baselineRigidCandidate.p[3]}" in lua
    assert "q=qnorm(cameraAnchor.q)" in lua


def test_hot_path_and_predicted_pose_safety_remain_intact():
    lua = LUA.read_text()
    assert "sock:settimeout(0)" in lua
    assert "local results=packValues(geluaOriginalGetter(...))" in lua
    assert "local hmdWorld=beamngWorld" in lua
    assert "openxr-layer/src/layer.cpp" not in str(LUA)
