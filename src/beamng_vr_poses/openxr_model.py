"""Testable model of the native API layer's identity, lifecycle and packet rules."""
import json
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

def usable(flags): return flags & REQUIRED_VALID == REQUIRED_VALID
def relative_pose(hmd_in_base: Pose, controller_in_base: Pose) -> Pose:
    return compose(inverse(hmd_in_base), controller_in_base)

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
