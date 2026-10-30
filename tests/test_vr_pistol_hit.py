"""Runs the vehicle-side vrPistolHit.lua against a tiny mocked vehicle."""
from pathlib import Path

import pytest

pytest.importorskip("lupa")
from tests.test_vr_hands import ROOT, lua_impl  # noqa: E402

VEHICLE = """
dofile_mock = true
nodePos = {[0] = vec3(0, 0, 1), [1] = vec3(0, 0.5, 1), [2] = vec3(0, 3, 1), [3] = vec3(0, 3.4, 1)}
forces, deflated, geCalls = {}, {}, {}
function fsign(x) return x >= 0 and 1 or -1 end
objectId = 42
obj = {
  getPosition = function() return vec3(100, 0, 0) end,
  getNodePosition = function(_, cid) return nodePos[cid] end,
  getNodeMass = function(_, cid) return 2 end,
  applyForceVector = function(_, cid, f) forces[#forces + 1] = {cid = cid, f = f} end,
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


def forces(rt):
    f = rt.globals().forces
    return [(f[i].cid, (f[i].f.x, f[i].f.y, f[i].f.z)) for i in range(1, len(f) + 1)]


def test_hit_pushes_struck_node_and_blasts_neighbours(lua):
    # Shot from (90, 0, 1) world along +X passes through node 0 at world (100, 0, 1).
    lua.globals().hit(90, 0, 1, 1, 0, 0, 50, 0.3, 6000, 1.3, 70, 0.0, True)
    got = forces(lua)
    assert got[0] == (0, (12000.0, 0.0, 0.0))  # impactForce * node mass along the bullet
    # Blast: node 1 is 0.5 m away (inside the full-force inner radius 0.65), pushed outwards.
    blast = dict(got[1:])
    assert set(blast) == {1}  # node 0 sits on the impact point; nodes 2/3 are > 1.3 m away
    assert blast[1] == pytest.approx((0, 140000.0, 0))
    assert len(lua.globals().deflated) == 0
    assert "onVehicleHit(42,100.0000,0.0000,1.0000,2)" in lua.globals().geCalls[1]


def test_wheel_hit_deflates_tyre(lua):
    lua.globals().hit(90, 3.2, 1, 1, 0, 0, 50, 0.3, 6000, 0, 70, 0.3, True)
    assert list(lua.globals().deflated.values()) == [0]


def test_tyre_deflation_can_be_disabled(lua):
    lua.globals().hit(90, 3.2, 1, 1, 0, 0, 50, 0.3, 6000, 0, 70, 0.3, False)
    assert len(lua.globals().deflated) == 0


def test_miss_does_nothing(lua):
    lua.globals().hit(90, 10, 1, 1, 0, 0, 50, 0.3, 6000, 1.3, 70, 0.3, True)
    assert len(lua.globals().geCalls) == 0
