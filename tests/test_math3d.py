from math import cos, pi, sin
import pytest
from beamng_vr_poses.math3d import (
    Pose,
    actual_hmd_world,
    compose,
    controller_world,
    inverse,
    quaternion_inverse,
)

I=(0.,0.,0.,1.)
def close(a,b): assert all(abs(x-y)<1e-6 for x,y in zip(a,b))

def test_identity_hmd_preserves_controller():
    c=Pose((.2,1.1,-.4),I); assert controller_world(Pose((0,0,0),I),Pose((0,0,0),I),c)==c

def test_identity_composition_preserves_pose_and_xyzw_order():
    child=Pose((1.,2.,3.),(.1,.2,.3,.9))
    result=compose(Pose((0.,0.,0.),I),child)
    close(result.position,child.position)
    # qnorm preserves the OpenXR component order: x, y, z, w.
    assert result.orientation[0] > 0 and result.orientation[1] > result.orientation[0]
    assert result.orientation[2] > result.orientation[1] and result.orientation[3] > result.orientation[2]

def test_translation_composition_adds_parent_and_child():
    result=compose(Pose((10.,-2.,5.),I),Pose((1.,3.,-4.),I))
    close(result.position,(11.,1.,1.));close(result.orientation,I)

def test_parent_quarter_turn_rotates_child_translation():
    qz=(0.,0.,sin(pi/4),cos(pi/4))
    result=compose(Pose((0.,0.,0.),qz),Pose((1.,0.,0.),I))
    close(result.position,(0.,1.,0.));close(result.orientation,qz)

def test_shared_tracking_translation_cancels():
    h=Pose((10,2,3),I); c=Pose((10.3,1.8,2.5),I)
    close(controller_world(Pose((100,20,5),I),h,c).position,(100.3,19.8,4.5))

def test_rotated_hmd_relative_pose_and_world_rotation():
    qz=(0,0,sin(pi/4),cos(pi/4)); h=Pose((0,0,0),qz); c=compose(h,Pose((1,0,0),I))
    world=controller_world(Pose((5,6,7),qz),h,c); close(world.position,(5,7,7)); close(world.orientation,qz)

def test_inverse_roundtrip():
    p=Pose((2,-3,4),(0,sin(pi/8),0,cos(pi/8))); x=compose(inverse(p),p)
    close(x.position,(0,0,0)); close(x.orientation,I)

def test_nonzero_beamng_camera_world_position_is_parent_origin():
    camera=Pose((-715.3673609,106.5844518,119.8104916),I)
    controller_relative=Pose((0.48,1.10,-0.97),I)
    world=compose(camera,controller_relative)
    close(world.position,(-714.8873609,107.6844518,118.8404916))


def test_nonidentity_beamng_camera_world_rotation_affects_controller():
    qz=(0.,0.,sin(pi/4),cos(pi/4))
    camera=Pose((-715.,106.,119.),qz)
    world=compose(camera,Pose((1.,0.,0.),I))
    close(world.position,(-715.,107.,119.))
    close(world.orientation,qz)


def test_controller_relative_to_camera_composition_without_external_hmd_twice():
    camera=Pose((-715.,106.,119.),I)
    controller_relative=Pose((0.5,1.0,-1.0),I)
    world=compose(camera,controller_relative)
    close(world.position,(-714.5,107.,118.))


def test_left_and_right_independent_movement_near_camera_origin():
    camera=Pose((-715.,106.,119.),I)
    left=compose(camera,Pose((-0.4,1.0,-0.9),I))
    right=compose(camera,Pose((0.4,1.2,-0.8),I))
    assert left.position != right.position
    close(left.position,(-715.4,107.,118.1))
    close(right.position,(-714.6,107.2,118.2))


def test_diagnostic_sphere_position_near_nonzero_camera_origin():
    camera=Pose((-715.,106.,119.),I)
    sphere=compose(camera,Pose((0.,1.,0.),I))
    close(sphere.position,(-715.,107.,119.))


def camera_world(position, raw_world_to_camera):
    """Model the conversion performed at the BeamNG Lua API boundary."""
    return Pose(position, quaternion_inverse(raw_world_to_camera))


def test_identity_quaternion_remains_identity_after_inversion():
    close(quaternion_inverse(I), I)


def test_raw_world_to_camera_positive_pitch_becomes_camera_to_world_pitch():
    raw_view_pitch = (sin(pi / 8), 0., 0., cos(pi / 8))
    close(camera_world((0., 0., 0.), raw_view_pitch).orientation,
          (-sin(pi / 8), 0., 0., cos(pi / 8)))


def test_looking_up_rotates_local_forward_offset_upward():
    # BeamNG local forward is +Y and up is +Z.  Its view quaternion has the
    # opposite sign from the camera-to-world pitch consumed by compose().
    raw_view_pitch_up = (-sin(pi / 8), 0., 0., cos(pi / 8))
    sphere = compose(camera_world((0., 0., 0.), raw_view_pitch_up),
                     Pose((0., 1., 0.), I))
    assert sphere.position[1] > 0.
    assert sphere.position[2] > 0.


def test_looking_down_rotates_local_forward_offset_downward():
    raw_view_pitch_down = (sin(pi / 8), 0., 0., cos(pi / 8))
    sphere = compose(camera_world((0., 0., 0.), raw_view_pitch_down),
                     Pose((0., 1., 0.), I))
    assert sphere.position[1] > 0.
    assert sphere.position[2] < 0.


def test_left_and_right_yaw_rotate_forward_with_gaze():
    raw_view_left = (0., 0., -sin(pi / 8), cos(pi / 8))
    raw_view_right = (0., 0., sin(pi / 8), cos(pi / 8))
    left = compose(camera_world((0., 0., 0.), raw_view_left), Pose((0., 1., 0.), I))
    right = compose(camera_world((0., 0., 0.), raw_view_right), Pose((0., 1., 0.), I))
    assert left.position[0] < 0. < right.position[0]
    assert left.position[1] > 0. and right.position[1] > 0.


def test_stationary_world_controller_survives_inverse_head_relative_change():
    controller_world_pose = Pose((0., 2., 0.), I)
    level_camera = camera_world((0., 0., 0.), I)
    raw_view_yaw = (0., 0., sin(pi / 8), cos(pi / 8))
    turned_camera = camera_world((0., 0., 0.), raw_view_yaw)
    level_relative = compose(inverse(level_camera), controller_world_pose)
    turned_relative = compose(inverse(turned_camera), controller_world_pose)
    close(compose(level_camera, level_relative).position, controller_world_pose.position)
    close(compose(turned_camera, turned_relative).position, controller_world_pose.position)


def test_quaternion_correction_does_not_change_nonzero_camera_position():
    position = (-715.3673609, 106.5844518, 119.8104916)
    raw_view_yaw = (0., 0., sin(pi / 8), cos(pi / 8))
    close(camera_world(position, raw_view_yaw).position, position)


def test_hand_calibration_offsets_remain_after_corrected_camera_transform():
    raw_view_yaw = (0., 0., -sin(pi / 4), cos(pi / 4))
    camera = camera_world((10., 20., 30.), raw_view_yaw)
    left_relative = Pose((-.4, 1., 0.), I)
    right_relative = Pose((.4, 1., 0.), I)
    left_offset = Pose((-.1, 0., 0.), I)
    right_offset = Pose((.1, 0., 0.), I)
    left = compose(camera, compose(left_relative, left_offset))
    right = compose(camera, compose(right_relative, right_offset))
    close(left.position, (9., 19.5, 30.))
    close(right.position, (9., 20.5, 30.))


def test_quaternion_inverse_round_trip():
    q = (.2, -.3, .4, .8)
    close(quaternion_inverse(quaternion_inverse(q)),
          tuple(v / sum(x*x for x in q)**.5 for v in q))


@pytest.mark.parametrize(('translation', 'expected'), [
    ((0.4, 0., 0.), (10.4, 20., 30.)),       # OpenXR right -> BeamNG right
    ((0., 0.3, 0.), (10., 20., 30.3)),       # OpenXR up -> BeamNG up
    ((0., 0., -0.5), (10., 20.5, 30.)),      # OpenXR forward -> BeamNG forward
])
def test_hmd_translation_delta_mapping(translation, expected):
    baseline=(2., 1.7, -3.)
    hmd=Pose(tuple(baseline[i]+translation[i] for i in range(3)), I)
    close(actual_hmd_world(Pose((10.,20.,30.),I),hmd,baseline).position, expected)


def test_recenter_reestablishes_baseline_without_world_jump():
    anchor=Pose((100.,200.,300.),I)
    recentered=Pose((8.,1.7,-4.),I)
    close(actual_hmd_world(anchor,recentered,recentered.position).position,anchor.position)


def test_hmd_delta_uses_nonzero_world_anchor_and_nonidentity_rotation():
    qz=(0.,0.,sin(pi/4),cos(pi/4))
    actual=actual_hmd_world(Pose((-715.,106.,119.),qz),Pose((1.,0.,0.),I),(0.,0.,0.))
    close(actual.position,(-715.,107.,119.))
    close(actual.orientation,qz)
