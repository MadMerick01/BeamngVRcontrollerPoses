import json
import math
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "mod/lua/ge/extensions/vrMockPistol.lua").read_text()
SETTINGS_PATH = ROOT / "mod/settings/vrMockPistol.json"


def qmul(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return (
        w1*x2+x1*w2+y1*z2-z1*y2,
        w1*y2-x1*z2+y1*w2+z1*x2,
        w1*z2+x1*y2-y1*x2+z1*w2,
        w1*w2-x1*x2-y1*y2-z1*z2,
    )


def qrot(q, p):
    result = qmul(qmul(q, (*p, 0)), (-q[0], -q[1], -q[2], q[3]))
    return result[:3]


def compose(parent, local):
    offset = qrot(parent[1], local[0])
    return (tuple(a+b for a, b in zip(parent[0], offset)), qmul(parent[1], local[1]))


def test_settings_are_valid_and_use_confirmed_local_axes():
    settings = json.loads(SETTINGS_PATH.read_text())
    assert settings["enabled"] is False
    assert settings["hand"] == "right"
    assert settings["maximumPoseAgeMs"] == 125
    assert settings["colour"] == [.01, .01, .01, 1]
    assert settings["barrel"]["dimensions"] == [.24, .045, .065]
    assert settings["handle"]["dimensions"] == [.045, .04, .13]
    # Existing tripod/math tests establish local +Y forward and +Z up.
    assert settings["barrel"]["position"][1] > 0
    assert settings["handle"]["position"][1] < settings["barrel"]["position"][1]
    assert settings["handle"]["position"][2] < settings["barrel"]["position"][2]


def test_both_prisms_use_exact_parent_times_local_composition_and_remain_rigid():
    root = ((4, -3, 2), (0, 0, math.sqrt(.5), math.sqrt(.5)))
    barrel_local = ((0, .12, 0), (0, 0, 0, 1))
    handle_local = ((0, .035, -.07), (0, 0, 0, 1))
    barrel = compose(root, barrel_local)
    handle = compose(root, handle_local)
    assert barrel[0] == pytest.approx((3.88, -3, 2))
    assert handle[0] == pytest.approx((3.965, -3, 1.93))
    assert math.dist(barrel[0], handle[0]) == pytest.approx(
        math.dist(barrel_local[0], handle_local[0]))
    assert qmul((-barrel[1][0], -barrel[1][1], -barrel[1][2], barrel[1][3]), handle[1]) == pytest.approx((0, 0, 0, 1))


def test_compact_final_right_pose_is_the_only_tracking_input_and_no_second_calibration():
    assert "getControllerWorldPose('right')" in SOURCE
    assert "provider.getState" not in SOURCE
    forbidden = ("cameraAnchor", "OpenXR", "trackingSpace", "quaternionBasis", "calibration")
    assert not any(word in SOURCE for word in forbidden)
    assert SOURCE.count("compose(parent,state.barrelLocalPose)") == 1
    assert SOURCE.count("compose(parent,state.handleLocalPose)") == 1


def test_invalid_or_stale_pose_hides_both_pieces():
    stale_check = "pose.ageMs<=cfg.maximumPoseAgeMs"
    assert stale_check in SOURCE
    invalid_branch = SOURCE[SOURCE.index("if not state.rightControllerPoseValid then"):]
    assert "hidePieces()" in invalid_branch
    assert "setHidden(pieces.barrel,true); setHidden(pieces.handle,true)" in SOURCE


def test_objects_are_created_once_updated_many_and_owned_cleanup_is_complete():
    update = SOURCE[SOURCE.index("function M.onPreRender"):SOURCE.index("function M.onExtensionLoaded")]
    assert "createObject" not in update
    assert "pcall(ensurePieces)" in update
    assert SOURCE.count("createObject('TSStatic')") == 1  # factory called once per owned piece
    assert "object:setPosRot" in SOURCE
    assert "function M.onExtensionUnloaded()" in SOURCE
    assert "destroyPieces()" in SOURCE[SOURCE.index("function M.onExtensionUnloaded()") :]
    assert "object:delete()" in SOURCE


def test_visual_only_contract_has_no_gameplay_or_physics_behavior():
    lower = SOURCE.lower()
    for forbidden in ("projectile", "firing", "recoil", "nodegrab", "activevehiclesiterator", "inputevent"):
        assert forbidden not in lower
    assert "object.dynamic=false" in SOURCE
    assert "object.collisionType='None'" in SOURCE
    assert "object.castShadows=false" in SOURCE
    assert len(re.findall(r"makePiece\('(barrel|handle)'", SOURCE)) == 2
