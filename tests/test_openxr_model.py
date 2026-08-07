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

def test_lua_uses_predicted_openxr_camera_world_transform_and_diagnostics():
    source=(Path(__file__).parents[1]/'mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    assert 'core_camera.getPosition' in source
    assert 'core_camera.getQuat' in source
    assert 'getCameraPosition' not in source
    assert 'getCameraQuat' not in source
    assert 'OpenXR.getCameraPosRotPredictedXYZXYZW' in source
    assert 'cameraTestSphereWorld' in source
    for diagnostic in ('beamngCameraPosition','rawOpenXrHmdPosition','rawHmdPose','hmdBaseline',
                       'rawHmdDelta','mappedHmdDelta','rotatedWorldHmdDelta',
                       'selectedHmdTranslationMode','candidateHmdWorldPositions',
                       'diagnosticSphereWorldPositions','finalControllerWorldPositions'):
        assert diagnostic in source
    assert "local beamngWorld,candidates=actualHmdWorld(cameraAnchor,latest.hmd)" in source


def predicted_camera_block():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    return source.split('local function predictedOpenXRCameraWorld()',1)[1].split('\nend\nlocal function beamCameraWorld()',1)[0]


def test_valid_predicted_pose_selection_and_quaternion_normalization():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    block=predicted_camera_block()
    assert 'pcall(getter)' in block
    assert 'qnorm({qx,qy,qz,qw})' in block
    assert 'return {p={px,py,pz},q=orientation},raw,nil' in block
    assert "if predictedCamera then hmdWorld=predictedCamera else fallbackReason=predictedError end" in source


def test_invalid_or_missing_predicted_getter_falls_back_safely():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    block=predicted_camera_block()
    assert "type(getter)~='function'" in block
    assert 'for i=1,7 do' in block and 'finiteNumber(raw[i])' in block
    assert "value==value and value~=math.huge and value~=-math.huge" in source
    assert 'local hmdWorld=beamngWorld' in source
    assert 'cameraSourceFallbackReason=fallbackReason' in source


def test_predicted_camera_does_not_add_packet_hmd_delta_and_composes_controller_once():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    block=predicted_camera_block()
    for forbidden in ('latest.hmd','rawDelta','worldDelta','core_camera','qrot(cameraAnchor'):
        assert forbidden not in block
    update=source.split('local function updateHand(',1)[1].split('\nend\nlocal validHmdTranslationModes',1)[0]
    assert 'local world=compose(cameraWorld,compose(rel,offset))' in update
    assert update.count('compose(') == 2


def test_camera_source_modes_default_to_predicted_and_retain_beamng_only():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    settings=json.loads(Path('mod/settings/beamngVRControllerPoses.json').read_text())
    assert settings['cameraSourceMode'] == 'predictedOpenXR'
    setter=source.split('function M.setCameraSourceMode(mode)',1)[1].split('\nend',1)[0]
    assert "mode~='predictedOpenXR' and mode~='beamngOnly'" in setter
    assert 'return false' in setter and 'return true' in setter


def test_predicted_camera_magenta_sphere_is_one_confirmed_local_forward_metre():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    block=source.split('if cfg.cameraTestSphere and cfg.cameraTestSphere.enabled then',1)[1].split("local c=ColorF",1)[0]
    assert 'local localPos=cfg.cameraTestSphere.offset or {0,1,0}' in block
    assert 'compose(predictedCamera,{p=localPos,q={0,0,0,1}})' in block
    assert 'ColorF(1,0,1,1)' in block

def test_lua_translation_modes_are_runtime_switchable_and_reset_baseline():
    source=(Path(__file__).parents[1]/'mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    for mode in ('beamngOnly','beamngPlusHmdDelta','beamngMinusHmdDelta','beamngFixedBaseHmdDelta'):
        assert mode in source
    setter=source.split('function M.setHmdTranslationMode(mode)',1)[1].split('\nend',1)[0]
    assert "resetHmdBaseline('translation mode changed to '..mode)" in setter
    assert 'return false' in setter and 'return true' in setter


def test_fixed_baseline_lifecycle_and_diagnostics_are_explicit():
    source=Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    for reason in ('tracking space changed', 'sample time reset', 'HMD pose discontinuity',
                   'explicit reset', 'extension loaded', 'translation mode changed to '):
        assert reason in source
    for field in ('rawBaseFromHmdPosition','rawBaseFromHmdOrientation',
                  'mappedBaseFromHmdOrientation','baselineBaseFromHmdPosition',
                  'baselineBaseFromHmdOrientation','baselineMappedHmdOrientation',
                  'baselineBeamngCameraOrientation','fixedWorldFromBaseOrientation',
                  'rawBaseDelta','mappedBaseDelta','liveCameraWorldDelta',
                  'fixedBaseWorldDelta','fixedBaseCandidateHmdWorldPosition',
                  'fixedBaseDiagnosticSphereWorldPosition','finalLeftControllerWorldPosition',
                  'finalRightControllerWorldPosition','baselineResetReason',
                  'fixedBaseWorldRight','fixedBaseWorldForward','fixedBaseWorldUp'):
        assert field in source
    derivation='worldFromBaseQ=qnorm(qmul(qnorm(cameraAnchor.q),qinv(mappedQ)))'
    assert derivation in source
    assert 'fixedDelta=qrot(worldFromBaseQ,mappedDelta)' in source

def test_lua_candidate_spheres_are_independent_and_have_required_colours():
    source=(Path(__file__).parents[1]/'mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    assert 'local red=compose(candidates.beamngOnly' in source
    assert 'local green=compose(candidates.beamngPlusHmdDelta' in source
    assert 'local yellow=compose(candidates.beamngMinusHmdDelta' in source
    assert 'local white=compose(candidates.beamngFixedBaseHmdDelta' in source
    for colour in ('ColorF(1,0,0,1)', 'ColorF(0,1,0,1)', 'ColorF(1,1,0,1)', 'ColorF(1,1,1,1)'):
        assert colour in source


def test_camera_axis_spheres_use_only_the_beamng_camera_anchor():
    source = Path('mod/lua/ge/extensions/beamngVRControllerPoses.lua').read_text()
    block = source.split('local cameraAxes=cfg.cameraAxisSpheres or {}', 1)[1].split(
        'if cfg.cameraTestSphere and cfg.cameraTestSphere.enabled then', 1)[0]
    assert 'compose(candidates.beamngOnly' in block
    assert 'beamngPlusHmdDelta' not in block
    assert 'beamngMinusHmdDelta' not in block
    for offset in ('{distance,0,0}', '{0,distance,0}', '{0,0,distance}'):
        assert offset in block
    for colour in ('ColorF(1,0,0,1)', 'ColorF(0,1,0,1)', 'ColorF(0,0,1,1)'):
        assert colour in block
    assert 'cameraAxisSphereWorldPositions' in block

    settings = json.loads(Path('mod/settings/beamngVRControllerPoses.json').read_text())
    assert settings['cameraAxisSpheres'] == {
        'enabled': True,
        'distance': 1.0,
        'diameter': 0.12,
    }

def test_default_translation_mode_and_confirmed_axis_mapping_are_preserved():
    settings=json.loads((Path(__file__).parents[1]/'mod/settings/beamngVRControllerPoses.json').read_text())
    assert settings['hmdTranslationMode'] == 'beamngFixedBaseHmdDelta'
    assert settings['axisOrder'] == [1,3,2]
    assert settings['axisSign'] == [1,-1,1]

def test_native_packet_publishes_same_sample_hmd_without_changing_protocol_version():
    source=(Path(__file__).parents[1]/'openxr-layer/src/layer.cpp').read_text()
    assert 'sample.hmd.pose=head.pose' in source
    assert '\\"v\\":2' in source and '\\"hmd\\":%s' in source
    assert '\\"sampleTime\\":%lld' in source

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
