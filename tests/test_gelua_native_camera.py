from math import cos, pi, sin
from pathlib import Path

import pytest

from beamng_vr_poses.math3d import (
    Pose, compose, gelua_native_camera_composition, gelua_native_controller_world,
    gelua_raw_native_view_quaternion, qmul, qnorm, qrotate, quaternion_inverse,
)

I = (0.0, 0.0, 0.0, 1.0)
LUA = Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua')


def yaw(degrees):
    a = degrees * pi / 360
    return (0.0, 0.0, sin(a), cos(a))


def test_position_is_direct_anchor_plus_predicted():
    result = gelua_native_camera_composition(Pose((10, 20, 30), I), Pose((1, -2, 3), I))
    assert result.position == (11, 18, 33)


def test_raw_rotation_is_normalized_predicted_then_anchor_order():
    predicted, anchor = yaw(30), (sin(pi/8), 0.0, 0.0, cos(pi/8))
    a, p = Pose((0, 0, 0), anchor), Pose((0, 0, 0), predicted)
    raw = gelua_raw_native_view_quaternion(a, p)
    result = gelua_native_camera_composition(a, p)
    assert raw == pytest.approx(qnorm(qmul(predicted, anchor)))
    assert raw != pytest.approx(qnorm(qmul(anchor, predicted)))
    assert result.orientation == pytest.approx(quaternion_inverse(raw))


def test_actual_capture_converts_view_to_known_good_world_rotation_once():
    anchor = Pose((5.2109110161, 1.7976832610, 1.4440261722),
                  (0, 0, 0.59177796762011, 0.80610100920381))
    predicted = Pose((-0.0302995145, 0.0222750939, 0.0036439896),
                     (0.078285835683346, -0.11317580193281,
                      0.14424252510071, -0.97992688417435))
    raw = gelua_raw_native_view_quaternion(anchor, predicted)
    world = gelua_native_camera_composition(anchor, predicted)
    assert raw == pytest.approx((-0.00386865, -0.13755896, -0.46362509, -0.87527960), abs=3e-8)
    assert world.orientation == pytest.approx((0.00386865, 0.13755896, 0.46362509, -0.87527960), abs=3e-8)
    assert world.position == pytest.approx((5.1806115016, 1.8199583549, 1.4476701618), abs=1e-10)
    # q and -q are equivalent, but a conjugate is not: compare directions too.
    assert qrotate(world.orientation, (0, 1, 0)) == pytest.approx(
        qrotate(quaternion_inverse(raw), (0, 1, 0)))
    assert qrotate(world.orientation, (0, 1, 0)) != pytest.approx(qrotate(raw, (0, 1, 0)))


@pytest.mark.parametrize('rotation', [yaw(35),
    (sin(pi/12), 0, 0, cos(pi/12)), (0, sin(pi/10), 0, cos(pi/10))])
def test_natural_yaw_pitch_and_roll_follow_known_good_direction(rotation):
    raw_view = quaternion_inverse(rotation)
    result = gelua_native_camera_composition(Pose((0, 0, 0), I), Pose((0, 0, 0), raw_view))
    assert qrotate(result.orientation, (0, 1, 0)) == pytest.approx(qrotate(rotation, (0, 1, 0)))


def test_identity_stays_identity_and_forward_diagnostic_is_world_facing():
    result = gelua_native_camera_composition(Pose((2, 3, 4), I), Pose((0, 0, 0), I))
    assert result.orientation == I
    assert compose(result, Pose((0, 1, 0), I)).position == (2, 4, 4)


def test_physical_and_game_components_remain_independent():
    anchor = Pose((100, 200, 300), yaw(45))
    base = gelua_native_camera_composition(anchor, Pose((0, 0, 0), I))
    physical = gelua_native_camera_composition(anchor, Pose((1, 2, 3), I))
    walked = gelua_native_camera_composition(Pose((110, 190, 305), yaw(45)), Pose((0, 0, 0), I))
    assert tuple(physical.position[i] - base.position[i] for i in range(3)) == (1, 2, 3)
    assert walked.position == (110, 190, 305)


def test_controller_uses_final_camera_exactly_once_and_hands_are_independent():
    anchor, predicted = Pose((10, 0, 0), I), Pose((2, 0, 0), I)
    left = gelua_native_controller_world(anchor, predicted, Pose((-1, 1, 0), I), Pose((0, 0, 0), I))
    right = gelua_native_controller_world(anchor, predicted, Pose((1, 1, 0), I), Pose((0, 0, 0), I))
    assert left.position == (11, 1, 0)
    assert right.position == (13, 1, 0)


def test_controller_and_calibration_offset_use_converted_parent_orientation():
    camera_world = yaw(90)
    raw_view = quaternion_inverse(camera_world)
    relative = Pose((0, 1, 0), yaw(15))
    offset = Pose((0.25, 0, 0), yaw(10))
    actual = gelua_native_controller_world(Pose((0, 0, 0), I),
                                           Pose((0, 0, 0), raw_view), relative, offset)
    expected = compose(Pose((0, 0, 0), camera_world), compose(relative, offset))
    assert actual.position == pytest.approx(expected.position)
    assert qrotate(actual.orientation, (0, 1, 0)) == pytest.approx(qrotate(expected.orientation, (0, 1, 0)))


def test_lua_wrapper_lifecycle_and_exact_forwarding_are_explicit():
    lua = LUA.read_text()
    assert lua.count('geluaOriginalSetter(...)') == 1
    assert lua.count('geluaOriginalGetter(...)') == 1
    assert 'local results=packValues(geluaOriginalSetter(...))' in lua
    assert 'local results=packValues(geluaOriginalGetter(...))' in lua
    assert 'if geluaCapture.captureInstalled then return true end' in lua
    assert 'OpenXR.setGeluaCameraPosRot==geluaSetterWrapper' in lua
    assert 'OpenXR.getCameraPosRotPredictedXYZXYZW==geluaGetterWrapper' in lua
    assert 'function M.onExtensionUnloaded() M.stopGeluaCameraAnchorCapture()' in lua


def test_pairing_staleness_mismatch_and_fallback_are_explicit():
    lua = LUA.read_text()
    for expression in (
        'geluaCapture.getterSequence~=geluaCapture.setterSequence',
        'geluaCapture.getterTimestamp<geluaCapture.setterTimestamp',
        'geluaCapture.pairAgeMs>maxAgeMs',
        "mode='baselineRigidPositionBeamngRotationRebased'",
        "state.selectedModeFallbackReason=nativeFailure",
    ):
        assert expression in lua


def test_native_mode_has_no_reconstruction_heuristics():
    block = LUA.read_text().split('local function geluaNativeCandidate', 1)[1].split('\nend', 1)[0]
    for forbidden in ('worldDelta', 'fixedDelta', 'physicalOffsetFromRecenter',
                      'movingWorldFromTracking', 'artificialYaw', 'gain', 'smooth'):
        assert forbidden not in block


def test_lua_boundary_diagnostics_are_separate_and_operational_pose_is_converted():
    lua = LUA.read_text()
    assert 'qnorm(qmul({pq[1],pq[2],pq[3],pq[4]},{aq[1],aq[2],aq[3],aq[4]}))' in lua
    assert 'local nativeCameraToWorldQuaternion=qinv(rawNativeViewQuaternion)' in lua
    assert 'q=nativeCameraToWorldQuaternion' in lua
    for field in ('geluaRawNativeViewQuaternion', 'geluaNativeCameraToWorldQuaternion',
                  'geluaQuaternionBoundaryConversion'):
        assert field in lua
    assert "'raw normalize(predicted * anchor), then inverse view-to-world'" in lua
    assert 'ColorF(0.5,0,1,1)' in lua
