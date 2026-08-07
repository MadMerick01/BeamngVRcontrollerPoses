import json
import socket
import threading
import pytest
from pathlib import Path
from beamng_vr_poses.math3d import Pose, compose
from beamng_vr_poses.openxr_model import *

I=(0.,0.,0.,1.)
def test_action_subaction_mapping_and_multiple_candidates():
    r=SpaceRegistry(); r.add_action(1,ActionMeta('pose','hand_pose','Hand pose',('/user/hand/left','/user/hand/right')))
    r.add_action(2,ActionMeta('boolean','click','Click',('/user/hand/left',)))
    r.add_space(10,'s',1,'/user/hand/right'); r.add_space(11,'s',1,'/user/hand/left'); r.add_space(12,'s',1,'/user/hand/left'); r.add_space(13,'s',2,'/user/hand/left')
    assert [r.spaces[x][2] for x in (10,11,12,13)] == ['right','left','left',None]

def test_space_and_session_cleanup():
    r=SpaceRegistry(); r.add_action(1,ActionMeta('pose','p','P',('/user/hand/left',))); r.add_space(10,'a',1,'/user/hand/left');r.add_space(11,'b',1,'/user/hand/left')
    r.destroy_space(10); assert 10 not in r.spaces
    r.destroy_session('b'); assert not r.spaces

def test_validity_requires_both_pose_components_not_tracking_bits():
    assert usable(POSITION_VALID|ORIENTATION_VALID)
    assert usable(POSITION_VALID|ORIENTATION_VALID|POSITION_TRACKED|ORIENTATION_TRACKED)
    assert not usable(POSITION_VALID|POSITION_TRACKED|ORIENTATION_TRACKED)

def test_same_base_and_time_relative_calculation():
    h=Pose((10,2,3),I); relative=Pose((.3,-.2,-.5),I); c=compose(h,relative)
    assert relative_pose(h,c).position == pytest.approx(relative.position)

def test_packet_roundtrip_and_stale_rejection():
    p=Pose((1,2,3),I); data=encode_packet(7,123,(p,REQUIRED_VALID),(p,0)); decoded=decode_packet(data)
    assert decoded['left']['valid'] and not decoded['right']['valid']
    try: decode_packet(data,7)
    except ValueError: pass
    else: assert False

def test_protocol_2_hmd_extension_and_legacy_packet_compatibility():
    p=Pose((1,2,3),I)
    legacy=decode_packet(encode_packet(1,100,(p,REQUIRED_VALID),(p,REQUIRED_VALID)))
    assert 'hmd' not in legacy
    extended=decode_packet(encode_packet(2,101,(p,REQUIRED_VALID),(p,REQUIRED_VALID),
                                         (p,REQUIRED_VALID),session='session-a',base='local-a'))
    assert extended['v'] == 2 and extended['hmd']['valid']
    assert extended['hmd']['sampleTime'] == 101
    assert extended['hmd']['session'] == 'session-a' and extended['hmd']['base'] == 'local-a'

def test_udp_receiver_absent_is_safe():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    assert s.sendto(b'probe',('127.0.0.1',44449)) == 5

def test_api_layer_unknown_calls_pass_to_next_dispatch():
    source=(Path(__file__).parents[1]/'openxr-layer/src/layer.cpp').read_text()
    assert 'd->second.gipa(i,n,f)' in source
    assert 'auto result=d.locateSpace(space,base,time,loc)' in source
    assert 'd.locateSpace(session->second.layerView,base,time,&head)' in source

def test_concurrent_snapshot_publication_and_lookup():
    registry=SnapshotRegistry(); registry.add_session('a','view-a')
    failures=[]
    def writer():
        for i in range(2000):
            registry.add_space(i,'a','left' if i%2 else 'right')
            if i: registry.destroy_space(i-1)
    def reader():
        generation=-1
        for _ in range(10000):
            snapshot=registry.snapshot()
            if snapshot.generation < generation: failures.append('generation regressed')
            generation=snapshot.generation
            for space in tuple(snapshot.spaces): SnapshotRegistry.lookup(snapshot,space)
    threads=[threading.Thread(target=writer),threading.Thread(target=reader),threading.Thread(target=reader)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not failures

def test_snapshot_stays_valid_across_space_and_session_destruction():
    registry=SnapshotRegistry(); registry.add_session('a','owned-view'); registry.add_space(1,'a','left')
    old=registry.snapshot(); registry.destroy_space(1); registry.destroy_session('a')
    assert SnapshotRegistry.lookup(old,1) == (('a','left'),'owned-view')
    assert SnapshotRegistry.lookup(registry.snapshot(),1) is None
    assert registry.destroyed_views == ['owned-view']

def test_multiple_sessions_and_candidates_are_isolated():
    registry=SnapshotRegistry()
    for session in ('a','b'): registry.add_session(session,'view-'+session)
    registry.add_space(1,'a','left');registry.add_space(2,'a','left');registry.add_space(3,'b','right')
    snap=registry.snapshot()
    assert [SnapshotRegistry.lookup(snap,x)[0][1] for x in (1,2,3)] == ['left','left','right']
    registry.destroy_session('a')
    assert SnapshotRegistry.lookup(registry.snapshot(),3) is not None

def test_invalid_and_stale_snapshot_fail_open_in_model():
    registry=SnapshotRegistry(); stale=registry.snapshot();registry.add_session('a',None);registry.add_space(1,'a','left')
    assert SnapshotRegistry.lookup(stale,1) is None
    assert SnapshotRegistry.lookup(registry.snapshot(),999) is None

def test_view_creation_or_locate_failure_is_nonfatal():
    p=Pose((1,2,3),I)
    result,published=model_locate('success',p,REQUIRED_VALID,'failure',p,REQUIRED_VALID)
    assert result == 'success' and published is None
    registry=SnapshotRegistry();registry.add_session('s',None)
    assert registry.snapshot().sessions['s'] is None

def test_downstream_locate_failure_is_returned_unchanged():
    p=Pose((1,2,3),I)
    result,published=model_locate('runtime-failure',p,REQUIRED_VALID,'success',p,REQUIRED_VALID)
    assert result == 'runtime-failure' and published is None

def test_successful_locate_preserves_flags_and_math():
    head=Pose((10,0,0),I);controller=Pose((11,2,3),I)
    result,published=model_locate('success',controller,REQUIRED_VALID|POSITION_TRACKED,'success',head,REQUIRED_VALID)
    assert result == 'success' and published[0].position == pytest.approx((1,2,3))
    assert published[1] == REQUIRED_VALID|POSITION_TRACKED

def test_locate_hot_path_has_no_blocking_or_io_operations():
    source=(Path(__file__).parents[1]/'openxr-layer/src/layer.cpp').read_text()
    body=source.split('layerLocateSpace(',1)[1].split('\n}\n\nXRAPI_ATTR',1)[0]
    for forbidden in ('lock_guard','writerMutex','sendto(','fopen(','sleep_for','make_shared','new '):
        assert forbidden not in body
    assert 'publishedRegistry.load' in body and 'InFlight sessionInFlight' in body and 'InFlight spaceInFlight' in body

def test_valid_candidate_not_overwritten_by_later_invalid_candidate_source():
    source=(Path(__file__).parents[1]/'openxr-layer/src/layer.cpp').read_text()
    assert 'if(incoming.valid)' in source
    assert 'without latching stale ghost controllers indefinitely' in source

def lua_source():
    return Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()



def lua_source():
    return Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()


def function_block(name, next_name):
    source=lua_source()
    return source.split('local function '+name,1)[1].split('\nlocal function '+next_name,1)[0]


def test_beamng_only_remains_default_and_only_one_candidate_mode_exists():
    source=lua_source(); settings=json.loads(Path('mod/settings/beamngVRControllerPoses.json').read_text())
    assert settings['hmdTranslationMode']=='beamngOnly'
    assert "local hmdTranslationMode='beamngOnly'" in source
    setter=source.split('function M.setHmdTranslationMode(mode)',1)[1].split('\nend',1)[0]
    assert "mode~='beamngOnly' and mode~='baselineRigidTracking'" in setter
    assert "if mode=='baselineRigidTracking' then resetHmdBaseline" in setter


def test_full_pose_inverse_and_composition_are_used_for_candidate():
    source=lua_source(); block=function_block('updateRigidCandidate(cameraWorld,hmd)', 'controllerPose')
    assert 'compose(baseline.worldFromTracking,mapped)' in block
    assert 'compose(compose(baselineBeamngCameraWorld, inverse(baselineTrackingHmd)), currentTrackingHmd)' in block
    inverse_block=function_block('inverse(t)', 'finiteNumber')
    assert 'qrot(qi,{-t.p[1],-t.p[2],-t.p[3]})' in inverse_block
    for forbidden in ('worldDelta','gain','smoothing','translation subtraction'):
        assert forbidden not in block


def test_invalid_tracking_pose_preserves_last_candidate():
    block=function_block('updateRigidCandidate(cameraWorld,hmd)', 'controllerPose')
    assert 'if not hmd or not hmd.valid or not validPose(hmd) then return state.baselineRigidCandidateHmdWorld end' in block
    valid=function_block('validPose(p)', 'copyPose')
    assert 'finiteNumber' in valid and 'qnorm(p.q)' in valid


def test_baseline_reset_events_and_normal_yaw_does_not_reset():
    source=lua_source(); block=function_block('updateRigidCandidate(cameraWorld,hmd)', 'controllerPose')
    assert "resetHmdBaseline('OpenXR session or base space changed')" in block
    assert "resetHmdBaseline('OpenXR sample time reset')" in block
    assert "resetHmdBaseline('VR recenter detected')" in block
    assert 'raw.q' not in block.split('resetHmdBaseline',1)[0]
    assert "resetHmdBaseline('extension loaded')" in source
    assert "resetHmdBaseline('explicit reset')" in source


def test_required_state_diagnostics_are_exposed():
    source=lua_source()
    required=('selectedHmdTranslationMode','baselineValid','baselineResetReason',
      'baselineBeamngCameraWorld','baselineTrackingHmdRaw','baselineTrackingHmdMapped',
      'baselineWorldFromTracking','currentTrackingHmdRaw','currentTrackingHmdMapped',
      'baselineRigidCandidateHmdWorld','baselineRigidLeftControllerWorld',
      'baselineRigidRightControllerWorld','beamngOnlyLeftControllerWorld',
      'beamngOnlyRightControllerWorld','trackingWorldRight','trackingWorldForward','trackingWorldUp')
    for name in required: assert 'state.'+name in source


def test_native_hmd_is_authoritative_and_predicted_getter_is_diagnostic_only():
    source=lua_source(); block=function_block('predictedOpenXRTrackingLocalPose()', 'storeBaseline')
    assert 'OpenXR.getCameraPosRotPredictedXYZXYZW' in block
    assert 'pcall(getter)' in block and 'finiteNumber(raw[i])' in block and 'qnorm({qx,qy,qz,qw})' in block
    assert 'updateRigidCandidate(cameraWorld,latest.hmd)' in source
    assert 'updateRigidCandidate(cameraWorld,predicted)' not in source
    assert 'compose(predicted' not in source


def test_red_and_purple_spheres_and_selected_blue_controllers():
    source=lua_source(); draw=function_block('drawDiagnostics(cameraWorld,candidate,selectedHmd)', 'M.onExtensionLoaded') if False else source.split('local function drawDiagnostics(cameraWorld,candidate,selectedHmd)',1)[1].split('\nfunction M.onExtensionLoaded',1)[0]
    assert 'ColorF(1,0,0,1)' in draw
    assert 'ColorF(0.65,0,1,1)' in draw
    assert 'baselineRigidTracking=purple' in draw
    assert "state[hand..'ControllerWorld']" in draw
    assert 'predictedOpenXR' not in draw


def test_controller_relative_and_stale_paths_remain_present():
    source=lua_source()
    assert 'compose(compose(hmdWorld,relative),offset)' in source
    assert '(now-latest.received)*1000>cfg.staleAfterMs' in source
    assert "out.ageMs=(now-latest.received)*1000" in source
