import json
import math
from pathlib import Path


ROOT = Path(__file__).parents[1]
LUA = ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua"
SETTINGS = ROOT / "mod/settings/beamngVRControllerPoses.json"


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qinv(q):
    return (-q[0], -q[1], -q[2], q[3])


def qrot(q, p):
    r = qmul(qmul(q, (*p, 0)), qinv(q))
    return r[:3]


def compose(a, b):
    rotated = qrot(a[1], b[0])
    return (tuple(a[0][i] + rotated[i] for i in range(3)), qmul(a[1], b[1]))


def inverse(pose):
    qi = qinv(pose[1])
    return (qrot(qi, tuple(-v for v in pose[0])), qi)


I = ((0, 0, 0), (0, 0, 0, 1))


def close(a, b, tol=1e-9):
    return all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a[0], b[0])) and abs(
        sum(x * y for x, y in zip(a[1], b[1]))
    ) > 1 - tol


def step(previous_camera, camera, previous_orange, orange, attachment, tracking):
    camera_delta = compose(camera, inverse(previous_camera))
    physical_delta = compose(orange, inverse(previous_orange))
    artificial_delta = compose(camera_delta, inverse(physical_delta))
    attachment = compose(artificial_delta, attachment)
    return compose(attachment, tracking), attachment, artificial_delta


def yaw(degrees):
    radians = math.radians(degrees) / 2
    return (0, 0, math.sin(radians), math.cos(radians))


def test_initialization_and_recenter_are_exactly_orange():
    tracking = ((0.2, -0.1, 1.7), yaw(15))
    orange = ((12, 7, 2), yaw(40))
    attachment = compose(orange, inverse(tracking))
    assert close(compose(attachment, tracking), orange)
    recentered = ((0, 0, 1.6), yaw(-10))
    reset_attachment = compose(orange, inverse(recentered))
    assert close(compose(reset_attachment, recentered), orange)


def test_pure_physical_translation_and_rotation_have_identity_artificial_delta():
    previous = ((1, 2, 3), yaw(5))
    for current in (((1.3, 1.8, 3.1), yaw(5)), ((1, 2, 3), yaw(125))):
        _, _, delta = step(previous, current, previous, current, I, I)
        assert close(delta, I)


def test_stick_translation_moves_dark_blue_once_in_world_coordinates():
    tracking = ((0, 0, 0), I[1])
    dark, _, artificial = step(I, ((4, -3, 0.5), I[1]), I, I, I, tracking)
    assert close(artificial, ((4, -3, 0.5), I[1]))
    assert close(dark, artificial)


def test_stick_rotation_is_complete_pivot_aware_rigid_transform():
    # Rotation about pivot (10, 0, 0): T(p) R T(-p), including translation.
    pivot = ((10, 0, 0), I[1])
    rigid_rotation = compose(compose(pivot, ((0, 0, 0), yaw(90))), inverse(pivot))
    point = ((11, 0, 0), I[1])
    dark, _, delta = step(I, rigid_rotation, I, I, I, point)
    assert close(delta, rigid_rotation)
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(dark[0], (10, 1, 0)))
    # No unrelated displacement: distance to the active pivot is preserved.
    assert math.isclose(math.dist(dark[0], pivot[0]), math.dist(point[0], pivot[0]))


def test_combined_motion_applies_physical_and_artificial_once():
    physical = ((0.25, 0, 0), yaw(20))
    artificial = ((3, 2, 0), yaw(35))
    camera = compose(artificial, physical)
    dark, _, recovered = step(I, camera, I, physical, I, physical)
    assert close(recovered, artificial)
    assert close(dark, compose(artificial, physical))


def test_quaternion_order_is_noncommutative_and_sign_invariant():
    s = math.sqrt(0.5)
    x, z = (s, 0, 0, s), (0, 0, s, s)
    assert not close(((0, 0, 0), qmul(x, z)), ((0, 0, 0), qmul(z, x)))
    assert close(((0, 0, 0), x), ((0, 0, 0), tuple(-v for v in x)))


def test_pose_inverse_rotates_negative_translation():
    pose = ((2, 0, 0), yaw(90))
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(inverse(pose)[0], (0, 2, 0)))
    assert close(compose(pose, inverse(pose)), I)


def test_lua_uses_full_pose_composition_and_exposes_required_state():
    text = LUA.read_text()
    block = text.split("local function updateDarkBlue", 1)[1].split("local function actualHmdWorld", 1)[0]
    assert "compose(cameraWorld,inversePose(previousBeamngCameraWorld))" in block
    assert "compose(orangeWorld,inversePose(previousOrangeHmdWorld))" in block
    assert "compose(beamngDelta,inversePose(orangeDelta))" in block
    assert "compose(artificialDelta,darkBlueWorldFromTracking)" in block
    assert "compose(darkBlueWorldFromTracking,trackingHmd)" in block
    assert "cameraWorld.p[1]-" not in block and "orangeWorld.p[1]-" not in block
    for field in (
        "darkBlueHmdWorld", "darkBlueDiagnosticSphereWorld", "darkBlueWorldFromTracking",
        "previousBeamngCameraWorld", "currentBeamngCameraWorld", "beamngCameraDelta",
        "previousOrangeHmdWorld", "currentOrangeHmdWorld", "orangePhysicalDelta",
        "artificialCameraDelta", "artificialTranslationMagnitude", "artificialRotationDegrees",
        "artificialDeltaConsideredIdentity", "darkBlueLeftControllerWorld",
        "darkBlueRightControllerWorld", "darkBlueResetReason", "darkBlueResetCount",
        "darkBlueValid", "darkBlueOrangePositionDifference", "darkBlueOrangeAngularDifference",
    ):
        assert f"state.{field}" in text


def test_mode_is_additive_and_packaged_default_orange_violet_behavior_is_preserved():
    text = LUA.read_text()
    cfg = json.loads(SETTINGS.read_text())
    assert "baselineRigidRebasedArtificialCamera=true" in text
    assert cfg["hmdTranslationMode"] == "beamngOnly"
    assert cfg["diagnosticVisualProfile"] == "orangeVioletControllers"
    assert "baselineRigidPositionBeamngRotationRebased=rebasedHybridCandidate" in text
    assert "geluaNativeCameraComposition=nativeCandidate" in text
    assert "OpenXR.centerNow" not in text


def test_clean_visuals_add_only_a_larger_darker_headset_and_do_not_draw_hypothetical_hands():
    text = LUA.read_text()
    cfg = json.loads(SETTINGS.read_text())
    assert cfg["darkBlueHeadsetSphereDiameter"] > cfg["sphereDiameter"]
    assert "ColorF(0.02,0.08,0.45,1.0)" in text
    assert "ColorF(0.75,0.9,1,1)" in text
    draw = text.split("local function drawDiagnostics", 1)[1].split("function M.onExtensionLoaded", 1)[0]
    assert "drawSphere(vec3(state.darkBlueLeftControllerWorld" not in draw
    assert "drawSphere(vec3(state.darkBlueRightControllerWorld" not in draw


def test_native_layer_and_protocol_are_outside_the_additive_change():
    # The implementation remains exclusively in GE Lua, settings, and tests.
    assert "xrLocateSpace" not in LUA.read_text()
