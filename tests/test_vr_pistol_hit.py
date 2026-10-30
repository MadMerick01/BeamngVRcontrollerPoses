"""Runs the vehicle-side vrPistolHit.lua against a tiny mocked vehicle."""
from pathlib import Path

import pytest

pytest.importorskip("lupa")
from tests.test_vr_hands import ROOT, lua_impl  # noqa: E402

VEHICLE = """
dofile_mock = true
nodePos = {[0] = vec3(0, 0, 1), [1] = vec3(0, 0.5, 1), [2] = vec3(0, 3, 1), [3] = vec3(0, 3.4, 1)}
broken, deflated, geCalls = {}, {}, {}
objectId = 42
obj = {
  getPosition = function() return vec3(100, 0, 0) end,
  getNodePosition = function(_, cid) return nodePos[cid] end,
  beamIsBroken = function(_, cid) return broken[cid] == true end,
  breakBeam = function(_, cid) broken[cid] = true end,
  queueGameEngineLua = function(_, s) geCalls[#geCalls + 1] = s end,
}
v = {data = {
  nodes = {[0] = {cid = 0}, [1] = {cid = 1}, [2] = {cid = 2}, [3] = {cid = 3}},
  beams = {[0] = {cid = 0, id1 = 0, id2 = 1}, [1] = {cid = 1, id1 = 2, id2 = 3}, [2] = {cid = 2, id1 = 1, id2 = 2}},
  wheels = {[0] = {node1 = 2, node2 = 3, radius = 0.35}},
}}
beamstate = {deflateTire = function(id) deflated[#deflated + 1] = id end}
"""


@pytest.fixture
def lua():
    rt = lua_impl.LuaRuntime(unpack_returned_tuples=True)
    rt.execute((ROOT / "tests" / "beamng_mock.lua").read_text())
    rt.execute(VEHICLE)
    rt.globals().hit = rt.execute((ROOT / "mod/lua/vehicle/extensions/vrPistolHit.lua").read_text()).hit
    return rt


def test_hit_breaks_nearest_beams(lua):
    # Shot from (90, 0, 1) world along +X passes through node 0 at world (100, 0, 1).
    lua.globals().hit(90, 0, 1, 1, 0, 0, 50, 0.3, 0.6, 1)
    g = lua.globals()
    assert g.broken[0] and not g.broken[1]
    assert len(g.deflated) == 0
    assert "onVehicleHit(42,100.0000,0.0000,1.0000,1)" in g.geCalls[1]


def test_wheel_hit_deflates_tyre(lua):
    lua.globals().hit(90, 3.2, 1, 1, 0, 0, 50, 0.3, 0.1, 2)
    assert list(lua.globals().deflated.values()) == [0]


def test_miss_does_nothing(lua):
    lua.globals().hit(90, 10, 1, 1, 0, 0, 50, 0.3, 0.6, 4)
    assert len(lua.globals().geCalls) == 0
