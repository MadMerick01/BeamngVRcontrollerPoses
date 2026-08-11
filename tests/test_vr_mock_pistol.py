import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "mod/lua/ge/extensions/vrMockPistol.lua").read_text()
SETTINGS_PATH = ROOT / "mod/settings/vrMockPistol.json"


def test_existing_settings_keep_manual_right_hand_freshness_configuration():
    settings = json.loads(SETTINGS_PATH.read_text())
    assert settings["enabled"] is False
    assert settings["hand"] == "right"
    assert settings["maximumPoseAgeMs"] == 125


def test_one_tsstatic_uses_the_external_corrected_dae():
    assert "local pistolShape='/art/shapes/vrpistol/vr_pistol.dae'" in SOURCE
    assert SOURCE.count("createObject('TSStatic')") == 1
    assert "cube_1m.dae" not in SOURCE
    assert "barrel" not in SOURCE.lower()
    assert "handle" not in SOURCE.lower()
    assert "setScale" not in SOURCE


def test_final_right_controller_pose_is_forwarded_without_conversion():
    assert "provider.getControllerWorldPose('right')" in SOURCE
    assert "pistol:setPosRot(p.x,p.y,p.z,q.x,q.y,q.z,q.w)" in SOURCE
    assert "provider.getState" not in SOURCE
    forbidden = (
        "cameraAnchor",
        "OpenXR",
        "trackingSpace",
        "quaternionBasis",
        "calibration",
        "compose(",
        "qmul",
        "qrot",
    )
    assert not any(word in SOURCE for word in forbidden)


def test_invalid_or_stale_pose_only_hides_the_visual():
    assert "pose.ageMs<=cfg.maximumPoseAgeMs" in SOURCE
    invalid_branch = SOURCE[SOURCE.index(
        "if not state.rightControllerPoseValid or not pistol then"
    ):]
    assert "setHidden(true)" in invalid_branch
    assert "state.visible=false" in invalid_branch
    assert "return" in invalid_branch


def test_object_creation_is_outside_the_per_frame_update_and_cleanup_is_owned():
    update = SOURCE[
        SOURCE.index("function M.onPreRender"):
        SOURCE.index("function M.onExtensionLoaded")
    ]
    assert "createObject" not in update
    assert "createPistol()" not in update
    assert "pistol:setPosRot" in update
    assert "function M.onExtensionUnloaded()" in SOURCE
    unload = SOURCE[SOURCE.index("function M.onExtensionUnloaded()") :]
    assert "destroyPistol()" in unload
    assert "pistol:delete()" in SOURCE


def test_missing_asset_or_object_failure_is_not_retried_each_frame():
    create = SOURCE[
        SOURCE.index("local function createPistol"):
        SOURCE.index("local function readSettings")
    ]
    assert "if creationAttempted then return pistol~=nil end" in create
    assert "creationAttempted=true" in create
    assert "pcall(function() object.shapeName=pistolShape end)" in create
    update = SOURCE[
        SOURCE.index("function M.onPreRender"):
        SOURCE.index("function M.onExtensionLoaded")
    ]
    assert "if not state.rightControllerPoseValid or not pistol then" in update


def test_visual_only_contract_disables_collision_and_has_no_gameplay_behaviour():
    lower = SOURCE.lower()
    for forbidden in (
        "projectile",
        "firing",
        "recoil",
        "nodegrab",
        "activevehiclesiterator",
        "inputevent",
        "damage",
        "muzzle",
        "debugdrawer",
    ):
        assert forbidden not in lower
    assert "configureField(object,'collision configuration','collisionType','None',true)" in SOURCE
    assert "configureField(object,'shadow configuration','castShadows','0',false)" in SOURCE
    assert "configureField(object,'object persistence','canSave','0',false)" in SOURCE


def test_extension_remains_manually_enableable_and_is_not_lazy_loaded():
    assert "function M.setEnabled(enabled)" in SOURCE
    assert "function M.onExtensionLoaded()" in SOURCE
    assert "function M.onPreRender()" in SOURCE
    assert "extensions.load" not in SOURCE
    assert "extensions.loadAtRoot" not in SOURCE


def test_failure_state_remains_available_for_manual_diagnostics():
    state = SOURCE[SOURCE.index("function M.getState()") :]
    for field in (
        "creationStage",
        "failedOperation",
        "failedField",
        "lastError",
    ):
        assert field in state
