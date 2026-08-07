import json
import math
from pathlib import Path


ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def source():
    return LUA.read_text()


def qrot(q, p):
    x, y, z, w = q
    tx, ty, tz = 2 * (y * p[2] - z * p[1]), 2 * (z * p[0] - x * p[2]), 2 * (x * p[1] - y * p[0])
    return (
        p[0] + w * tx + y * tz - z * ty,
        p[1] + w * ty + z * tx - x * tz,
        p[2] + w * tz + x * ty - y * tx,
    )


def hybrid(position, camera_orientation):
    length = math.sqrt(sum(value * value for value in camera_orientation))
    return tuple(position), tuple(value / length for value in camera_orientation)


def test_hybrid_keeps_rigid_position_exactly_and_uses_live_camera_orientation():
    rigid_position = (12.5, -7.25, 1.75)
    camera_q = (0, 0, math.sqrt(0.5), math.sqrt(0.5))
    position, orientation = hybrid(rigid_position, camera_q)
    assert position == rigid_position
    assert orientation == camera_q

    block = source().split("local hybridCandidate=", 1)[1].split("return {", 1)[0]
    assert "baselineRigidCandidate.p[1],baselineRigidCandidate.p[2],baselineRigidCandidate.p[3]" in block
    assert "q=qnorm(cameraAnchor.q)" in block
    assert "qinv" not in block and "qrot" not in block


def test_live_yaw_rotates_hybrid_orientation_and_forward_sphere_not_position():
    position = (4, 5, 6)
    identity = (0, 0, 0, 1)
    yaw_90 = (0, 0, math.sqrt(0.5), math.sqrt(0.5))
    first_position, first_q = hybrid(position, identity)
    second_position, second_q = hybrid(position, yaw_90)
    assert first_position == second_position == position
    assert first_q != second_q
    assert qrot(first_q, (0, 1, 0)) == (0, 1, 0)
    rotated = qrot(second_q, (0, 1, 0))
    assert math.isclose(rotated[0], -1) and math.isclose(rotated[1], 0, abs_tol=1e-12)

    draw = source().split("local function drawDiagnostics", 1)[1]
    assert "compose(candidates.baselineRigidPositionBeamngRotation,{p=localPos" in draw
    assert "ColorF(0,1,1,1)" in draw


def test_hybrid_controller_composition_is_normal_and_hands_are_independent():
    lua = source()
    update = lua.split("local function updateHand", 1)[1].split("local validHmdTranslationModes", 1)[0]
    assert "compose(cameraWorld,compose(rel,offset))" in update
    assert "inverse(hmdInBase) * controllerInBase" in update
    assert "updateHand('left',latest.left,hmdWorld,now); updateHand('right',latest.right,hmdWorld,now)" in lua
    assert "diagnosticControllerWorld('left',latest.left,candidates.baselineRigidPositionBeamngRotation)" in lua
    assert "diagnosticControllerWorld('right',latest.right,candidates.baselineRigidPositionBeamngRotation)" in lua

    yaw_90 = (0, 0, math.sqrt(0.5), math.sqrt(0.5))
    left = qrot(yaw_90, (-0.3, 0.4, 0))
    right = qrot(yaw_90, (0.3, 0.4, 0))
    assert left != right
    assert all(math.isclose(actual, expected, abs_tol=1e-12) for actual, expected in zip(left, (-0.4, -0.3, 0)))
    assert all(math.isclose(actual, expected, abs_tol=1e-12) for actual, expected in zip(right, (-0.4, 0.3, 0)))


def test_mode_is_additive_resets_baseline_and_safely_falls_back_until_valid():
    lua = source()
    modes = lua.split("local validHmdTranslationModes=", 1)[1].split("\n", 1)[0]
    for mode in (
        "beamngOnly", "beamngPlusHmdDelta", "beamngMinusHmdDelta",
        "beamngFixedBaseHmdDelta", "baselineRigidTracking",
        "baselineRigidPositionBeamngRotation",
    ):
        assert mode + "=true" in modes
    assert "resetHmdBaseline('translation mode changed to '..mode)" in lua
    assert "rigidBaseline=nil; lastBaselineRigidCandidate=nil" in lua
    assert "mode=='baselineRigidPositionBeamngRotation' and not candidates.baselineRigidPositionBeamngRotation then mode='beamngOnly'" in lua
    assert "local selected=candidates[mode] or candidates.beamngOnly" in lua
    settings = json.loads((ROOT / "mod/settings/beamngVRControllerPoses.json").read_text())
    assert settings["hmdTranslationMode"] == "beamngOnly"


def test_pr26_calculation_and_required_hybrid_state_remain_explicit():
    lua = source()
    assert "lastBaselineRigidCandidate=compose(rigidBaseline.worldFromTracking,mappedTrackingHmd)" in lua
    for field in (
        "baselineRigidPositionBeamngRotationHmdWorld", "baselineRigidPosition",
        "baselineRigidTrackingOrientation", "beamngLiveCameraOrientation",
        "selectedHybridOrientation", "hybridLeftControllerWorld",
        "hybridRightControllerWorld", "hybridDiagnosticSphereWorldPosition",
    ):
        assert "state." + field in lua
        assert "state.diagnostics." + field in lua or "'" + field + "'" in lua
    assert "baselineRigidTracking=purple" in lua
