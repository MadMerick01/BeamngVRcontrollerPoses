from math import cos, pi, sin
from pathlib import Path

import pytest

from beamng_vr_poses.math3d import (
    Pose, gelua_native_camera_composition, gelua_native_controller_world, qmul,
)

I = (0.0, 0.0, 0.0, 1.0)
LUA = Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua')


def yaw(degrees):
    a = degrees * pi / 360
    return (0.0, 0.0, sin(a), cos(a))


def test_position_is_direct_anchor_plus_predicted():
    result = gelua_native_camera_composition(Pose((10, 20, 30), I), Pose((1, -2, 3), I))
    assert result.position == (11, 18, 33)


def test_rotation_is_setmulxyzw_predicted_then_anchor_order():
    predicted, anchor = yaw(30), (sin(pi/8), 0.0, 0.0, cos(pi/8))
    result = gelua_native_camera_composition(Pose((0, 0, 0), anchor), Pose((0, 0, 0), predicted))
    assert result.orientation == pytest.approx(qmul(predicted, anchor))
    assert result.orientation != pytest.approx(qmul(anchor, predicted))


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
