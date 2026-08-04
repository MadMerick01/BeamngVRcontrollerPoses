"""SteamVR/OpenVR tracking publisher. It never opens a second OpenXR session."""
import argparse, json, logging, socket, time


def matrix_pose(m):
    # OpenVR's 3x4 device-to-absolute transform, right-handed metres.
    from math import sqrt
    r = [[m[i][j] for j in range(3)] for i in range(3)]
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = sqrt(tr + 1.0) * 2; q = ((r[2][1]-r[1][2])/s, (r[0][2]-r[2][0])/s, (r[1][0]-r[0][1])/s, .25*s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s=sqrt(1+r[0][0]-r[1][1]-r[2][2])*2; q=(.25*s,(r[0][1]+r[1][0])/s,(r[0][2]+r[2][0])/s,(r[2][1]-r[1][2])/s)
    elif r[1][1] > r[2][2]:
        s=sqrt(1+r[1][1]-r[0][0]-r[2][2])*2; q=((r[0][1]+r[1][0])/s,.25*s,(r[1][2]+r[2][1])/s,(r[0][2]-r[2][0])/s)
    else:
        s=sqrt(1+r[2][2]-r[0][0]-r[1][1])*2; q=((r[0][2]+r[2][0])/s,(r[1][2]+r[2][1])/s,.25*s,(r[1][0]-r[0][1])/s)
    return {"p":[m[0][3],m[1][3],m[2][3]], "q":list(q)}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=44441); ap.add_argument('--hz',type=float,default=90); ap.add_argument('--log-interval',type=float,default=5)
    a=ap.parse_args(); logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try: import openvr
    except ImportError: raise SystemExit('Install hardware support: pip install .[steamvr]')
    openvr.init(openvr.VRApplication_Background); system=openvr.VRSystem(); out=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); counter=0; last=0
    try:
        while True:
            poses=system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0,
                openvr.k_unMaxTrackedDeviceCount)
            packet={"v":1,"counter":counter,"monotonic_ns":time.monotonic_ns(),"source":"openvr-standing","hmd":None,"left":None,"right":None}
            for i,p in enumerate(poses):
                valid=bool(p.bPoseIsValid and p.bDeviceIsConnected)
                cls=system.getTrackedDeviceClass(i)
                value={"valid":valid, **matrix_pose(p.mDeviceToAbsoluteTracking)} if valid else {"valid":False}
                if cls==openvr.TrackedDeviceClass_HMD: packet['hmd']=value
                elif cls==openvr.TrackedDeviceClass_Controller:
                    role=system.getControllerRoleForTrackedDeviceIndex(i)
                    if role==openvr.TrackedControllerRole_LeftHand: packet['left']=value
                    elif role==openvr.TrackedControllerRole_RightHand: packet['right']=value
            out.sendto(json.dumps(packet,separators=(',',':')).encode(),(a.host,a.port)); counter+=1
            if time.monotonic()-last>=a.log_interval: logging.info('counter=%d valid hmd/left/right=%s/%s/%s',counter,*[bool(packet[x] and packet[x]['valid']) for x in ('hmd','left','right')]); last=time.monotonic()
            time.sleep(1/a.hz)
    finally: openvr.shutdown()
