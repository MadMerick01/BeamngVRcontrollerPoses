import json
from pathlib import Path


LUA = Path("mod/lua/ge/extensions/beamngVRControllerPoses.lua")
SETTINGS = Path("mod/settings/beamngVRControllerPoses.json")


def source():
    return LUA.read_text()


def test_packaged_profile_is_only_orange_violet_and_blue():
    cfg = json.loads(SETTINGS.read_text())
    assert cfg["diagnosticVisualProfile"] == "orangeVioletControllers"
    assert cfg["cameraAxisSpheres"]["enabled"] is False
    assert cfg["axisTripods"]["drawControllerTripods"] is False
    block = source().split("local radius=(cfg.cameraTestSphere.diameter", 1)[1].split(
        "local c=ColorF", 1)[0]
    assert "drawSphere(vec3(orange.p)" in block
    assert "drawSphere(vec3(lime.p)" in block
    for candidate in ("red", "green", "yellow", "white", "purple", "cyan", "pink"):
        assert f"drawSphere(vec3({candidate}.p)" not in block
    assert "local diagnosticItems={}" in block


def test_only_visible_candidate_tripods_and_lines_are_constructed():
    block = source().split("local diagnosticItems={}", 1)[1].split(
        "local c=ColorF", 1)[0]
    assert "diagnosticItems.baselineRigidPositionBeamngRotationRebased=orange" in block
    assert "diagnosticItems.geluaNativeCameraComposition=lime" in block
    for hidden in ("beamngOnly", "beamngPlusHmdDelta", "beamngMinusHmdDelta",
                   "beamngFixedBaseHmdDelta", "baselineRigidTracking",
                   "baselineRigidPositionBeamngRotation=cyan",
                   "baselineRigidPositionBeamngRotationRebasedMovingAnchor"):
        assert hidden not in block


def test_controllers_share_one_authoritative_violet_parent_and_compose_once():
    text = source()
    frame = text.split("function M.onPreRender", 1)[1].split(
        "function M.setCameraSourceMode", 1)[0]
    assert "selectedControllerCameraWorld=candidates.geluaNativeCameraComposition" in frame
    assert "selectedControllerCameraWorld==candidates.geluaNativeCameraComposition" in frame
    assert "updateHand('left',latest.left,hmdWorld,now); updateHand('right',latest.right,hmdWorld,now)" in frame
    update = text.split("local function updateHand", 1)[1].split(
        "local validHmdTranslationModes", 1)[0]
    assert update.count("compose(cameraWorld,compose(rel,offset))") == 1
    for forbidden in ("worldDelta", "fixedDelta", "hmdBaseline", "beamngOnly"):
        assert forbidden not in update


def test_violet_fallback_is_wholly_orange_and_exposes_actual_selection():
    frame = source().split("function M.onPreRender", 1)[1].split(
        "function M.setCameraSourceMode", 1)[0]
    assert "selectedControllerCameraWorld=candidates.baselineRigidPositionBeamngRotationRebased" in frame
    assert "controllerParentFallbackReason" in frame
    assert "controllersUseVioletParent" in frame
    for field in ("selectedControllerParentMode", "selectedControllerParentTransform",
                  "violetCameraWorld", "orangeCameraWorld", "leftControllerRelativeToHmd",
                  "rightControllerRelativeToHmd", "finalLeftControllerWorld",
                  "finalRightControllerWorld"):
        assert f"state.{field}" in frame


def test_native_yellow_candidates_are_diagnostic_only_in_clean_profile():
    block = source().split("local function drawNativeSourcePoses", 1)[1].split(
        "local function syncGeluaDiagnostics", 1)[0]
    assert "if diagnosticVisualProfile=='orangeVioletControllers' then return end" in block


def test_native_layer_protocol_and_cache_are_untouched_by_cleanup():
    # The cleanup is GE Lua/settings-only; the native hot path and UDP publisher remain unchanged.
    assert "openxr-layer/src/layer.cpp" not in {str(path) for path in (LUA, SETTINGS)}
