"""Runs mod/lua/ge/extensions/vrHands.lua under LuaJIT with mocked BeamNG globals."""
import json
import math
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")
try:
    from lupa import luajit21 as lua_impl
except ImportError:  # pragma: no cover
    from lupa import lua51 as lua_impl

ROOT = Path(__file__).resolve().parents[1]
S = math.sqrt(0.5)


def packet(right_aim=None, right_grip=None, trigger=0.0):
    def pose(p):
        if p is None:
            return {"ok": False, "t": 1, "p": [0, 0, 0], "q": [0, 0, 0, 1]}
        return {"ok": True, "t": 1, "p": p[0], "q": p[1]}

    return json.dumps({
        "v": 3, "seq": 1, "t": 1,
        "hands": {
            "left": {"grip": pose(None), "aim": pose(None), "trigger": 0, "squeeze": 0},
            "right": {"grip": pose(right_grip), "aim": pose(right_aim), "trigger": trigger, "squeeze": 0},
        },
    })


@pytest.fixture
def lua():
    rt = lua_impl.LuaRuntime(unpack_returned_tuples=True)
    rt.execute((ROOT / "tests" / "beamng_mock.lua").read_text())
    rt.globals().mock.decode = lambda s: rt.table_from(json.loads(s), recursive=True)
    hands = rt.execute((ROOT / "mod/lua/ge/extensions/vrHands.lua").read_text())
    hands.onExtensionLoaded()
    rt.globals().hands = hands
    return rt


def send(rt, text):
    rt.eval("function(s) table.insert(mock.inbox, s) end")(text)
    rt.globals().hands.onPreRender()


def face_plus_x(rt, conjugate=False):
    # Head at (10, 20, 1.5) looking along world +X (yaw -90 degrees about Z).
    rt.globals().mock.camPos = rt.globals().vec3(10, 20, 1.5)
    z = S if conjugate else -S
    rt.globals().mock.camQuat = rt.table_from({"x": 0, "y": 0, "z": z, "w": S})
    rt.globals().mock.camForward = rt.globals().vec3(1, 0, 0)


def vec(v):
    return (v.x, v.y, v.z)


def close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


@pytest.mark.parametrize("conjugate", [False, True])
def test_controller_in_front_of_head(lua, conjugate):
    face_plus_x(lua, conjugate)
    # 0.2 m right, 0.3 m down, 0.5 m in front of the eyes, pointing straight ahead.
    send(lua, packet(right_aim=([0.2, -0.3, -0.5], [0, 0, 0, 1])))
    pose = lua.globals().hands.getPose("right", "aim")
    assert pose.valid
    assert close(vec(pose.pos), (10.5, 19.8, 1.2))
    assert close(vec(pose.dir), (1, 0, 0))
    assert close(vec(pose.up), (0, 0, 1))
    assert close(vec(pose.right), (0, -1, 0))


def test_pointing_left(lua):
    face_plus_x(lua)
    # Rotated +90 degrees about the headset's up axis: points to the player's left (+Y here).
    send(lua, packet(right_aim=([0, 0, -0.4], [0, S, 0, S])))
    pose = lua.globals().hands.getPose("right", "aim")
    assert close(vec(pose.dir), (0, 1, 0))
    assert close(vec(pose.pos), (10.4, 20, 1.5))


def test_pitch_up(lua):
    face_plus_x(lua)
    # +45 degrees about the headset's right axis tilts the aim ray upwards.
    a = math.radians(45) / 2
    send(lua, packet(right_aim=([0, 0, -0.4], [math.sin(a), 0, 0, math.cos(a)])))
    pose = lua.globals().hands.getPose("right", "aim")
    assert close(vec(pose.dir), (S, 0, S))


def test_head_motion_moves_controller_rigidly(lua):
    face_plus_x(lua)
    send(lua, packet(right_aim=([0, 0, -0.5], [0, 0, 0, 1])))
    lua.globals().mock.camPos = lua.globals().vec3(0, 0, 0)
    lua.globals().mock.camQuat = lua.table_from({"x": 0, "y": 0, "z": 0, "w": 1})
    pose = lua.globals().hands.getPose("right", "aim")
    # Head now faces +Y; the same head-relative pose is 0.5 m along +Y.
    assert close(vec(pose.pos), (0, 0.5, 0))


def test_stale_and_invalid(lua):
    face_plus_x(lua)
    send(lua, packet(right_aim=([0, 0, -0.5], [0, 0, 0, 1]), trigger=0.9))
    assert lua.globals().hands.getTrigger("right") == pytest.approx(0.9)
    assert not lua.globals().hands.getPose("left", "aim").valid
    lua.globals().mock.time = 100 + 0.5  # 500 ms without packets
    assert not lua.globals().hands.getPose("right", "aim").valid
    assert lua.globals().hands.getTrigger("right") == 0


def test_newest_packet_wins(lua):
    face_plus_x(lua)
    lua.eval("function(a, b) table.insert(mock.inbox, a); table.insert(mock.inbox, b) end")(
        packet(right_aim=([0, 0, -0.5], [0, 0, 0, 1])),
        packet(right_aim=([0, 0, -1.0], [0, 0, 0, 1])),
    )
    lua.globals().hands.onPreRender()
    assert close(vec(lua.globals().hands.getPose("right", "aim").pos), (11, 20, 1.5))



@pytest.mark.parametrize("conjugate", [False, True])
def test_head_offset_turns_with_the_anchor(lua, conjugate):
    # BeamNG adds the tracked head offset to the anchor without turning it by the
    # anchor's yaw. With the anchor facing +X (yaw -90), a head 0.1 m right and
    # 0.2 m forward of the tracking origin really sits at anchor + (0.2, -0.1, 0).
    g = lua.globals()
    z = S if conjugate else -S
    lua.execute("mock.xrHead = {0.1, 0.2, 0, 0, 0, 0, 1}")
    g.OpenXR.setGeluaCameraPosRot(0, 0, 1, 0, 0, z, S)
    g.OpenXR.getCameraPosRotPredictedXYZXYZW()
    g.mock.camPos = g.vec3(0.1, 0.2, 1)
    g.mock.camQuat = lua.table_from({"x": 0, "y": 0, "z": z, "w": S})
    g.mock.camForward = g.vec3(1, 0, 0)
    send(lua, packet(right_aim=([0, 0, -0.5], [0, 0, 0, 1])))
    pose = g.hands.getPose("right", "aim")
    assert pose.valid
    assert close(vec(pose.pos), (0.7, -0.1, 1))
    assert close(vec(pose.dir), (1, 0, 0))

    g.hands.getConfig().fixHeadOffset = False
    assert close(vec(g.hands.getPose("right", "aim").pos), (0.6, 0.2, 1))


def test_hooks_openxr_once_vr_starts(lua):
    # OpenXR only exists once VR is on, which can be after the mod loads.
    g = lua.globals()
    lua.execute("OpenXR = nil")
    send(lua, packet())
    assert not g.hands.getStatus().openxrHooked
    lua.execute("OpenXR = mock.makeOpenXR()")
    send(lua, packet())
    assert g.hands.getStatus().openxrHooked
    lua.execute("mock.xrHead = {0.1, 0.2, 0, 0, 0, 0, 1}")
    g.OpenXR.setGeluaCameraPosRot(0, 0, 1, 0, 0, 1, 0)
    assert tuple(lua.eval("{OpenXR.getCameraPosRotPredictedXYZXYZW()}").values()) == (0.1, 0.2, 0, 0, 0, 0, 1)
    assert g.hands.getStatus().headOffsetFix

    # Reloading the extension wraps the originals again, not its own wrappers.
    again = lua.execute((ROOT / "mod/lua/ge/extensions/vrHands.lua").read_text())
    again.onExtensionLoaded()
    again.onPreRender()
    assert again.getStatus().openxrHooked
    assert lua.eval("vrHandsOpenXROriginals.set") is not None
