"""Testable model of the native API layer's identity, lifecycle and packet rules."""
import json
import threading
from types import MappingProxyType
from dataclasses import dataclass
from .math3d import Pose, compose, inverse

POSITION_VALID = 0x1
ORIENTATION_VALID = 0x2
POSITION_TRACKED = 0x4
ORIENTATION_TRACKED = 0x8
REQUIRED_VALID = POSITION_VALID | ORIENTATION_VALID

@dataclass(frozen=True)
class ActionMeta:
    action_type: str
    name: str
    localized_name: str
    subaction_paths: tuple[str, ...]

class SpaceRegistry:
    def __init__(self):
        self.actions, self.spaces, self.sessions = {}, {}, {}
    def add_action(self, handle, meta): self.actions[handle] = meta
    def add_space(self, handle, session, action, subaction):
        meta = self.actions.get(action)
        hand = subaction.removeprefix('/user/hand/') if (meta and meta.action_type == 'pose' and subaction in meta.subaction_paths and subaction in ('/user/hand/left','/user/hand/right')) else None
        self.spaces[handle] = (session, action, hand)
    def destroy_space(self, handle): self.spaces.pop(handle, None)
    def destroy_action(self, handle):
        self.actions.pop(handle, None)
        for space, value in list(self.spaces.items()):
            if value[1] == handle:
                self.spaces[space] = (value[0], value[1], None)
    def destroy_session(self, session):
        self.sessions.pop(session, None)
        self.spaces = {k:v for k,v in self.spaces.items() if v[0] != session}

@dataclass(frozen=True)
class RegistrySnapshot:
    """Immutable reader view used to test the native copy-on-write contract."""
    generation: int
    sessions: object
    spaces: object

class SnapshotRegistry:
    """Writer-locked publication; snapshot() itself never takes that lock."""
    def __init__(self):
        self._writer = threading.Lock()
        self._generation = 0
        self._sessions, self._spaces = {}, {}
        self._snapshot = RegistrySnapshot(0, MappingProxyType({}), MappingProxyType({}))
        self.destroyed_views = []
    def snapshot(self): return self._snapshot
    def _publish(self):
        self._generation += 1
        self._snapshot = RegistrySnapshot(self._generation,
            MappingProxyType(dict(self._sessions)), MappingProxyType(dict(self._spaces)))
    def add_session(self, session, view):
        with self._writer: self._sessions[session] = view; self._publish()
    def add_space(self, space, session, hand):
        with self._writer: self._spaces[space] = (session, hand); self._publish()
    def destroy_space(self, space):
        with self._writer: self._spaces.pop(space, None); self._publish()
    def destroy_session(self, session):
        with self._writer:
            view = self._sessions.pop(session, None)
            self._spaces = {k:v for k,v in self._spaces.items() if v[0] != session}
            self._publish()
        if view is not None: self.destroyed_views.append(view)
    @staticmethod
    def lookup(snapshot, space):
        metadata = snapshot.spaces.get(space)
        if not metadata or metadata[0] not in snapshot.sessions: return None
        return metadata, snapshot.sessions[metadata[0]]

def usable(flags): return flags & REQUIRED_VALID == REQUIRED_VALID
def relative_pose(hmd_in_base: Pose, controller_in_base: Pose) -> Pose:
    return compose(inverse(hmd_in_base), controller_in_base)

def model_locate(app_result, app_pose, app_flags, view_result, view_pose, view_flags):
    """Small fail-open oracle for native interception result/validity rules."""
    if app_result != 'success': return app_result, None
    valid = view_result == 'success' and usable(app_flags) and usable(view_flags)
    return app_result, (relative_pose(view_pose, app_pose), app_flags) if valid else None

def encode_packet(counter, xr_time, left, right):
    def hand(value):
        pose, flags = value
        return {'valid': usable(flags), 'flags': flags, 'p': list(pose.position), 'q': list(pose.orientation)}
    return json.dumps({'v':2, 'counter':counter, 'xrTime':xr_time, 'source':'openxr-api-layer', 'left':hand(left), 'right':hand(right)}, separators=(',', ':')).encode()

def decode_packet(data, last_counter=-1):
    packet=json.loads(data)
    if packet.get('v') != 2 or not isinstance(packet.get('counter'), int) or packet['counter'] <= last_counter: raise ValueError('invalid or stale packet')
    for name in ('left','right'):
        h=packet.get(name)
        if not isinstance(h,dict) or h.get('valid') != usable(int(h.get('flags',0))) or len(h.get('p',[])) != 3 or len(h.get('q',[])) != 4: raise ValueError('invalid hand')
    return packet

class StableHandPublisher:
    """Python oracle for the layer's per-hand candidate cache and 125 ms grace."""
    def __init__(self, freshness_ms=125):
        self.freshness_ms = freshness_ms
        self.hands = {"left": {"selected": None, "candidates": {}, "updates": 0}, "right": {"selected": None, "candidates": {}, "updates": 0}}
    def update(self, hand, candidate, valid, now_ms, pose=None, flags=REQUIRED_VALID):
        state = self.hands[hand]
        if valid:
            state["candidates"][candidate] = {"pose": pose or Pose((0,0,0),(0,0,0,1)), "flags": flags, "time": now_ms, "updates": state["candidates"].get(candidate, {}).get("updates", 0) + 1}
            state["updates"] += 1
            if state["selected"] is None:
                state["selected"] = candidate
        return self.snapshot(now_ms)
    def destroy_space(self, candidate):
        for state in self.hands.values():
            state["candidates"].pop(candidate, None)
            if state["selected"] == candidate:
                state["selected"] = None
    def destroy_session(self):
        for state in self.hands.values():
            state["selected"] = None; state["candidates"].clear()
    def _hand(self, name, now_ms):
        state = self.hands[name]
        def fresh(item): return now_ms - item[1]["time"] <= self.freshness_ms
        selected = state["selected"]
        chosen = None
        if selected in state["candidates"] and fresh((selected, state["candidates"][selected])):
            chosen = (selected, state["candidates"][selected])
        else:
            fresh_candidates = [item for item in state["candidates"].items() if fresh(item)]
            chosen = max(fresh_candidates, key=lambda item: item[1]["time"], default=None)
        if not chosen:
            state["selected"] = None
            return {"valid": False, "candidate": None, "updates": state["updates"], "ageMs": 0}
        state["selected"] = chosen[0]
        return {"valid": True, "candidate": chosen[0], "updates": state["updates"], "ageMs": now_ms - chosen[1]["time"], "pose": chosen[1]["pose"]}
    def snapshot(self, now_ms):
        return {"left": self._hand("left", now_ms), "right": self._hand("right", now_ms)}
