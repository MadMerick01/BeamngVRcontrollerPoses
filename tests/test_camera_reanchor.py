from pathlib import Path
import json

ROOT = Path(__file__).parents[1]
LUA = (ROOT / 'mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()


def section(start, end):
    return LUA.split(start, 1)[1].split(end, 1)[0]


def test_rebuild_uses_only_raw_anchor_and_current_mapped_hmd():
    block = section('local function rebuildTrackingBaselineFromCapture', 'local function updateDarkBlue')
    assert 'capturedArtificialGameAnchor(now)' in block
    assert 'worldFromTracking=compose(anchor,inversePose(mappedTrackingHmd))' in block
    assert 'rigidBaseline={' in block and 'rebasedWorldFromTracking=copyPose(worldFromTracking)' in block
    assert 'geluaNativeCandidate' not in block
    assert 'rawPredicted' not in block
    assert 'core_camera' not in block


def test_atomic_frame_rebuilds_orange_then_resets_dark_blue():
    actual = section('local function actualHmdWorld', '\nlocal function receive')
    assert actual.index('rebuildTrackingBaselineFromCapture') < actual.index('lastRebasedRigidCandidate=compose')
    assert "establishDarkBlue(currentOrangeHmdWorld,reanchorAnchor,'camera tracking baseline rebuilt')" in actual
    assert 'return nil,nil' in actual


def test_context_and_fresh_anchor_cut_both_request_reattachment():
    assert 'cameraReanchorRequestedAfterSequence=geluaCapture.setterSequence' in LUA
    observe = section('local function observeRawAnchorDiscontinuity', 'local function establishDarkBlue')
    assert 'sequence<=lastObservedAnchorSequence' in observe
    assert "requestCameraReanchor('fresh raw setter-anchor discontinuity'" in observe
    settings = json.loads((ROOT / 'mod/settings/beamngVRControllerPoses.json').read_text())
    assert settings['cameraReanchorTranslationDiscontinuityMetres'] == 5.0
    assert settings['cameraReanchorAngularDiscontinuityDegrees'] == 120.0


def test_fail_closed_and_same_frame_controller_parenting():
    frame = section('function M.onPreRender', 'function M.setCameraSourceMode')
    assert 'state.stalePoseSuppressed=true' in frame
    assert "state.leftControllerWorld.valid=false; state.rightControllerWorld.valid=false" in frame
    assert frame.count("updateHand('left',latest.left,hmdWorld,now)") == 1
    assert frame.count("updateHand('right',latest.right,hmdWorld,now)") == 1
    assert 'state.controllersReparentedThisFrame=state.orangeBaselineRebuiltThisFrame and requestedDarkBlue' in frame


def test_forbidden_recenter_and_pose_sources_are_absent_from_reanchor_path():
    block = section('local function rebuildTrackingBaselineFromCapture', 'local function updateDarkBlue')
    for forbidden in ('OpenXR.centerNow', 'geluaNativeCameraComposition', 'rawPredictedPosition', 'rawPredictedQuaternion'):
        assert forbidden not in block
    assert 'OpenXR.centerNow' not in LUA
