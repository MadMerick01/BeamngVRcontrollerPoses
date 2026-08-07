import json
from pathlib import Path


LUA_PATH = Path("mod/lua/ge/extensions/beamngVRControllerPoses.lua")
DOC_PATH = Path("docs/PR32_GELUA_CAMERA_ANCHOR.md")


def source():
    return LUA_PATH.read_text()


def capture_block():
    lua = source()
    return lua.split("local geluaCameraAnchorCapture={", 1)[1].split("\n}", 1)[0]


def test_capture_api_is_explicit_and_not_installed_on_load():
    lua = source()
    load = lua.split("function M.onExtensionLoaded()", 1)[1].split(
        "function M.onPreRender", 1
    )[0]
    assert "startGeluaCameraAnchorCapture()" not in load
    assert "function M.startGeluaCameraAnchorCapture()" in lua
    assert "function M.stopGeluaCameraAnchorCapture()" in lua
    assert "function M.getGeluaCameraAnchorCaptureState()" in lua


def test_unproven_dynamic_lookup_fails_closed_without_replacing_or_invoking_setter():
    lua = source()
    start = lua.split("function M.startGeluaCameraAnchorCapture()", 1)[1].split(
        "\nend\nfunction M.stopGeluaCameraAnchorCapture", 1
    )[0]
    assert "return false,geluaCaptureFailureReason" in start
    assert "wrapperInstalled=false" in start
    assert "OpenXR.setGeluaCameraPosRot=" not in lua
    assert "OpenXR.setGeluaCameraPosRot(" not in lua
    assert "originalSetGeluaCameraPosRot" not in lua


def test_stop_and_unload_are_idempotent_and_original_binding_stays_untouched():
    lua = source()
    stop = lua.split("function M.stopGeluaCameraAnchorCapture()", 1)[1].split(
        "\nend\nfunction M.getGeluaCameraAnchorCaptureState", 1
    )[0]
    assert "wrapperInstalled=false" in stop
    assert "originalFunctionPreserved=true" in stop
    unload = lua.split("function M.onExtensionUnloaded()", 1)[1].split("\nend", 1)[0]
    assert "M.stopGeluaCameraAnchorCapture()" in unload


def test_complete_raw_diagnostic_contract_is_exposed_separately_from_interpretation():
    block = capture_block()
    for field in (
        "callCounter",
        "lastCallTimestamp",
        "timeSinceLastCall",
        "callsPerSecond",
        "completeArgumentCount",
        "completeRawArguments",
        "requiredValuesNumericFinite",
        "lastSuccessfulPassthrough",
        "lastPassthroughError",
        "wrapperInstalled",
        "originalFunctionPreserved",
        "captureAvailable",
        "captureFailureReason",
        "anyCallsObserved",
    ):
        assert field in block
    assert "completeRawArguments=nil" in block
    for field in (
        "capturedGeluaCameraPosition",
        "capturedGeluaCameraRawQuaternion",
        "capturedGeluaCameraQuaternionNormalized",
        "capturedGeluaCameraQuaternionInverse",
        "capturedGeluaCameraTransformAsProvided",
        "capturedGeluaCameraTransformInverted",
    ):
        assert f"{field}=nil" in block


def test_no_unproven_candidate_controls_hmd_world_hands_or_visuals():
    lua = source()
    assert "local hmdWorld=beamngWorld" in lua
    assert "updateHand('left',latest.left,hmdWorld,now)" in lua
    assert "updateHand('right',latest.right,hmdWorld,now)" in lua
    update = lua.split("local function updateHand(", 1)[1].split(
        "\nend\nlocal validHmdTranslationModes", 1
    )[0]
    draw = lua.split("local function drawDiagnostics(", 1)[1].split(
        "\nend\nfunction M.onExtensionLoaded", 1
    )[0]
    assert "gelua" not in update.lower()
    assert "gelua" not in draw.lower()
    assert "visualCandidatesDrawn=false" in capture_block()


def test_existing_operational_modes_and_defaults_are_unchanged():
    lua = source()
    settings = json.loads(Path("mod/settings/beamngVRControllerPoses.json").read_text())
    modes = lua.split("local validHmdTranslationModes=", 1)[1].split("\n", 1)[0]
    assert "baselineRigidPositionBeamngRotationRebased=true" in modes
    assert "gelua" not in modes.lower()
    assert settings["hmdTranslationMode"] == "beamngOnly"
    assert settings["cameraSourceMode"] == "beamngOnly"


def test_logging_is_not_in_a_setter_wrapper_or_per_render_capture_path():
    lua = source()
    render = lua.split("function M.onPreRender", 1)[1].split(
        "function M.setCameraSourceMode", 1
    )[0]
    assert "setGeluaCameraPosRot" not in render
    assert "capture stop" in lua
    assert "capture unavailable" in lua


def test_static_evidence_and_headset_commands_are_documented():
    doc = DOC_PATH.read_text()
    for unknown in (
        "Argument count/order | Unknown",
        "Position units | Unknown",
        "Quaternion ordering | Unknown",
        "Camera-to-world vs world-to-camera | Unknown",
        "Called from GE Lua every frame | Not proven",
        "Dynamic global-table lookup | Not proven",
    ):
        assert unknown in doc
    for command in (
        "extensions.load('beamngVRControllerPoses')",
        "baselineRigidPositionBeamngRotationRebased",
        "startGeluaCameraAnchorCapture()",
        "stopGeluaCameraAnchorCapture()",
        "getGeluaCameraAnchorCaptureState()",
    ):
        assert command in doc
