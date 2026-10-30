"""Runs vrPistol.lua's targeting and trigger logic under LuaJIT with mocks."""
import json
from pathlib import Path

import pytest

pytest.importorskip("lupa")
from tests.test_vr_hands import ROOT, lua_impl, packet, face_plus_x  # noqa: E402


@pytest.fixture
def lua():
    rt = lua_impl.LuaRuntime(unpack_returned_tuples=True)
    rt.execute((ROOT / "tests" / "beamng_mock.lua").read_text())
    rt.globals().mock.decode = lambda s: rt.table_from(json.loads(s), recursive=True)
    hands = rt.execute((ROOT / "mod/lua/ge/extensions/vrHands.lua").read_text())
    hands.onExtensionLoaded()
    rt.globals().vrHands = hands
    rt.globals().vrPistol = rt.execute((ROOT / "mod/lua/ge/extensions/vrPistol.lua").read_text())
    return rt


def frame(rt, text):
    rt.eval("function(s) table.insert(mock.inbox, s) end")(text)
    rt.globals().vrHands.onPreRender()
    rt.globals().vrPistol.onPreRender()


def commands(rt):
    return [rt.globals().mock.commands[i + 1].name for i in range(len(rt.globals().mock.commands))]


def add(rt, name, center, half):
    v = rt.globals().vec3
    return rt.globals().mock.addVehicle(name, v(*center), v(*half))


AIM = ([0, 0, -0.5], [0, 0, 0, 1])  # straight ahead: world +X from (10.5, 20, 1.5)


def test_trigger_fires_once_per_pull_at_nearest_vehicle(lua):
    face_plus_x(lua)
    add(lua, "far", (60, 20, 1), (2, 1, 1))
    add(lua, "near", (30, 20, 1), (2, 1, 1))
    add(lua, "offAxis", (30, 40, 1), (2, 1, 1))
    frame(lua, packet(right_aim=AIM, trigger=0.0))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    frame(lua, packet(right_aim=AIM, trigger=0.95))  # still held: no second shot
    assert commands(lua) == ["near"]
    cmd = lua.globals().mock.commands[1].cmd
    assert "vrPistolHit.hit(10.5000,20.0000,1.5000,1.000000,0.000000,0.000000" in cmd

    lua.globals().mock.time += 1
    frame(lua, packet(right_aim=AIM, trigger=0.1))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    assert commands(lua) == ["near", "near"]


def test_wall_blocks_shot_and_player_car_is_skipped(lua):
    face_plus_x(lua)
    lua.globals().mock.player = add(lua, "player", (11, 20, 1), (2, 1, 1))
    add(lua, "behindWall", (30, 20, 1), (2, 1, 1))
    lua.globals().mock.wallDistance = 10
    frame(lua, packet(right_aim=AIM, trigger=0.0))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    assert commands(lua) == []


def test_no_shot_without_tracking(lua):
    face_plus_x(lua)
    add(lua, "car", (30, 20, 1), (2, 1, 1))
    frame(lua, packet(right_aim=None, trigger=0.9))
    assert commands(lua) == []
