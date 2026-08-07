import json
from pathlib import Path


LUA = Path("mod/lua/ge/extensions/beamngVRControllerPoses.lua")
SETTINGS = Path("mod/settings/beamngVRControllerPoses.json")


def lua():
    return LUA.read_text()


def test_native_api_and_safe_lifecycle_are_explicit():
    text = lua()
    for name in ("startNativeSourcePoseDiagnostics", "stopNativeSourcePoseDiagnostics",
                 "getNativeSourcePoseDiagnosticState"):
        assert f"function M.{name}" in text
    assert "if nativeSource.enabled then return true end" in text
    assert "nativeSource.enabled=false; nativeSource.failureReason='diagnostics disabled'" in text
    assert "type(OpenXR)~='table'" in text
    assert "type(OpenXR.getInputSourceStates)~='function'" in text
    assert "type(OpenXR.getSourcePoseStates)~='function'" in text


def test_native_functions_are_called_under_pcall_and_never_replaced():
    text = lua()
    assert "pcall(OpenXR.getInputSourceStates)" in text
    assert "pcall(OpenXR.getSourcePoseStates,source.path)" in text
    assert "OpenXR.getInputSourceStates=" not in text
    assert "OpenXR.getSourcePoseStates=" not in text


def test_handedness_and_pose_semantics_are_path_based():
    text = lua()
    assert "path=='/user/hand/left'" in text
    assert "path=='/user/hand/right'" in text
    assert ":find('/grip/pose',1,true)" in text
    assert ":find('/aim/pose',1,true)" in text
    assert "for posePath,rawPose in pairs(poses)" in text


def test_validation_raw_preservation_and_exact_formula_are_explicit():
    text = lua()
    for phrase in ("source.active~=true", "raw.active~=nil and raw.active~=true",
                   "raw.poseValid~=true", "position is missing or non-finite",
                   "quaternion is missing or non-finite", "quaternion has zero length"):
        assert phrase in text
    assert "rawNativePosition=p,rawNativeQuaternion=q" in text
    assert "geluaCapture.rawAnchorPosition[1]+p[1]" in text
    block = text.split("local function pollNativeSourcePoses", 1)[1].split(
        "local function drawNativeSourcePoses", 1)[0]
    for forbidden in ("mappedPosition", "worldDelta", "fixedDelta", "hmdBaseline",
                      "rebasedWorldFromTracking", "gain", "smooth"):
        assert forbidden not in block


def test_visible_cap_yellow_colour_and_hand_markers_are_separate():
    text = lua()
    assert "index<=cap" in text
    assert "ColorF(1,1,0,1)" in text
    assert "ColorF(0,1,1,1) or ColorF(1,0,1,1)" in text
    assert "state.leftControllerWorld=" not in text
    assert "state.rightControllerWorld=" not in text


def test_configuration_defaults_disabled_with_requested_sizes():
    cfg = json.loads(SETTINGS.read_text())["nativeSourcePoseDiagnostics"]
    assert cfg == {"enabled": False, "pollIntervalSeconds": 0.0,
                   "logIntervalSeconds": 1.0, "gripSphereDiameter": 0.07,
                   "aimSphereDiameter": 0.05, "otherSphereDiameter": 0.03,
                   "maxVisibleCandidatesPerHand": 8}
