from pathlib import Path


ROOT = Path(__file__).parents[1]
ATTACHMENT = ROOT / "mod/lua/ge/extensions/vrPistolAttachment.lua"
PROVIDER = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"


def source() -> str:
    return ATTACHMENT.read_text(encoding="utf-8")


def test_uses_right_controller_public_pose_api_and_expected_external_model():
    text = source()
    assert "pcall(poseProvider.getControllerWorldPose,'right')" in text
    assert "'/art/shapes/vrpistol/vr_pistol.dae'" in text


def test_validates_complete_fresh_pose():
    text = source()
    assert "pose.valid~=true" in text
    assert "pose.ageMs>maximumPoseAgeMs" in text
    for field in ("p.x", "p.y", "p.z", "q.x", "q.y", "q.z", "q.w"):
        assert f"finite({field})" in text
    assert "q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w==0" in text


def test_complete_xyzw_transform_and_named_grip_offsets_are_used():
    text = source()
    assert "local gripPositionOffset" in text
    assert "local gripRotationOffset" in text
    assert "composeGripTransform(pose.position,pose.orientation)" in text
    assert "orientation.x,orientation.y,orientation.z,orientation.w" in text


def test_single_instance_hiding_recovery_and_cleanup_contract():
    text = source()
    assert text.count("createObject('TSStatic')") == 1
    assert "if pistol then return true end" in text
    assert "setHidden(true); transitionTracking(state)" in text
    assert "right-controller tracking recovered" in text
    assert "function M.onExtensionUnloaded() cleanup('extension unloaded') end" in text
    assert "function M.onProviderUnloaded()" in text
    assert "pistol:delete()" in text


def test_no_primitive_or_fallback_pistol_construction():
    text = source().lower()
    assert "createobject('tsstatic')" in text
    for forbidden in ("cube.dae", "primitive", "fallback pistol", "vrmockpistol"):
        assert forbidden not in text


def test_provider_optionally_loads_attachment_without_declared_dependency_cycle():
    provider = PROVIDER.read_text(encoding="utf-8")
    assert "pcall(function()" in provider
    assert "extensions.load('vrPistolAttachment')" in provider
    assert "optional vrPistolAttachment load failed" in provider
    assert "dependencies" not in source()


def test_external_renderer_is_disabled_to_avoid_duplicate_object():
    text = source()
    assert "extensions.vrPistolVisual" in text
    assert "pcall(current.setEnabled,false)" in text


def test_external_renderer_disappearance_and_reload_reset_suppression_state():
    text = source()
    suppression = text.split("local function disableBundledRenderer()", 1)[1].split(
        "\nend\n\nlocal function cleanup", 1
    )[0]
    assert "if not current then" in suppression
    assert "externalRendererReference=nil" in suppression
    assert "externalRendererDisabled=false" in suppression
    assert "if current~=externalRendererReference then" in suppression
    assert "externalRendererReference=current" in suppression
    assert "pcall(current.isEnabled)" in suppression
    assert "pcall(current.setEnabled,false)" in suppression
