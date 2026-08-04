from math import cos, pi, sin
from beamng_vr_poses.math3d import Pose, compose, controller_world, inverse

I=(0.,0.,0.,1.)
def close(a,b): assert all(abs(x-y)<1e-6 for x,y in zip(a,b))

def test_identity_hmd_preserves_controller():
    c=Pose((.2,1.1,-.4),I); assert controller_world(Pose((0,0,0),I),Pose((0,0,0),I),c)==c

def test_shared_tracking_translation_cancels():
    h=Pose((10,2,3),I); c=Pose((10.3,1.8,2.5),I)
    close(controller_world(Pose((100,20,5),I),h,c).position,(100.3,19.8,4.5))

def test_rotated_hmd_relative_pose_and_world_rotation():
    qz=(0,0,sin(pi/4),cos(pi/4)); h=Pose((0,0,0),qz); c=compose(h,Pose((1,0,0),I))
    world=controller_world(Pose((5,6,7),qz),h,c); close(world.position,(5,7,7)); close(world.orientation,qz)

def test_inverse_roundtrip():
    p=Pose((2,-3,4),(0,sin(pi/8),0,cos(pi/8))); x=compose(inverse(p),p)
    close(x.position,(0,0,0)); close(x.orientation,I)
