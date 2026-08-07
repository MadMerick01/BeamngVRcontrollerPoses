"""PR #29 moving BeamNG camera-anchor tests."""
import json
from math import cos, inf, pi, sin
from pathlib import Path

import pytest

from beamng_vr_poses.math3d import (
    Pose, baseline_rigid_controller_world, compose, moving_anchor_update,
    pivot_preserving_world_from_tracking_rebase, translate_world_from_tracking,
)

I = (0., 0., 0., 1.)
ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def yaw(degrees):
    angle = degrees * pi / 360
    return (0., 0., sin(angle), cos(angle))


@pytest.mark.parametrize("delta", [(0., 0., 0.), (2., 0., 0.), (0., 3., 0.), (0., 0., -4.)])
def test_anchor_delta_is_applied_exactly_in_world_axes(delta):
    pose = Pose((10., 20., 30.), yaw(90))
    assert translate_world_from_tracking(pose, delta).position == pytest.approx(
        tuple(pose.position[i] + delta[i] for i in range(3)))


def test_anchor_delta_is_not_rotated_by_live_hmd_yaw():
    moved, delta, jump, _ = moving_anchor_update(
        Pose((0., 0., 0.), I), (0., 0., 0.), (1., 0., 0.),
        Pose((0., 0., 0.), yaw(90)), yaw(90))
    assert not jump and delta == (1., 0., 0.) and moved.position == pytest.approx((1., 0., 0.))


def test_physical_hmd_motion_with_stationary_anchor_is_preserved():
    attachment, *_ = moving_anchor_update(Pose((4., 5., 6.), I), (2., 2., 2.),
                                          (2., 2., 2.), Pose((.3, -.2, .1), I), I)
    assert compose(attachment, Pose((.3, -.2, .1), I)).position == pytest.approx((4.3, 4.8, 6.1))


def test_stationary_hmd_follows_camera_once():
    moved, *_ = moving_anchor_update(Pose((4., 5., 6.), I), (1., 1., 1.),
                                     (3., 0., 1.), Pose((0., 0., 0.), I), I)
    assert compose(moved, Pose((0., 0., 0.), I)).position == pytest.approx((6., 4., 6.))


def test_simultaneous_anchor_and_physical_motion_compose_once_each():
    moved, *_ = moving_anchor_update(Pose((0., 0., 0.), I), (0., 0., 0.),
                                     (1., 2., 0.), Pose((.25, .5, 0.), I), I)
    assert compose(moved, Pose((.25, .5, 0.), I)).position == pytest.approx((1.25, 2.5, 0.))


def test_walking_after_ninety_degree_rebase_uses_world_camera_delta():
    rebased, _, _ = pivot_preserving_world_from_tracking_rebase(
        Pose((0., 0., 0.), I), Pose((0., 0., 0.), I), yaw(90))
    moved, *_ = moving_anchor_update(rebased, (0., 0., 0.), (0., 2., 0.),
                                     Pose((0., 0., 0.), I), yaw(90))
    assert moved.position == pytest.approx((0., 2., 0.))


def test_yaw_and_translation_same_frame_preserve_translated_pivot():
    moved, delta, _, rebased = moving_anchor_update(
        Pose((3., 4., 1.), I), (0., 0., 0.), (1., -2., 0.),
        Pose((.2, .3, 1.6), I), yaw(90))
    assert rebased and delta == (1., -2., 0.)
    assert compose(moved, Pose((.2, .3, 1.6), I)).position == pytest.approx((4.2, 2.3, 2.6))


def test_controllers_receive_identical_anchor_translation():
    hmd = Pose((3., 4., 1.), I)
    left = baseline_rigid_controller_world(hmd, Pose((-.2, .3, 0.), I), Pose((0., 0., 0.), I))
    right = baseline_rigid_controller_world(hmd, Pose((.2, .3, 0.), I), Pose((0., 0., 0.), I))
    delta = (2., -1., .5)
    moved_hmd = translate_world_from_tracking(hmd, delta)
    for original, relative in ((left, Pose((-.2, .3, 0.), I)), (right, Pose((.2, .3, 0.), I))):
        moved = baseline_rigid_controller_world(moved_hmd, relative, Pose((0., 0., 0.), I))
        assert tuple(moved.position[i] - original.position[i] for i in range(3)) == pytest.approx(delta)


def test_repeated_increments_and_return_do_not_drift():
    attachment, anchor = Pose((10., 20., 30.), I), (0., 0., 0.)
    for current in ((.1, 0., 0.), (.2, 0., 0.), (.3, 0., 0.), (0., 0., 0.)):
        attachment, *_ = moving_anchor_update(attachment, anchor, current, Pose((0., 0., 0.), I), I)
        anchor = current
    assert attachment.position == pytest.approx((10., 20., 30.))


def test_excessive_delta_requests_fresh_baseline_without_old_translation():
    original = Pose((1., 2., 3.), I)
    moved, delta, jump, _ = moving_anchor_update(original, (0., 0., 0.), (5.01, 0., 0.),
                                                 Pose((0., 0., 0.), I), I, 5.)
    assert jump and delta == (5.01, 0., 0.) and moved == original


@pytest.mark.parametrize("bad", [(inf, 0., 0.), (float("nan"), 0., 0.)])
def test_non_finite_anchor_positions_are_rejected(bad):
    with pytest.raises(ValueError):
        moving_anchor_update(Pose((0., 0., 0.), I), (0., 0., 0.), bad,
                             Pose((0., 0., 0.), I), I)


def test_lifecycle_mode_and_diagnostics_are_wired_additively():
    lua = LUA.read_text()
    reset = lua.split("local function resetHmdBaseline", 1)[1].split("local function vec3ToTable", 1)[0]
    assert "movingWorldFromTracking=nil" in reset
    assert "previousBeamngCameraAnchorPosition=nil" in reset
    assert "resetHmdBaseline('translation mode changed to '..mode)" in lua
    assert "baselineRigidPositionBeamngRotationRebasedMovingAnchor=true" in lua
    assert "ColorF(1,0.2,0.6,1)" in lua


def test_previous_candidate_formulas_remain_unchanged_and_default_stays_beamng_only():
    lua = LUA.read_text()
    assert "lastBaselineRigidCandidate=compose(rigidBaseline.worldFromTracking,mappedTrackingHmd)" in lua
    assert "lastRebasedRigidCandidate=compose(rebasedWorldFromTracking,mappedTrackingHmd)" in lua
    assert "p={baselineRigidCandidate.p[1],baselineRigidCandidate.p[2],baselineRigidCandidate.p[3]}" in lua
    settings = json.loads((ROOT / "mod/settings/beamngVRControllerPoses.json").read_text())
    assert settings["hmdTranslationMode"] == "beamngOnly"
    assert settings["beamngAnchorJumpMetres"] == 5.0


def test_lua_world_delta_is_direct_and_not_quaternion_rotated():
    lua = LUA.read_text()
    block = lua.split("local anchorDelta=", 1)[1].split("local movingTargetQ=", 1)[0]
    assert "movingWorldFromTracking.p={movingWorldFromTracking.p[1]+anchorDelta[1]" in block
    assert "qrot" not in block
