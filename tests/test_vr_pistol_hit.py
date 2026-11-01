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


# ------------------------------------------------------------------ driver ---
# The car faces world +Y, roof up. Its driver camera node (cid 4) sits 1.2 m up,
# at world (100, 1, 1.2); the driver box is 0.5 m, centred 0.15 m below it.
DRIVER = """
nodePos[4] = vec3(0, 1, 1.2)
v.data.nodes[4] = {cid = 4, pos = vec3(0.35, -1, 1.2)}
obj.getDirectionVector = function() return vec3(0, 1, 0) end
obj.getDirectionVectorUp = function() return vec3(0, 0, 1) end
aiCalls, inputs = {}, {}
ai = {mode = 'traffic', setMode = function(m) aiCalls[#aiCalls + 1] = m; ai.mode = m end}
input = {event = function(name, value, filter) inputs[name] = value end}
"""


@pytest.fixture
def car(lua):
    lua.execute(DRIVER)
    return lua


def ai_calls(rt):
    return list(rt.globals().aiCalls.values())


def driver_hits(rt):
    calls = rt.globals().geCalls
    return [calls[i] for i in range(1, len(calls) + 1) if "onDriverHit" in calls[i]]


def shoot(rt, oy, oz, **kw):
    # Shot along +X from x = 90; the car (and the box) sit at x = 100.
    rt.globals().hit(90, oy, oz, 1, 0, 0, kw.get("dist", 50), 0.3, 6000, 1.3, 70, 0.3, True,
                     kw.get("size", 0.5), 0.15, kw.get("brake", 0.5))


def test_one_shot_through_the_driver_box_turns_the_ai_off(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    shoot(car, 1, 1.1)  # 0.1 m below the camera: inside the box
    assert ai_calls(car) == ["disabled"]
    assert car.globals().inputs.throttle == 0 and car.globals().inputs.brake == 0.5
    # Reported where the bullet enters the box: its near face, 0.25 m before the camera.
    assert "onDriverHit(42,99.7500,1.0000,1.1000)" in driver_hits(car)[0]


def test_shot_beside_the_box_leaves_the_ai_driving(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    shoot(car, 1, 1.4)    # 0.2 m above the camera: over the box top (1.3)
    shoot(car, 1.3, 1.05)  # 0.3 m behind it, past the back face (1.25)
    assert ai_calls(car) == [] and driver_hits(car) == []


def test_a_wall_before_the_driver_stops_the_bullet(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    shoot(car, 1, 1.1, dist=5)  # world hit 5 m out, the car is 10 m away
    assert ai_calls(car) == []


def test_driver_hit_counts_even_without_a_node_hit(car):
    # Through an open window: no node near the ray, still the driver.
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    car.execute("v.data.nodes[0], v.data.nodes[1] = nil, nil")
    shoot(car, 1, 1.1)
    assert ai_calls(car) == ["disabled"]


def test_already_stopped_or_player_car_is_left_alone(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}; ai.mode = 'disabled'")
    shoot(car, 1, 1.1)
    assert ai_calls(car) == [] and driver_hits(car) == []


def test_camera_found_by_jbeam_position_and_driver_preferred(car):
    car.execute("""v.data.camerasInternal = {
      [0] = {type = 'hood', x = 0, y = 0, z = 0},
      [1] = {type = 'driver', x = 0.35, y = -1, z = 1.2},
    }""")
    shoot(car, 1, 1.1)
    assert ai_calls(car) == ["disabled"]


def test_camera_without_a_node_uses_the_ref_node_offset(car):
    # Ref node 0 is at jbeam (0, 0, 0) and world offset (0, 0, 1). The camera is
    # 1 m forward (jbeam -y) and 0.2 m up from it: world offset (0, 1, 1.2).
    car.execute("""
      v.data.nodes[0].pos = vec3(0, 0, 0)
      v.data.refNodes = {[0] = {ref = 0}}
      v.data.camerasInternal = {[0] = {type = 'driver', x = 0, y = -1, z = 0.2}}
    """)
    shoot(car, 1, 1.1)
    assert ai_calls(car) == ["disabled"]


def test_no_driver_camera_means_no_driver_shot(car):
    shoot(car, 1, 1.1)
    assert ai_calls(car) == []


def test_driver_box_size_zero_disables_it(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    shoot(car, 1, 1.1, size=0)
    assert ai_calls(car) == []


def test_box_reaches_a_quarter_metre_from_its_centre(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    shoot(car, 1, 1.28)  # top of the head, 8 cm above the camera
    assert ai_calls(car) == ["disabled"]


def test_driver_brake_zero_just_coasts(car):
    car.execute("v.data.camerasInternal = {[0] = {type = 'driver', camNodeID = 4}}")
    shoot(car, 1, 1.1, brake=0)
    assert car.globals().inputs.brake == 0 and car.globals().inputs.throttle == 0
