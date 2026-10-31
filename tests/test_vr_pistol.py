"""Runs vrPistol.lua's targeting and trigger logic under LuaJIT with mocks."""
import json
import math
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


def all_commands(rt):
    return [rt.globals().mock.commands[i + 1] for i in range(len(rt.globals().mock.commands))]


def commands(rt):
    """Vehicles that were sent a hit (sound commands are left out)."""
    return [c.name for c in all_commands(rt) if "vrPistolHit" in c.cmd]


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
    cmd = [c.cmd for c in all_commands(lua) if "vrPistolHit" in c.cmd][0]
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


# ------------------------------------------------------------ revolver model ---

@pytest.fixture
def model(lua):
    lua.globals().FS = lua.table_from({"fileExists": lambda _self, _path: True})
    face_plus_x(lua)
    return lua


def part(rt, name):
    return rt.globals().mock.objects["vrPistolModel_" + name]


def test_model_parts_follow_the_aim_pose(model):
    frame(model, packet(right_aim=AIM, trigger=0.0))
    names = sorted(model.globals().mock.objects.keys())
    assert names == ["vrPistolModel_" + n for n in ("cylinder", "flash", "frame", "hammer", "trigger")]
    f = part(model, "frame")
    assert not f.hidden
    # Aim at (10.5, 20, 1.5) facing +X; the gun origin sits 2 cm back and 5 cm up.
    assert (round(f.pos.x, 4), round(f.pos.y, 4), round(f.pos.z, 4)) == (10.48, 20.0, 1.55)
    assert part(model, "flash").hidden

    frame(model, packet(right_aim=None, trigger=0.0))
    assert all(part(model, n).hidden for n in ("frame", "cylinder", "hammer", "trigger"))


def test_double_action_cocks_then_drops_hammer_and_indexes_cylinder(model):
    frame(model, packet(right_aim=AIM, trigger=0.0))
    vp = model.globals().vrPistol
    rest = vp.getActionState(0.0)
    assert rest.hammer == 0 and rest.cylinder == 0 and rest.shotsFired == 0

    half = vp.getActionState(0.35)  # half way to the 0.7 break
    assert 0 < half.hammer < math.radians(48)
    assert half.trigger < 0
    assert 0 < half.cylinder <= math.radians(60)

    frame(model, packet(right_aim=AIM, trigger=0.9))  # breaks: shot fired
    model.globals().mock.time += 0.02
    frame(model, packet(right_aim=AIM, trigger=0.9))
    fired = vp.getActionState(0.9)
    assert fired.shotsFired == 1
    assert fired.hammer == 0                           # hammer has fallen
    assert fired.cylinder == pytest.approx(math.radians(60))
    assert fired.flash and fired.recoil > 0
    assert not part(model, "flash").hidden

    model.globals().mock.time += 1
    frame(model, packet(right_aim=AIM, trigger=0.0))
    settled = vp.getActionState(0.0)
    assert settled.recoil == 0 and not settled.flash
    assert settled.cylinder == pytest.approx(math.radians(60))
    assert part(model, "flash").hidden


def test_recoil_moves_the_model_but_not_the_shot(model):
    add(model, "car", (30, 20, 1), (2, 1, 1))
    frame(model, packet(right_aim=AIM, trigger=0.0))
    hammer_rest = part(model, "hammer").pos
    frame(model, packet(right_aim=AIM, trigger=0.9))
    model.globals().mock.time += 0.02  # near the peak of the kick
    frame(model, packet(right_aim=AIM, trigger=0.9))
    hammer_kicked = part(model, "hammer").pos
    assert hammer_kicked.x < hammer_rest.x - 0.01      # pushed back toward the shooter
    cmd = [c.cmd for c in all_commands(model) if "vrPistolHit" in c.cmd][0]
    assert "vrPistolHit.hit(10.5000,20.0000,1.5000,1.000000,0.000000,0.000000" in cmd


def test_missing_model_files_leave_shooting_working(lua):
    face_plus_x(lua)
    add(lua, "car", (30, 20, 1), (2, 1, 1))
    frame(lua, packet(right_aim=AIM, trigger=0.0))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    assert dict(lua.globals().mock.objects) == {}
    assert commands(lua) == ["car"]


def test_each_shot_plays_the_gunshot_on_the_player_vehicle(lua):
    face_plus_x(lua)
    lua.globals().mock.player = add(lua, "player", (0, -50, 0), (2, 1, 1))  # off the line of fire
    frame(lua, packet(right_aim=AIM, trigger=0.0))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    sounds = [c for c in all_commands(lua) if "playSFX" in c.cmd]
    assert [c.name for c in sounds] == ["player"]
    assert "/art/sound/vrpistol/revolver_shot.wav" in sounds[0].cmd
    # The command must be valid Lua for the vehicle VM.
    lua.eval("function(s) return assert(loadstring(s)) end")(sounds[0].cmd)


def test_gunshot_without_player_vehicle_does_not_break_shooting(lua):
    face_plus_x(lua)
    add(lua, "car", (30, 20, 1), (2, 1, 1))
    frame(lua, packet(right_aim=AIM, trigger=0.0))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    assert commands(lua) == ["car"]


def test_each_shot_vibrates_the_controller(lua):
    face_plus_x(lua)
    frame(lua, packet(right_aim=AIM, trigger=0.0))
    frame(lua, packet(right_aim=AIM, trigger=0.9))
    sent = lua.globals().mock.sent
    assert sent is not None and len(sent) == 1
    assert sent[1].data == "pulse right 1.000 0.060"
    assert (sent[1].ip, sent[1].port) == ("127.0.0.1", 44442)


def test_model_follows_camera_motion_when_predicting(model):
    g = model.globals()
    frame(model, packet(right_aim=AIM))
    rest = part(model, "frame").pos
    # Camera anchor moving at 30 m/s along +X, one 11 ms frame apart.
    g.OpenXR.setGeluaCameraPosRot(0, 0, 0, 0, 0, 0, 1)
    g.mock.time = g.mock.time + 0.011
    g.OpenXR.setGeluaCameraPosRot(0.33, 0, 0, 0, 0, 0, 1)
    g.vrPistol.getConfig().predictSeconds = 0.011
    frame(model, packet(right_aim=AIM))
    led = part(model, "frame").pos
    assert abs((led.x - rest.x) - 0.33) < 1e-6 and abs(led.y - rest.y) < 1e-9


def test_model_test_cycles_phases_and_saves(model):
    model.execute("""
      written = nil
      function writeFile(path, text) written = {path = path, text = text} return true end
      FS.directoryCreate = function() end
      FS.getUserPath = function() return 'C:/user/' end
      function jsonEncode(t) return '{"phase":"' .. t.phase .. '","mode":"' .. tostring(vrPistol.getConfig().placeMode) .. '"}' end
    """)
    g = model.globals()
    g.vrPistol.startModelTest(0.05)  # 5 phases of ~4-5 frames at 90 fps
    for _ in range(40):
        if g.written is not None:
            break
        frame(model, packet(right_aim=AIM))
    assert g.written is not None and g.written.path.startswith("/vrHandsCapture/model_")
    phases = [json.loads(line)["phase"] for line in g.written.text.strip().split("\n")]
    assert phases[0] == "A" and phases[-1] == "E" and set(phases) == set("ABCDE")
    assert g.vrPistol.getConfig().placeMode == "server"  # restored afterwards
    assert g.vrPistol.getConfig().predictSeconds == 0
