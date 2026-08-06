from math import cos, pi, sin
import pytest
from beamng_vr_poses.math3d import (
    Pose,
    actual_hmd_world,
    axis_tripod_endpoints,
    compose,
    controller_world,
    inverse,
    qnorm,
    quaternion_inverse,
    hmd_translation_candidates,
    select_hmd_translation,
)

I=(0.,0.,0.,1.)
def close(a,b): assert all(abs(x-y)<1e-6 for x,y in zip(a,b))


def test_identity_tripod_points_right_forward_and_up_and_preserves_centre():
    centre = [10., 20., 30.]
    original = list(centre)
    x, y, z = axis_tripod_endpoints(centre, I, .25)
    close(x, (10.25, 20., 30.))
    close(y, (10., 20.25, 30.))
    close(z, (10., 20., 30.25))
    assert centre == original


def test_yaw_and_pitch_rotate_tripod_axes_in_xyzw_order():
    yaw = (0., 0., sin(pi / 4), cos(pi / 4))
    x, y, z = axis_tripod_endpoints((0., 0., 0.), yaw, 1.)
    close(x, (0., 1., 0.)); close(y, (-1., 0., 0.)); close(z, (0., 0., 1.))
    pitch = (sin(pi / 4), 0., 0., cos(pi / 4))
    x, y, z = axis_tripod_endpoints((0., 0., 0.), pitch, 1.)
    close(x, (1., 0., 0.)); close(y, (0., 0., 1.)); close(z, (0., -1., 0.))


@pytest.mark.parametrize('length', [.025, .25, 2.])
def test_every_tripod_axis_preserves_configured_length(length):
    centre = (4., -2., 9.)
    q = qnorm((.2, -.3, .4, .8))
    for endpoint in axis_tripod_endpoints(centre, q, length):
        assert sum((endpoint[i] - centre[i]) ** 2 for i in range(3)) ** .5 == pytest.approx(length)


def test_controller_tripods_use_independent_controller_orientations_not_camera():
    camera = (0., 0., 0., 1.)
    left_q = (0., 0., sin(pi / 4), cos(pi / 4))
    right_q = (sin(pi / 4), 0., 0., cos(pi / 4))
    left = axis_tripod_endpoints((-1., 0., 0.), left_q, 1.)
    right = axis_tripod_endpoints((1., 0., 0.), right_q, 1.)
    camera_axes = axis_tripod_endpoints((-1., 0., 0.), camera, 1.)
    assert left != camera_axes
    assert left != right
    close(left[0], (-1., 1., 0.))
    close(right[1], (1., 0., 1.))

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


def test_zero_hmd_delta_makes_all_translation_modes_identical():
    camera = Pose((10., 20., 30.), I)
    hmd = Pose((2., 1.7, -3.), I)
    candidates = hmd_translation_candidates(camera, hmd, hmd.position)
    assert set(candidates) == {"beamngOnly", "beamngPlusHmdDelta", "beamngMinusHmdDelta"}
    for candidate in candidates.values():
        close(candidate.position, camera.position)


@pytest.mark.parametrize(('delta', 'mapped'), [
    ((.4, 0., 0.), (.4, 0., 0.)),
    ((0., .3, 0.), (0., 0., .3)),
    ((0., 0., -.5), (0., .5, 0.)),
    ((0., 0., .5), (0., -.5, 0.)),
])
def test_plus_and_minus_modes_for_room_scale_axes(delta, mapped):
    origin = (-715., 106., 119.)
    hmd = Pose(delta, I)
    candidates = hmd_translation_candidates(Pose(origin, I), hmd, (0., 0., 0.))
    close(candidates['beamngOnly'].position, origin)
    close(candidates['beamngPlusHmdDelta'].position,
          tuple(origin[i] + mapped[i] for i in range(3)))
    close(candidates['beamngMinusHmdDelta'].position,
          tuple(origin[i] - mapped[i] for i in range(3)))


def test_translation_candidates_rotate_delta_without_rotating_world_origin():
    qz = (0., 0., sin(pi/4), cos(pi/4))
    camera = Pose((-715., 106., 119.), qz)
    candidates = hmd_translation_candidates(camera, Pose((1., 0., 0.), I), (0., 0., 0.))
    close(candidates['beamngOnly'].position, camera.position)
    close(candidates['beamngPlusHmdDelta'].position, (-715., 107., 119.))
    close(candidates['beamngMinusHmdDelta'].position, (-715., 105., 119.))


def test_each_candidate_is_an_independent_pose_calculation():
    candidates = hmd_translation_candidates(Pose((1., 2., 3.), I),
                                            Pose((.2, .3, .4), I), (0., 0., 0.))
    assert len({id(pose) for pose in candidates.values()}) == 3
    assert len({id(pose.position) for pose in candidates.values()}) == 3
    close(candidates['beamngOnly'].position, (1., 2., 3.))


def test_selection_returns_requested_mode_and_rejects_invalid_mode():
    camera = Pose((10., 20., 30.), I)
    hmd = Pose((1., 0., 0.), I)
    close(select_hmd_translation(camera, hmd, (0., 0., 0.),
                                 'beamngMinusHmdDelta').position, (9., 20., 30.))
    with pytest.raises(ValueError):
        select_hmd_translation(camera, hmd, (0., 0., 0.), 'scaledGuess')


def test_invalid_or_absent_hmd_data_uses_zero_delta_for_every_mode():
    camera = Pose((10., 20., 30.), I)
    for hmd, baseline in ((None, None), (Pose((99., 99., 99.), I), None)):
        for candidate in hmd_translation_candidates(camera, hmd, baseline).values():
            close(candidate.position, camera.position)
