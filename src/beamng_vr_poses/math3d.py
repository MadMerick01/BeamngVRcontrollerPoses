"""Rigid-transform math. Quaternions are (x, y, z, w), composed parent * child."""
from dataclasses import dataclass
from math import acos, degrees, isfinite, sqrt


@dataclass(frozen=True)
class Pose:
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]


def qmul(a, b):
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return (aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw,
            aw*bw - ax*bx - ay*by - az*bz)


def qnorm(q):
    n = sqrt(sum(v*v for v in q))
    if n == 0: raise ValueError("zero quaternion")
    return tuple(v/n for v in q)


def quaternion_inverse(q):
    """Return the inverse of a rotation quaternion in (x, y, z, w) order."""
    x, y, z, w = qnorm(q)
    return (-x, -y, -z, w)


def qrotate(q, v):
    q = qnorm(q); r = qmul(qmul(q, (*v, 0.0)), quaternion_inverse(q))
    return r[:3]


def axis_tripod_endpoints(position, orientation, axis_length):
    """Return positive BeamNG X/Y/Z endpoints without modifying the centre."""
    centre = tuple(position)
    basis = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return tuple(
        tuple(centre[i] + qrotate(orientation, axis)[i] * axis_length
              for i in range(3))
        for axis in basis
    )


def compose(parent: Pose, child: Pose) -> Pose:
    rp = qrotate(parent.orientation, child.position)
    return Pose(tuple(parent.position[i] + rp[i] for i in range(3)),
                qnorm(qmul(parent.orientation, child.orientation)))


def inverse(p: Pose) -> Pose:
    qi = quaternion_inverse(p.orientation)
    return Pose(qrotate(qi, tuple(-v for v in p.position)), qi)


def controller_world(beamng_hmd: Pose, external_hmd: Pose, controller: Pose) -> Pose:
    """beamngHmd * inverse(externalHmd) * externalController."""
    return compose(beamng_hmd, compose(inverse(external_hmd), controller))


def map_openxr_position(position):
    """Map OpenXR metres (x, y, z) to BeamNG axes (x, -z, y)."""
    x, y, z = position
    return (x, -z, y)


OPENXR_TO_BEAMNG_BASIS = (sqrt(0.5), 0.0, 0.0, sqrt(0.5))


def map_openxr_orientation(orientation):
    """Change an OpenXR orientation into the (x, -z, y) BeamNG basis."""
    basis = qnorm(OPENXR_TO_BEAMNG_BASIS)
    return qnorm(qmul(qmul(basis, qnorm(orientation)), quaternion_inverse(basis)))


def map_openxr_pose(pose: Pose) -> Pose:
    """Map a complete OpenXR pose with one shared change of basis."""
    return Pose(map_openxr_position(pose.position),
                map_openxr_orientation(pose.orientation))


def baseline_world_from_tracking(beamng_camera_world: Pose,
                                 baseline_tracking_hmd: Pose) -> Pose:
    """Return the complete, fixed BeamNG-world-from-tracking transform."""
    return compose(beamng_camera_world,
                   inverse(map_openxr_pose(baseline_tracking_hmd)))


def baseline_rigid_hmd_world(world_from_tracking: Pose,
                             current_tracking_hmd: Pose) -> Pose:
    return compose(world_from_tracking, map_openxr_pose(current_tracking_hmd))


def baseline_rigid_controller_world(candidate_hmd_world: Pose,
                                    controller_relative_to_hmd: Pose,
                                    calibration_offset: Pose) -> Pose:
    return compose(candidate_hmd_world,
                   compose(controller_relative_to_hmd, calibration_offset))


def target_world_from_tracking_orientation(camera_world_orientation,
                                           mapped_tracking_hmd_orientation):
    """Alignment which maps the current tracking HMD rotation to the camera."""
    return qnorm(qmul(qnorm(camera_world_orientation),
                      quaternion_inverse(mapped_tracking_hmd_orientation)))


def quaternion_angular_difference_degrees(a, b):
    """Shortest rotation between normalized XYZW quaternions (q and -q agree)."""
    dot = abs(sum(x*y for x, y in zip(qnorm(a), qnorm(b))))
    return degrees(2 * acos(min(1.0, max(-1.0, dot))))


def pivot_preserving_world_from_tracking_rebase(world_from_tracking: Pose,
                                                mapped_tracking_hmd: Pose,
                                                new_orientation) -> tuple[Pose, Pose, Pose]:
    """Rotate tracking axes around the current HMD without moving the pivot."""
    before = compose(world_from_tracking, mapped_tracking_hmd)
    orientation = qnorm(new_orientation)
    rotated = qrotate(orientation, mapped_tracking_hmd.position)
    position = tuple(before.position[i] - rotated[i] for i in range(3))
    rebased_transform = Pose(position, orientation)
    after = compose(rebased_transform, mapped_tracking_hmd)
    return rebased_transform, before, after


def anchor_physical_offset_to_current_camera(current_camera_position,
                                             baseline_reference_hmd_position,
                                             current_reference_hmd_position):
    """Anchor an absolute PR #28 physical displacement to today's camera pose.

    No previous-frame camera position is accepted: both outputs are calculated
    directly from the three current/baseline absolute positions.
    """
    camera = tuple(float(value) for value in current_camera_position)
    baseline = tuple(float(value) for value in baseline_reference_hmd_position)
    current = tuple(float(value) for value in current_reference_hmd_position)
    if (len(camera) != 3 or len(baseline) != 3 or len(current) != 3 or
            not all(isfinite(value) for value in camera + baseline + current)):
        raise ValueError("camera and reference HMD positions must be finite vec3 values")
    physical_offset = tuple(current[i] - baseline[i] for i in range(3))
    final_position = tuple(camera[i] + physical_offset[i] for i in range(3))
    return physical_offset, final_position


def fixed_world_from_base(world_from_hmd, base_from_hmd):
    """Derive the fixed BeamNG world-from-OpenXR-base rotation at baseline."""
    mapped = map_openxr_orientation(base_from_hmd)
    return qnorm(qmul(qnorm(world_from_hmd), quaternion_inverse(mapped)))


def fixed_base_world_delta(world_from_base, current_position, baseline_position):
    raw = tuple(current_position[i] - baseline_position[i] for i in range(3))
    return qrotate(world_from_base, map_openxr_position(raw))


def actual_hmd_world(camera_anchor: Pose, hmd_in_base: Pose, baseline):
    """Supplement BeamNG's anchor with baseline-relative room-scale motion."""
    raw_delta = tuple(hmd_in_base.position[i] - baseline[i] for i in range(3))
    mapped_delta = map_openxr_position(raw_delta)
    world_delta = qrotate(camera_anchor.orientation, mapped_delta)
    return Pose(tuple(camera_anchor.position[i] + world_delta[i] for i in range(3)),
                camera_anchor.orientation)


HMD_TRANSLATION_MODES = (
    "beamngOnly",
    "beamngPlusHmdDelta",
    "beamngMinusHmdDelta",
    "beamngFixedBaseHmdDelta",
)


def hmd_translation_candidates(camera_anchor: Pose, hmd_in_base: Pose | None, baseline,
                               world_from_base=None):
    """Return independently calculated Lua diagnostic candidates."""
    raw_delta = ((0.0, 0.0, 0.0) if hmd_in_base is None or baseline is None else
                 tuple(hmd_in_base.position[i] - baseline[i] for i in range(3)))
    world_delta = qrotate(camera_anchor.orientation, map_openxr_position(raw_delta))
    fixed_delta = ((0.0, 0.0, 0.0) if world_from_base is None else
                   qrotate(world_from_base, map_openxr_position(raw_delta)))
    return {
        "beamngOnly": Pose(tuple(camera_anchor.position[i] for i in range(3)),
                           camera_anchor.orientation),
        "beamngPlusHmdDelta": Pose(
            tuple(camera_anchor.position[i] + world_delta[i] for i in range(3)),
            camera_anchor.orientation),
        "beamngMinusHmdDelta": Pose(
            tuple(camera_anchor.position[i] - world_delta[i] for i in range(3)),
            camera_anchor.orientation),
        "beamngFixedBaseHmdDelta": Pose(
            tuple(camera_anchor.position[i] + fixed_delta[i] for i in range(3)),
            camera_anchor.orientation),
    }


def select_hmd_translation(camera_anchor: Pose, hmd_in_base: Pose | None, baseline,
                           mode: str, world_from_base=None) -> Pose:
    if mode not in HMD_TRANSLATION_MODES:
        raise ValueError(f"invalid HMD translation mode: {mode}")
    return hmd_translation_candidates(camera_anchor, hmd_in_base, baseline,
                                      world_from_base)[mode]
