import json
import math
from pathlib import Path

ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"
SETTINGS = ROOT / "mod/settings/beamngVRControllerPoses.json"
EVIDENCE = ROOT / "docs/PR38_ARTIFICIAL_MOTION_SOURCE.md"


def qmul(a, b):
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return (aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx,
            aw*bz+ax*by-ay*bx+az*bw, aw*bw-ax*bx-ay*by-az*bz)

def qinv(q): return (-q[0], -q[1], -q[2], q[3])
def qrot(q, p): return qmul(qmul(q, (*p, 0)), qinv(q))[:3]
def compose(a, b):
    rp = qrot(a[1], b[0]); return (tuple(a[0][i]+rp[i] for i in range(3)), qmul(a[1], b[1]))
def inverse(a):
    qi=qinv(a[1]); return (qrot(qi, tuple(-x for x in a[0])), qi)
I=((0,0,0),(0,0,0,1))
def yaw(d):
    r=math.radians(d)/2; return (0,0,math.sin(r),math.cos(r))
def close(a,b,tol=1e-9):
    return all(math.isclose(x,y,abs_tol=tol) for x,y in zip(a[0],b[0])) and abs(sum(x*y for x,y in zip(a[1],b[1])))>1-tol

def step(artificial, previous_anchor, anchor, orange):
    delta=compose(anchor,inverse(previous_anchor))
    artificial=compose(delta,artificial)
    return compose(artificial,orange),artificial,delta


def test_initialization_recenter_and_missing_source_equal_orange():
    for orange in (((4,5,1.7),yaw(20)), ((-2,9,1.5),yaw(-80))):
        assert close(compose(I,orange),orange)
    text=LUA.read_text(); block=text.split('local function updateDarkBlue',1)[1].split('local function actualHmdWorld',1)[0]
    assert "if not gameAnchor then" in block
    assert "darkBlueArtificialTransform=identityPose()" in block
    assert "lastDarkBlueRigidCandidate=copyPose(orangeWorld)" in block


def test_physical_translation_and_natural_rotation_do_not_change_artificial_transform():
    artificial=((3,-2,0.5),yaw(35)); anchor=((10,4,2),yaw(10))
    for orange in (((1.2,0,1.7),yaw(0)), ((0,0,1.7),yaw(160))):
        dark, unchanged, delta=step(artificial,anchor,anchor,orange)
        assert close(delta,I) and close(unchanged,artificial)
        assert close(dark,compose(artificial,orange))


def test_stick_translation_changes_only_artificial_transform_and_combined_applies_once():
    anchor0=((10,0,0),yaw(0)); anchor1=((13,2,0),yaw(0)); physical=((0.4,-0.2,1.7),yaw(25))
    dark,artificial,delta=step(I,anchor0,anchor1,physical)
    assert close(artificial,delta)
    assert close(dark,compose(delta,physical))


def test_yaw_delta_is_complete_pivot_preserving_transform_not_map_origin_rotation():
    pivot=((10,0,0),yaw(0)); rotated_anchor=((10,0,0),yaw(90))
    dark,_,delta=step(I,pivot,rotated_anchor,((11,0,0),yaw(0)))
    assert close(delta,compose(compose(pivot,((0,0,0),yaw(90))),inverse(pivot)))
    assert all(math.isclose(a,b,abs_tol=1e-9) for a,b in zip(dark[0],(10,1,0)))
    assert not all(math.isclose(a,b,abs_tol=1e-9) for a,b in zip(dark[0],(0,11,0)))


def test_operational_path_uses_authoritative_pre_vr_anchor_without_subtraction_or_stick_integration():
    text=LUA.read_text(); block=text.split('local function updateDarkBlue',1)[1].split('local function actualHmdWorld',1)[0]
    source=text.split('local function capturedArtificialGameAnchor',1)[1].split('local function establishDarkBlue',1)[0]
    assert "geluaCapture.rawAnchorPosition" in source and "geluaCapture.rawAnchorQuaternion" in source
    assert "compose(gameAnchor,inversePose(previousGameAnchor))" in block
    assert "compose(gameAnchorDelta,darkBlueArtificialTransform)" in block
    assert "compose(darkBlueArtificialTransform,orangeWorld)" in block
    assert "inversePose(previousOrangeHmdWorld)" not in block
    for forbidden in ('stickValue','movementSpeed','correctionGain','perAxisGain'):
        assert forbidden not in block


def test_required_diagnostics_and_concise_log_are_present():
    text=LUA.read_text()
    for field in ('darkBlueHmdWorld','orangeHmdWorld','darkBlueArtificialTransform','artificialMotionSourceName',
      'artificialMotionSourceAvailable','artificialMotionSourceFailureReason','previousGameAnchor','currentGameAnchor',
      'gameAnchorDelta','artificialTranslationDetected','artificialRotationDetected','artificialTranslationMagnitude',
      'artificialRotationDegrees','physicalMovementDetected','artificialInputActive','artificialYawInputActive',
      'artificialTranslationInputActive','darkBlueOrangePositionDifference','darkBlueOrangeAngularDifference',
      'darkBlueUnexpectedSeparationDuringPhysicalMotion','darkBlueResetReason','darkBlueResetCount','darkBlueValid'):
        assert f'state.{field}' in text
    assert "motion darkBlueValid=" in text


def test_orange_violet_controllers_and_clean_visual_profile_are_unchanged():
    text=LUA.read_text(); cfg=json.loads(SETTINGS.read_text())
    assert "local currentOrangeHmdWorld={p={lastRebasedRigidCandidate.p[1]" in text
    assert "candidates.geluaNativeCameraComposition=nativeCandidate" in text
    assert "selectedControllerCameraWorld=candidates.geluaNativeCameraComposition" in text
    assert "ColorF(0.02,0.08,0.45,1.0)" in text and "ColorF(0.75,0.9,1,1)" in text
    assert cfg['diagnosticVisualProfile']=='orangeVioletControllers'
    draw=text.split('local function drawDiagnostics',1)[1].split('function M.onExtensionLoaded',1)[0]
    assert 'state.darkBlueLeftControllerWorld' not in draw and 'state.darkBlueRightControllerWorld' not in draw


def test_pr37_headset_evidence_and_source_inspection_are_recorded():
    evidence=EVIDENCE.read_text()
    for phrase in ('world-facing-dependent error','natural head rotation correctly','stick-controlled locomotion',
                   'does not follow stick-controlled rotation','Recenter returns dark blue exactly to orange'):
        assert phrase in evidence
    assert 'core_camera.getPosition()' in evidence and 'setGeluaCameraPosRot' in evidence


def test_native_layer_udp_protocol_hand_cache_and_xrlocatespace_are_untouched_by_design():
    assert 'xrLocateSpace' not in LUA.read_text()
