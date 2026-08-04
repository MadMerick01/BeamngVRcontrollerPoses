import socket
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
    assert 'return d?d->gipa(i,n,f):XR_ERROR_HANDLE_INVALID' in source
    assert 'auto r=d->locateSpace(space,base,time,loc)' in source
    assert 'd->locateSpace(view,base,time,&h)' in source
