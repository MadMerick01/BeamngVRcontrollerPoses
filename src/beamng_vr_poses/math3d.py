"""Rigid-transform math. Quaternions are (x, y, z, w), composed parent * child."""
from dataclasses import dataclass
from math import sqrt


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


def actual_hmd_world(camera_anchor: Pose, hmd_in_base: Pose, baseline):
    """Supplement BeamNG's anchor with baseline-relative room-scale motion."""
    raw_delta = tuple(hmd_in_base.position[i] - baseline[i] for i in range(3))
    mapped_delta = map_openxr_position(raw_delta)
    world_delta = qrotate(camera_anchor.orientation, mapped_delta)
    return Pose(tuple(camera_anchor.position[i] + world_delta[i] for i in range(3)),
                camera_anchor.orientation)
