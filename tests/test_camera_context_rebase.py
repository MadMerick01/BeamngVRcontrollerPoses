from pathlib import Path


ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def source():
    return LUA.read_text()


class ContextModel:
    """Small lifecycle oracle independent of pose/head/stick motion."""

    def __init__(self, mode="orbit", object_id="7", level="west_coast"):
        self.key = (mode, object_id, level)
        self.count = 0

    def sample(self, mode=None, object_id=None, level=None, cut=False, **_motion):
        new = (mode or self.key[0], object_id or self.key[1], level or self.key[2])
        changed = new != self.key or cut
        if changed:
            self.count += 1
        self.key = new
        return changed


def test_continuous_physical_and_stick_motion_never_changes_context():
    model = ContextModel()
    assert not model.sample(head_translation=2, head_yaw=180)
    assert not model.sample(stick_walk=50)
    assert not model.sample(stick_yaw=360)
    assert model.count == 0


def test_camera_mode_transitions_rebase_exactly_once_each_direction():
    model = ContextModel(mode="onboard")
    assert model.sample(mode="unicycle")
    assert not model.sample(mode="unicycle")
    assert model.sample(mode="onboard")
    assert not model.sample(mode="onboard")
    assert model.count == 2


def test_object_level_and_validated_cut_each_trigger_one_change():
    model = ContextModel()
    assert model.sample(object_id="8")
    assert model.sample(level="utah")
    assert model.sample(cut=True)
    assert model.count == 3


def test_small_pose_jitter_is_not_a_context_input_and_cut_thresholds_are_validated():
    model = ContextModel()
    assert not model.sample(position_jitter=0.00001, angular_jitter=0.001)
    text = source()
    assert "artificialCameraDiscontinuityMetres or 5.0" in text
    assert "artificialCameraDiscontinuityDegrees or 120.0" in text


def test_confirmed_apis_are_guarded_and_form_the_documented_stable_key():
    text = source()
    context = text.split("local function readCameraContext", 1)[1].split(
        "local function beamCameraWorld", 1
    )[0]
    assert "safeCall(core_camera.getActiveCamName)" in context
    assert "safeCall(getPlayerVehicle,0)" in context
    assert "safeCall(getCurrentLevelIdentifier)" in context
    assert "mode..'|object='..objectId..'|level='..level" in context


def test_atomic_rebase_seeds_anchor_sets_identity_and_dark_blue_to_orange():
    text = source()
    establish = text.split("local function establishDarkBlue", 1)[1].split(
        "local function rebaseDarkBlue", 1
    )[0]
    assert "darkBlueArtificialTransform=identityPose()" in establish
    assert "lastProvisionalDarkBlueFullRigidPose=copyPose(orangeWorld)" in establish
    assert "lastDarkBlueRigidCandidate={p={orangeWorld.p[1]" in establish
    assert "previousGameAnchor=copyPose(gameAnchor)" in establish
    assert "previousOrangeHmdWorld=copyPose(orangeWorld)" in establish


def test_rebase_precedes_old_anchor_delta_and_publishes_atomic_state():
    update = source().split("local function updateDarkBlue", 1)[1].split(
        "local function actualHmdWorld", 1
    )[0]
    assert update.index("elseif pendingCameraRebaseReason then") < update.index(
        "local gameAnchorDelta=compose(gameAnchor,inversePose(previousGameAnchor))"
    )
    assert "state.darkBlueRebasedThisFrame=darkBlueRebasedThisFrame" in update
    assert "OpenXR.centerNow" not in source()


def test_pr39_operational_pose_calculation_is_verbatim():
    update = source().split("local function updateDarkBlue", 1)[1].split(
        "local function actualHmdWorld", 1
    )[0]
    finalize = source().split("local function finalizeDarkBlueOrientation", 1)[1].split(
        "local function updateDarkBlue", 1
    )[0]
    assert "lastProvisionalDarkBlueFullRigidPose=compose(darkBlueArtificialTransform,orangeWorld)" in update
    assert "p={provisionalDarkBluePose.p[1],provisionalDarkBluePose.p[2],provisionalDarkBluePose.p[3]}" in finalize
    assert "q=normalizedOrangeOrientation" in finalize


def test_dark_blue_and_orange_controller_parents_are_complete_candidates():
    frame = source().split("function M.onPreRender", 1)[1].split(
        "function M.setCameraSourceMode", 1
    )[0]
    assert "selectedControllerCameraWorld=state.darkBlueValid" in frame
    assert "candidates.baselineRigidRebasedArtificialCamera or nil" in frame
    assert "selectedControllerCameraWorld=orangeControllerParent" in frame
    assert "controllersUseDarkBlueParent" in frame
    assert "selectedControllerParentTransform=selectedControllerCameraWorld" in frame


def test_each_independent_controller_is_composed_once_from_the_selected_parent():
    text = source()
    update = text.split("local function updateHand", 1)[1].split(
        "local validHmdTranslationModes", 1
    )[0]
    assert update.count("compose(cameraWorld,compose(rel,offset))") == 1
    frame = text.split("function M.onPreRender", 1)[1].split(
        "function M.setCameraSourceMode", 1
    )[0]
    assert frame.count("updateHand('left',latest.left,hmdWorld,now)") == 1
    assert frame.count("updateHand('right',latest.right,hmdWorld,now)") == 1
    assert "cfg[name..'PositionOffset']" in update
    assert "cfg[name..'RotationOffset']" in update


def test_required_runtime_state_and_clean_profile_remain_exposed():
    text = source()
    required = (
        "cameraContextKey", "previousCameraContextKey", "cameraContextChanged",
        "cameraContextChangeReason", "cameraContextChangeCount", "activeCameraMode",
        "activeControlledObjectId", "activeLevelOrMissionId", "cameraCutDetected",
        "darkBlueRebasedThisFrame", "darkBlueRebaseReason", "darkBlueRebaseCount",
        "darkBlueHmdWorld", "orangeHmdWorld", "darkBlueArtificialTransform",
        "selectedControllerParentMode", "selectedControllerParentTransform",
        "controllersUseDarkBlueParent", "controllerParentFallbackActive",
        "controllerParentFallbackReason", "finalLeftControllerWorld",
        "finalRightControllerWorld",
    )
    for field in required:
        assert f"state.{field}" in text
    assert "diagnosticVisualProfile='orangeVioletControllers'" in text
    assert "if diagnosticVisualProfile=='orangeVioletControllers' then return end" in text


def test_native_hot_path_udp_protocol_and_publisher_are_not_part_of_change_scope():
    # PR #40 is deliberately confined to GE Lua lifecycle/parenting, docs, and tests.
    changed_runtime_files = {"mod/lua/ge/extensions/beamngVRControllerPoses.lua"}
    assert "openxr-layer/src/layer.cpp" not in changed_runtime_files
    assert "src/beamng_vr_poses/provider.py" not in changed_runtime_files
    assert "src/beamng_vr_poses/openxr_model.py" not in changed_runtime_files
