"""PR #30 moving BeamNG camera-anchor motion-separation tests."""
import json
from math import cos, inf, pi, sin
from pathlib import Path

import pytest

from beamng_vr_poses.math3d import (
    Pose, baseline_rigid_controller_world, compose, moving_anchor_update,
    pivot_preserving_world_from_tracking_rebase, separate_camera_motion,
)

I = (0., 0., 0., 1.)
ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def yaw(degrees):
    angle = degrees * pi / 360
    return (0., 0., sin(angle), cos(angle))


def update(attachment, previous_anchor, current_anchor, tracking_hmd,
           previous_final, camera_q=I, jump=5.):
    return moving_anchor_update(attachment, previous_anchor, current_anchor,
                                tracking_hmd, previous_final, camera_q, jump)


def test_physical_only_motion_is_not_applied_twice():
    result = update(Pose((0., 0., 0.), I), (0., 0., 0.), (1., 2., 3.),
                    Pose((1., 2., 3.), I), (0., 0., 0.))
    moved, raw, physical, residual, jump, _, _, final = result
    assert not jump
    assert raw == physical == (1., 2., 3.)
    assert residual == pytest.approx((0., 0., 0.))
    assert moved.position == (0., 0., 0.)
    assert final.position == (1., 2., 3.)


def test_stick_walking_only_applies_raw_core_delta():
    moved, raw, physical, residual, *_ = update(
        Pose((4., 5., 6.), I), (1., 1., 1.), (3., 0., 1.),
        Pose((0., 0., 0.), I), (4., 5., 6.))
    assert raw == residual == (2., -1., 0.)
    assert physical == (0., 0., 0.)
    assert moved.position == (6., 4., 6.)


def test_simultaneous_physical_and_stick_motion_are_each_applied_once():
    result = update(Pose((0., 0., 0.), I), (0., 0., 0.), (1.25, 2.5, 0.),
                    Pose((.25, .5, 0.), I), (0., 0., 0.))
    moved, raw, physical, residual, *_, final = result
    assert raw == (1.25, 2.5, 0.) and physical == (.25, .5, 0.)
    assert residual == (1., 2., 0.)
    assert moved.position == (1., 2., 0.)
    assert final.position == (1.25, 2.5, 0.)


@pytest.mark.parametrize("natural_yaw", [0, 45, 90, 180, 270])
def test_physical_translation_at_natural_yaws_has_no_residual(natural_yaw):
    physical = (0.3, -0.2, 0.1)
    result = update(Pose((0., 0., 0.), I), (0., 0., 0.), physical,
                    Pose(physical, yaw(natural_yaw)), (0., 0., 0.), yaw(natural_yaw))
    assert result[3] == pytest.approx((0., 0., 0.))


def test_physical_translation_after_ninety_degree_artificial_turn_remains_once():
    attachment, _, _ = pivot_preserving_world_from_tracking_rebase(
        Pose((0., 0., 0.), I), Pose((0., 0., 0.), I), yaw(90))
    tracking = Pose((1., 0., 0.), I)
    pre = compose(attachment, tracking)
    result = update(attachment, (0., 0., 0.), pre.position, tracking,
                    (0., 0., 0.), yaw(90))
    assert result[3] == pytest.approx((0., 0., 0.))
    assert result[-1].position == pytest.approx(pre.position)


def test_stick_walking_after_ninety_degree_turn_uses_world_delta():
    attachment, _, _ = pivot_preserving_world_from_tracking_rebase(
        Pose((0., 0., 0.), I), Pose((0., 0., 0.), I), yaw(90))
    result = update(attachment, (0., 0., 0.), (0., 2., 0.),
                    Pose((0., 0., 0.), I), (0., 0., 0.), yaw(90))
    assert result[3] == (0., 2., 0.)
    assert result[0].position == pytest.approx((0., 2., 0.))


def test_snap_turn_and_translation_same_frame_preserve_position():
    result = update(Pose((3., 4., 1.), I), (0., 0., 0.), (1., -2., 0.),
                    Pose((.2, .3, 1.6), I), (3.2, 4.3, 2.6), yaw(90))
    assert result[5]
    assert result[-1].position == pytest.approx((4.2, 2.3, 2.6))


def test_smooth_turn_while_walking_does_not_duplicate_physical_motion():
    attachment, anchor, final = Pose((0., 0., 0.), I), (0., 0., 0.), (0., 0., 0.)
    for angle in (1., 2., 3., 4.):
        tracking = Pose((angle / 100., 0., 0.), I)
        current = (angle / 100. + .25, 0., 0.)
        result = update(attachment, anchor, current, tracking, final, yaw(angle))
        attachment, anchor, final = result[0], current, result[-1].position
    assert final == pytest.approx((.29, 0., 0.))


def test_physical_return_to_start_returns_candidate():
    attachment = Pose((10., 20., 30.), I)
    first = update(attachment, (0., 0., 0.), (1., 0., 0.), Pose((1., 0., 0.), I), attachment.position)
    second = update(first[0], (1., 0., 0.), (0., 0., 0.), Pose((0., 0., 0.), I), first[-1].position)
    assert second[-1].position == pytest.approx(attachment.position)


def test_equal_forward_backward_game_deltas_return_attachment():
    attachment = Pose((10., 20., 30.), I)
    forward = update(attachment, (0., 0., 0.), (2., 0., 0.), Pose((0., 0., 0.), I), attachment.position)
    backward = update(forward[0], (2., 0., 0.), (0., 0., 0.), Pose((0., 0., 0.), I), forward[-1].position)
    assert backward[0].position == pytest.approx(attachment.position)


def test_both_controllers_receive_combined_motion_once():
    result = update(Pose((0., 0., 0.), I), (0., 0., 0.), (1.5, 0., 0.),
                    Pose((.5, 0., 0.), I), (0., 0., 0.))
    hmd = result[-1]
    for x in (-.2, .2):
        controller = baseline_rigid_controller_world(hmd, Pose((x, .3, 0.), I), Pose((0., 0., 0.), I))
        assert controller.position == pytest.approx((1.5 + x, .3, 0.))


@pytest.mark.parametrize("bad", [(inf, 0., 0.), (float("nan"), 0., 0.)])
def test_non_finite_data_is_rejected_before_state_can_advance(bad):
    with pytest.raises(ValueError):
        update(Pose((0., 0., 0.), I), (0., 0., 0.), bad,
               Pose((0., 0., 0.), I), (0., 0., 0.))


def test_jump_clears_residual_application():
    original = Pose((1., 2., 3.), I)
    result = update(original, (0., 0., 0.), (5.01, 0., 0.),
                    Pose((0., 0., 0.), I), original.position)
    assert result[4] and result[0] == original


def test_motion_separation_helper_validates_and_reconstructs():
    raw, physical = (3., -1., .5), (1., .25, -.5)
    residual = separate_camera_motion(raw, physical)
    assert residual == (2., -1.25, 1.)
    assert tuple(physical[i] + residual[i] for i in range(3)) == raw
    with pytest.raises(ValueError):
        separate_camera_motion((inf, 0., 0.), (0., 0., 0.))


def test_lifecycle_mode_and_diagnostics_are_wired_additively():
    lua = LUA.read_text()
    reset = lua.split("local function resetHmdBaseline", 1)[1].split("local function vec3ToTable", 1)[0]
    for field in ("movingWorldFromTracking=nil", "previousBeamngCameraAnchorPosition=nil",
                  "previousFinalMovingHmdWorldPosition=nil"):
        assert field in reset
    assert "resetHmdBaseline('translation mode changed to '..mode)" in lua
    assert "baselineRigidPositionBeamngRotationRebasedMovingAnchor=true" in lua
    assert "ColorF(1,0.2,0.6,1)" in lua
    for diagnostic in ("rawCoreCameraDelta", "physicalTrackingWorldDelta",
                       "residualGameLocomotionDelta", "movingReconstructionError"):
        assert diagnostic in lua


def test_previous_candidate_formulas_and_default_remain_unchanged():
    lua = LUA.read_text()
    assert "lastBaselineRigidCandidate=compose(rigidBaseline.worldFromTracking,mappedTrackingHmd)" in lua
    assert "lastRebasedRigidCandidate=compose(rebasedWorldFromTracking,mappedTrackingHmd)" in lua
    settings = json.loads((ROOT / "mod/settings/beamngVRControllerPoses.json").read_text())
    assert settings["hmdTranslationMode"] == "beamngOnly"


def test_lua_subtracts_physical_motion_and_never_applies_raw_delta_directly():
    lua = LUA.read_text()
    block = lua.split("local preAnchorHmdWorld=", 1)[1].split("local movingTargetQ=", 1)[0]
    assert "rawCoreCameraDelta[1]-physicalTrackingWorldDelta[1]" in block
    assert "movingWorldFromTracking.p[1]+residualGameLocomotionDelta[1]" in block
    assert "movingWorldFromTracking.p[1]+rawCoreCameraDelta[1]" not in block
    assert "qrot(rawCoreCameraDelta" not in block
