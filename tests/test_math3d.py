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
    qmul,
    quaternion_inverse,
    hmd_translation_candidates,
    fixed_base_world_delta,
    fixed_world_from_base,
    map_openxr_orientation,
    map_openxr_position,
    qrotate,
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



from beamng_vr_poses.math3d import (
    baseline_rigid_controller_world,
    baseline_rigid_hmd_world,
    baseline_world_from_tracking,
    map_openxr_pose,
)


def rigid(camera, baseline_hmd, current_hmd):
    fixed = baseline_world_from_tracking(camera, baseline_hmd)
    return fixed, baseline_rigid_hmd_world(fixed, current_hmd)


def test_complete_rigid_transform_inverse_round_trip_includes_translation():
    pose = Pose((3., -4., 5.), qnorm((.2, -.3, .1, .8)))
    result = compose(pose, inverse(pose))
    close(result.position, (0., 0., 0.)); close(result.orientation, I)
    assert inverse(pose).position != tuple(-v for v in pose.position)


def test_identity_baseline_and_zero_movement_reproduce_camera_exactly():
    camera = Pose((0., 0., 0.), I); hmd = Pose((0., 0., 0.), I)
    fixed, current = rigid(camera, hmd, hmd)
    assert fixed == camera; close(current.position, camera.position); close(current.orientation, camera.orientation)


def test_nonzero_tracking_baseline_and_nonzero_world_position_cancel_exactly():
    camera = Pose((-715., 106., 119.), I)
    hmd = Pose((2., 1.7, -3.), I)
    _, current = rigid(camera, hmd, hmd)
    close(current.position, camera.position); close(current.orientation, camera.orientation)


def test_nonidentity_baseline_hmd_orientation_cancels_exactly():
    camera = Pose((10., 20., 30.), qnorm((0., 0., .3, .9)))
    hmd = Pose((2., 1.7, -3.), qnorm((0., .4, 0., .8)))
    _, current = rigid(camera, hmd, hmd)
    close(current.position, camera.position); close(current.orientation, camera.orientation)


@pytest.mark.parametrize('degrees', (0, 90, 180, 270))
def test_world_yaw_determines_fixed_tracking_axes(degrees):
    yaw = (0., 0., sin(degrees*pi/360), cos(degrees*pi/360))
    camera = Pose((100., -50., 8.), yaw); base = Pose((0., 0., 0.), I)
    _, moved = rigid(camera, base, Pose((1., 0., 0.), I))
    expected = compose(camera, Pose((1., 0., 0.), I))
    close(moved.position, expected.position)


def test_tracking_translation_meaning_is_fixed_not_rotated_by_current_head_yaw():
    camera = Pose((10., 20., 30.), qnorm((0., 0., .2, .98))); base = Pose((0., 0., 0.), I)
    fixed = baseline_world_from_tracking(camera, base)
    positions=[]
    for degrees in (0, 90, 180, 270):
        raw_yaw=(0., sin(degrees*pi/360), 0., cos(degrees*pi/360))
        positions.append(baseline_rigid_hmd_world(fixed, Pose((.4, 0., 0.), raw_yaw)).position)
    for position in positions[1:]: close(position, positions[0])


def test_current_hmd_yaw_changes_orientation_without_changing_translation_axes():
    fixed = baseline_world_from_tracking(Pose((0., 0., 0.), I), Pose((0., 0., 0.), I))
    level = baseline_rigid_hmd_world(fixed, Pose((0., 0., -1.), I))
    turned = baseline_rigid_hmd_world(fixed, Pose((0., 0., -1.), (0., sin(pi/4), 0., cos(pi/4))))
    close(level.position, turned.position)
    assert turned.orientation != pytest.approx(level.orientation)


def test_left_and_right_controllers_remain_independent():
    hmd = Pose((5., 6., 7.), I); offset = Pose((0., 0., 0.), I)
    left = baseline_rigid_controller_world(hmd, Pose((-.4, 1., 0.), I), offset)
    right = baseline_rigid_controller_world(hmd, Pose((.4, 1., 0.), I), offset)
    assert left.position != right.position
    close(left.position, (4.6, 7., 7.)); close(right.position, (5.4, 7., 7.))


def test_openxr_position_and_orientation_share_one_basis_mapping():
    yaw_about_openxr_up = (0., sin(pi/4), 0., cos(pi/4))
    mapped = map_openxr_pose(Pose((0., 1., -2.), yaw_about_openxr_up))
    close(mapped.position, (0., 2., 1.))
    # OpenXR up maps to BeamNG +Z, so yaw maps to a +Z-axis quaternion.
    assert abs(mapped.orientation[2]) == pytest.approx(sin(pi/4))
    assert abs(mapped.orientation[0]) < 1e-6 and abs(mapped.orientation[1]) < 1e-6


def test_beamng_only_is_the_only_default_mode():
    from beamng_vr_poses.math3d import HMD_TRANSLATION_MODES
    assert HMD_TRANSLATION_MODES == ('beamngOnly', 'baselineRigidTracking')
