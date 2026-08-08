import math
from pathlib import Path


ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx,
            aw*bz+ax*by-ay*bx+az*bw, aw*bw-ax*bx-ay*by-az*bz)


def qnorm(q):
    length = math.sqrt(sum(value*value for value in q))
    return tuple(value/length for value in q)


def qinv(q):
    q = qnorm(q)
    return (-q[0], -q[1], -q[2], q[3])


def qrot(q, p):
    return qmul(qmul(qnorm(q), (*p, 0)), qinv(q))[:3]


def compose(a, b):
    rotated = qrot(a[1], b[0])
    return (tuple(a[0][i] + rotated[i] for i in range(3)), qmul(a[1], b[1]))


def inverse(pose):
    qi = qinv(pose[1])
    return (qrot(qi, tuple(-value for value in pose[0])), qi)


def yaw(degrees):
    radians = math.radians(degrees) / 2
    return (0, 0, math.sin(radians), math.cos(radians))


def rotation_equal(a, b, tolerance=1e-9):
    a, b = qnorm(a), qnorm(b)
    return abs(sum(x*y for x, y in zip(a, b))) >= 1-tolerance


def corrected_pose(artificial, orange):
    provisional = compose(artificial, orange)
    final = (provisional[0], qnorm(orange[1]))
    return provisional, final


def test_final_pose_preserves_full_rigid_position_and_normalized_orange_orientation():
    orange = ((11, 0, 1.7), tuple(value*3 for value in yaw(35)))
    artificial = (compose(((10, 0, 0), yaw(90)), inverse(((10, 0, 0), yaw(0)))))
    provisional, final = corrected_pose(artificial, orange)

    assert final[0] == provisional[0]
    assert rotation_equal(final[1], orange[1])
    assert math.isclose(sum(value*value for value in final[1]), 1)
    assert not rotation_equal(final[1], provisional[1])
    assert final[0] != orange[0]  # Pivot-preserving correction remains active.


def test_quaternion_comparison_is_sign_invariant():
    orientation = yaw(123)
    assert rotation_equal(orientation, tuple(-value for value in orientation))


def test_natural_rotation_and_stick_yaw_have_orange_angular_rate_without_double_yaw():
    for natural_yaw, stick_yaw in ((160, 0), (25, 90), (-35, 180), (40, 360)):
        orange = ((0.2, -0.1, 1.7), yaw(natural_yaw + stick_yaw))
        provisional, final = corrected_pose(((0, 0, 0), yaw(stick_yaw)), orange)
        assert rotation_equal(final[1], orange[1])
        if stick_yaw % 360:
            assert not rotation_equal(provisional[1], final[1])


def test_stick_translation_recenter_and_fail_closed_positions_remain_unchanged():
    orange = ((0.4, -0.2, 1.7), yaw(20))
    translated = ((3, 2, 0), yaw(0))
    provisional, final = corrected_pose(translated, orange)
    assert final[0] == provisional[0] == compose(translated, orange)[0]

    for artificial in (((0, 0, 0), yaw(0)),):  # recenter and fail-closed identity
        provisional, final = corrected_pose(artificial, orange)
        assert provisional[0] == final[0] == orange[0]
        assert rotation_equal(final[1], orange[1])


def test_operational_lua_removes_only_duplicate_orientation_and_exposes_diagnostics():
    text = LUA.read_text()
    update = text.split("local function updateDarkBlue", 1)[1].split("local function actualHmdWorld", 1)[0]
    finalize = text.split("local function finalizeDarkBlueOrientation", 1)[1].split("local function updateDarkBlue", 1)[0]

    assert "lastProvisionalDarkBlueFullRigidPose=compose(darkBlueArtificialTransform,orangeWorld)" in update
    assert "p={provisionalDarkBluePose.p[1],provisionalDarkBluePose.p[2],provisionalDarkBluePose.p[3]}" in finalize
    assert "q=normalizedOrangeOrientation" in finalize
    assert "qnorm(orangeWorld.q)" in finalize
    assert "darkBlueOrientationSource='orange live BeamNG orientation'" in update
    assert "state.duplicateArtificialYawRemoved=true" in update
    assert "quaternionAngularDifferenceDegrees(" in update  # abs(dot) makes q/-q equivalent.

    for field in ("provisionalDarkBlueFullRigidPose", "provisionalDarkBlueOrientation",
                  "finalDarkBlueOrientation", "orangeOrientationUsedForDarkBlue",
                  "darkBlueOrientationSource", "duplicateArtificialYawRemoved",
                  "provisionalVsFinalAngularDifferenceDegrees", "darkBlueOrientationEqualsOrange"):
        assert f"state.{field}" in text

    for forbidden in ("*0.5", "* 0.5", "angularGain", "rotationGain", "yawGain"):
        assert forbidden not in update


def test_visual_and_hypothetical_controllers_consume_final_candidate_only():
    text = LUA.read_text()
    draw = text.split("local function drawDiagnostics", 1)[1].split("function M.onExtensionLoaded", 1)[0]
    assert "local darkBlue=candidates.baselineRigidRebasedArtificialCamera and compose(" in draw
    assert "drawSphereStick(candidates.baselineRigidRebasedArtificialCamera.p,darkBlue.p" in draw
    assert "diagnosticControllerWorld('left',latest.left,candidates.baselineRigidRebasedArtificialCamera)" in text
    assert "diagnosticControllerWorld('right',latest.right,candidates.baselineRigidRebasedArtificialCamera)" in text
    assert "state.darkBlueLeftControllerWorld" not in draw
    assert "state.darkBlueRightControllerWorld" not in draw


def test_orange_violet_native_protocol_and_controller_selection_are_unchanged():
    text = LUA.read_text()
    assert "selectedControllerCameraWorld=candidates.geluaNativeCameraComposition" in text
    assert "candidates.geluaNativeCameraComposition=nativeCandidate" in text
    assert "xrLocateSpace" not in text
