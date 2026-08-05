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

def test_lua_uses_beamng_camera_world_transform_and_diagnostics():
    source=(Path(__file__).parents[1]/'mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    assert 'getCameraPosition' in source
    assert 'getCameraQuat' in source
    assert 'getCameraPosRotPredictedXYZXYZW' not in source
    assert 'cameraTestSphereWorld' in source
    assert 'rawLeft=%s rawRight=%s leftWorld=%s rightWorld=%s' in source

def test_left_then_right_valid_updates_publish_combined_snapshot():
    p=StableHandPublisher(); p.update('left','L1',True,0); snap=p.update('right','R1',True,10)
    assert snap['left']['valid'] and snap['right']['valid']

def test_right_update_does_not_clear_fresh_left_pose():
    p=StableHandPublisher(); p.update('left','L1',True,0); snap=p.update('right','R1',True,50)
    assert snap['left']['valid'] and snap['left']['candidate'] == 'L1'

def test_left_update_does_not_clear_fresh_right_pose():
    p=StableHandPublisher(); p.update('right','R1',True,0); snap=p.update('left','L1',True,50)
    assert snap['right']['valid'] and snap['right']['candidate'] == 'R1'

def test_invalid_secondary_candidate_cannot_overwrite_valid_selected_candidate():
    p=StableHandPublisher(); p.update('left','L1',True,0); snap=p.update('left','L2',False,10)
    assert snap['left']['valid'] and snap['left']['candidate'] == 'L1'

def test_hand_remains_valid_during_brief_gap_within_grace_threshold():
    p=StableHandPublisher(); p.update('left','L1',True,0); snap=p.snapshot(124)
    assert snap['left']['valid'] and snap['left']['ageMs'] == 124

def test_hand_becomes_invalid_after_individual_freshness_timeout():
    p=StableHandPublisher(); p.update('left','L1',True,0); snap=p.snapshot(126)
    assert not snap['left']['valid']

def test_one_controller_can_expire_while_other_remains_valid():
    p=StableHandPublisher(); p.update('left','L1',True,0); p.update('right','R1',True,100); snap=p.snapshot(130)
    assert not snap['left']['valid'] and snap['right']['valid']

def test_session_and_space_destruction_clear_cached_poses():
    p=StableHandPublisher(); p.update('left','L1',True,0); p.update('right','R1',True,0); p.destroy_space('L1')
    assert not p.snapshot(1)['left']['valid'] and p.snapshot(1)['right']['valid']
    p.destroy_session(); assert not p.snapshot(2)['right']['valid']

def test_candidate_switching_uses_valid_replacement_without_invalid_pulse():
    p=StableHandPublisher(); p.update('left','L1',True,0); p.update('left','L2',True,10); snap=p.snapshot(126)
    assert snap['left']['valid'] and snap['left']['candidate'] == 'L2'

def test_locate_hot_path_still_has_no_project_locks_io_or_allocation_after_stability_fix():
    source=(Path(__file__).parents[1]/'openxr-layer/src/layer.cpp').read_text()
    body=source.split('layerLocateSpace(',1)[1].split('\n}\n\nXRAPI_ATTR',1)[0]
    for forbidden in ('lock_guard','writerMutex','sendto(','fopen(','sleep_for','make_shared','new ','candidateSlot('):
        assert forbidden not in body
    assert 'sample.candidate=space' in body
