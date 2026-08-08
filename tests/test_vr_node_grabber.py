"""Contract tests for PR #42's intentionally separate proximity selector."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[1]
POSE = (ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua").read_text()
GRABBER = (ROOT / "mod/lua/ge/extensions/vrNodeGrabber.lua").read_text()
CONFIG = json.loads((ROOT / "mod/settings/vrNodeGrabber.json").read_text())


def compact_pose(source: dict, hand: str) -> dict:
    pose = source[hand]
    return {key: pose.get(key) for key in
            ("valid", "ageMs", "position", "orientation", "updateCounter")}


@dataclass
class Node:
    vehicle: int
    node: int
    position: tuple[float, float, float]


def nearest(hand: tuple[float, float, float], nodes: list[Node], radius=.2,
            excluded: int | None = None) -> Node | None:
    best, best_squared = None, radius * radius
    for candidate in nodes:
        if candidate.vehicle == excluded or not all(map(math.isfinite, candidate.position)):
            continue
        squared = sum((a - b) ** 2 for a, b in zip(hand, candidate.position))
        if squared <= best_squared:
            best, best_squared = candidate, squared
    return best


class Grip:
    def __init__(self):
        self.pressed = False
        self.searches = 0
        self.candidate = False

    def set(self, value: float):
        if not self.pressed and value >= .65:
            self.pressed, self.candidate = True, True
            self.searches += 1
        elif self.pressed and value <= .35:
            self.pressed, self.candidate = False, False


def test_production_accessor_is_compact_and_hands_are_independent():
    state = {
        "left": {"valid": True, "ageMs": 2, "position": {"x": 1},
                 "orientation": {"w": 1}, "updateCounter": 7, "diagnostics": [1]},
        "right": {"valid": False, "ageMs": 8, "updateCounter": 9},
    }
    assert set(compact_pose(state, "left")) == {
        "valid", "ageMs", "position", "orientation", "updateCounter"}
    assert compact_pose(state, "left") != compact_pose(state, "right")
    accessor = POSE[POSE.index("function M.getControllerWorldPose"):]
    accessor = accessor[:accessor.index("\nend")]
    assert "getState" not in accessor and "diagnostics" not in accessor


def test_squared_distance_selects_closest_inside_radius_and_rejects_outside():
    nodes = [Node(1, 0, (.19, 0, 0)), Node(1, 1, (.05, 0, 0)), Node(2, 0, (.21, 0, 0))]
    assert nearest((0, 0, 0), nodes) == nodes[1]
    assert nearest((0, 0, 0), [nodes[2]]) is None
    loop = GRABBER[GRABBER.index("function M.findNearestNode"):GRABBER.index("function M.setGripState")]
    assert "dx*dx+dy*dy+dz*dz" in loop
    assert loop.count("math.sqrt") == 1


def test_filters_excluded_and_non_finite_nodes():
    nodes = [Node(7, 0, (.01, 0, 0)), Node(8, 0, (math.nan, 0, 0)), Node(9, 0, (.1, 0, 0))]
    assert nearest((0, 0, 0), nodes, excluded=7) == nodes[2]
    assert "safeNodeCount(vehicle)" in GRABBER
    assert "if not count" in GRABBER  # node-less JBeam objects are ineligible
    assert "vehicle.isActive" in GRABBER


def test_node_calls_are_guarded_and_world_formula_is_vehicle_plus_local_node():
    assert re.search(r"pcall\(function\(\) return vehicle:getNodeCount\(\) end\)", GRABBER)
    assert re.search(r"pcall\(function\(\) return vehicle:getNodePosition\(nodeId\) end\)", GRABBER)
    assert "local wx,wy,wz=vx+nx,vy+ny,vz+nz" in GRABBER
    assert "for nodeId=0,nodeCount-1" in GRABBER


def test_grip_hysteresis_searches_once_while_held_and_release_clears():
    grip = Grip()
    for value in (.65, .9, 1, .5):
        grip.set(value)
    assert grip.searches == 1 and grip.candidate
    grip.set(.35)
    assert not grip.candidate and not grip.pressed
    grip.set(.8)
    assert grip.searches == 2
    assert "M.findNearestNode(hand)" in GRABBER
    assert "M.clearCandidate(hand,'grip released')" in GRABBER


def test_stale_pose_and_lifecycle_invalidation_are_explicit():
    assert "pose.ageMs<=cfg.poseStaleAfterMs" in GRABBER
    for phrase in ("controller pose invalid or stale", "selected vehicle despawned or replaced",
                   "selected node unavailable", "selected vehicle reset", "extension unloaded"):
        assert phrase in GRABBER
    assert "getObjectByID" in GRABBER and "selectionObjectSignature" in GRABBER


def test_no_transform_reconstruction_or_physics_mouse_grabber_calls():
    forbidden = ("predicted", "setGeluaCameraPosRot", "getCameraPosRot", "applyForce",
                 "addForce", "onMouseButton", "renderNodes(true)")
    assert not any(token in GRABBER for token in forbidden)
    assert "getControllerWorldPose(hand)" in GRABBER
    assert "provider.getState(" not in GRABBER


def test_preview_defaults_off_is_throttled_and_capped():
    assert CONFIG["nearbyNodePreviewEnabled"] is False
    assert CONFIG["nearbyNodePreviewMaximumMarkersPerHand"] == 32
    assert "previewAccumulator>=cfg.nearbyNodePreviewIntervalSeconds" in GRABBER
    assert "#markers>=cap" in GRABBER


def test_legacy_spheres_can_be_suppressed_without_touching_tracking():
    assert CONFIG["legacyControllerSpheresVisible"] is False
    assert "setLegacyControllerSpheresVisible" in POSE
    assert "if legacyControllerSpheresVisible then debugDrawer:drawSphere" in POSE
    assert "provider.setLegacyControllerSpheresVisible" in GRABBER


def test_required_public_api_and_candidate_language():
    for name in ("getState", "getHandState", "findNearestNode", "clearCandidate",
                 "setGripState", "setNearbyNodePreviewEnabled"):
        assert f"function M.{name}" in GRABBER
    assert "candidateSelected" in GRABBER
    assert "green" not in GRABBER.lower()
    assert "grabbed" not in GRABBER.lower()
