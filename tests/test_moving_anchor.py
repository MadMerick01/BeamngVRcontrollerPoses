"""PR #31 absolute moving-anchor tests.

These replace PR #29's accumulated camera-delta and PR #30's physical/residual
separation assertions. Live headset evidence disproved both models: the current
camera is the absolute anchor and PR #28 orange supplies the physical offset.
"""
import json
from math import cos, inf, pi, sin
from pathlib import Path

import pytest

from beamng_vr_poses.math3d import (
    Pose, anchor_physical_offset_to_current_camera,
    baseline_rigid_controller_world, compose,
    pivot_preserving_world_from_tracking_rebase,
)

I = (0., 0., 0., 1.)
ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def yaw(degrees):
    angle = degrees * pi / 360
    return (0., 0., sin(angle), cos(angle))


def anchored(camera, baseline, current, orientation=I):
    offset, final = anchor_physical_offset_to_current_camera(camera, baseline, current)
    return offset, Pose(final, orientation)


def test_baseline_is_zero_and_final_equals_current_anchor():
    offset, final = anchored((10., 20., 30.), (2., 3., 4.), (2., 3., 4.))
    assert offset == (0., 0., 0.)
    assert final.position == (10., 20., 30.)


def test_physical_only_uses_orange_offset_once():
    offset, final = anchored((10., 20., 30.), (2., 3., 4.), (2.5, 2., 6.))
    assert offset == (.5, -1., 2.)
    assert final.position == (10.5, 19., 32.)


def test_stick_walking_only_follows_absolute_current_anchor():
    offset, final = anchored((99., -4., 8.), (1., 2., 3.), (1., 2., 3.))
    assert offset == (0., 0., 0.)
    assert final.position == (99., -4., 8.)


def test_simultaneous_movement_includes_each_component_once():
    offset, final = anchored((5., 7., 9.), (1., 1., 1.), (3., 0., 1.5))
    assert offset == (2., -1., .5)
    assert final.position == (7., 6., 9.5)


def test_natural_yaw_uses_camera_orientation_without_rotating_offset():
    offset, final = anchored((0., 0., 0.), (0., 0., 0.), (1., 2., 3.), yaw(90))
    assert offset == (1., 2., 3.)
    assert final.position == (1., 2., 3.)
    assert final.orientation == yaw(90)


def test_artificial_yaw_rebase_preserves_existing_offset_and_rotates_later_axes():
    tracking = Pose((1., 0., 0.), I)
    transform, before, after = pivot_preserving_world_from_tracking_rebase(
        Pose((0., 0., 0.), I), tracking, yaw(90))
    assert after.position == pytest.approx(before.position)
    baseline = (0., 0., 0.)
    assert anchored((4., 5., 6.), baseline, after.position)[1].position == pytest.approx((5., 5., 6.))
    later = compose(transform, Pose((2., 0., 0.), I))
    assert later.position != pytest.approx(after.position)


@pytest.mark.parametrize("degrees", [180, 360])
def test_large_artificial_yaw_has_no_pivot_drift(degrees):
    tracking = Pose((.3, -.8, 1.6), I)
    _, before, after = pivot_preserving_world_from_tracking_rebase(
        Pose((4., 7., -2.), I), tracking, yaw(degrees))
    assert after.position == pytest.approx(before.position)


def test_recenter_and_later_camera_motion_are_absolute():
    leaned = (3., 4., 5.)
    assert anchored((20., 30., 40.), leaned, leaned)[1].position == (20., 30., 40.)
    assert anchored((24., 29., 42.), leaned, leaned)[1].position == (24., 29., 42.)


def test_returning_camera_has_no_accumulated_error():
    baseline = current = (1., 1., 1.)
    anchored((50., 0., 0.), baseline, current)
    assert anchored((0., 0., 0.), baseline, current)[1].position == (0., 0., 0.)


def test_controllers_share_final_hmd_and_remain_independent():
    hmd = anchored((10., 20., 30.), (0., 0., 0.), (.5, 0., 0.))[1]
    left = baseline_rigid_controller_world(hmd, Pose((-.2, .3, 0.), I), Pose((0., 0., 0.), I))
    right = baseline_rigid_controller_world(hmd, Pose((.2, .3, 0.), I), Pose((0., 0., 0.), I))
    assert left.position == pytest.approx((10.3, 20.3, 30.))
    assert right.position == pytest.approx((10.7, 20.3, 30.))


@pytest.mark.parametrize("bad", [(inf, 0., 0.), (float("nan"), 0., 0.)])
def test_non_finite_inputs_are_rejected(bad):
    with pytest.raises(ValueError):
        anchor_physical_offset_to_current_camera(bad, (0., 0., 0.), (0., 0., 0.))


def test_lua_absolute_formula_reset_jump_and_diagnostics_are_wired():
    lua = LUA.read_text()
    assert "resetHmdBaseline('translation mode changed to '..mode)" in lua
    assert "resetHmdBaseline('BeamNG camera anchor jump')" in lua
    assert "baselineRigidPositionBeamngRotationRebasedMovingAnchor=true" in lua
    assert "ColorF(1,0.2,0.6,1)" in lua
    for diagnostic in (
        "baselineOrangeReferenceHmdWorldPosition", "currentOrangeReferenceHmdWorldPosition",
        "physicalOffsetFromRecenter", "physicalOffsetFromRecenterMagnitude",
        "currentBeamngCameraAnchorPosition", "absoluteMovingHmdWorldPosition",
        "absoluteMovingHmdWorldOrientation", "absoluteMovingHybridHmdWorld",
        "absoluteMovingLeftControllerWorld", "absoluteMovingRightControllerWorld",
        "absoluteMovingDiagnosticSphereWorldPosition", "movingReferenceWorldFromTracking",
        "movingArtificialYawAlignmentDeltaDegrees", "movingArtificialYawRebaseCount",
        "movingAnchorResetReason", "movingAnchorJumpDetected",
    ):
        assert diagnostic in lua
    assert "currentAnchor[1]+physicalOffsetFromRecenter[1]" in lua


def test_disproven_operational_models_are_absent():
    lua = LUA.read_text()
    for obsolete in (
        "rawCoreCameraDelta", "residualGameLocomotionDelta",
        "accumulatedBeamngAnchorTranslation", "physicalMotionSubtracted",
        "movingReconstructionError", "previousFinalMovingHmdWorldPosition",
    ):
        assert obsolete not in lua
    assert "anchorStepMagnitude" in lua  # discontinuity detection only


def test_sources_previous_formulas_and_default_remain_unchanged():
    lua = LUA.read_text()
    assert "anchorSource='core_camera.getPosition'" in lua
    assert "physicalOffsetSource='PR28 orange reference from native OpenXR HMD'" in lua
    assert "orientationSource='corrected core_camera.getQuat'" in lua
    assert "lastBaselineRigidCandidate=compose(rigidBaseline.worldFromTracking,mappedTrackingHmd)" in lua
    assert "lastRebasedRigidCandidate=compose(rebasedWorldFromTracking,mappedTrackingHmd)" in lua
    settings = json.loads((ROOT / "mod/settings/beamngVRControllerPoses.json").read_text())
    assert settings["hmdTranslationMode"] == "beamngOnly"


def test_helper_has_no_previous_frame_arguments():
    assert anchor_physical_offset_to_current_camera.__code__.co_argcount == 3
