"""Builds the VR revolver model and writes it into the mod.

A 1960s-style large-frame .44 revolver with a 6.5" barrel, modelled from
scratch here so the mod owns the asset outright. Moving parts are separate
shapes, each with its origin on its own pivot, so vrPistol.lua can animate
them:

  frame.dae     frame, barrel, sights, trigger guard, grips
  cylinder.dae  origin on the cylinder axis at its rear face, spins about +Y
  hammer.dae    origin on the hammer pivot, cocks about +X
  trigger.dae   origin on the trigger pivot, pulls about +X
  flash.dae     muzzle flash, origin at the muzzle

Gun space matches the aim pose: +X right, +Y along the barrel, +Z up, metres.
The bore axis is the Y axis; PIVOTS below gives each part's origin in gun space.

Run:  pip install bpy  &&  python tools/revolverModel/build_revolver.py
(Blender 5 dropped COLLADA export, so this writes the .dae files itself.)
"""
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import bpy  # noqa: I001 (bpy must load before bmesh)
import bmesh
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'mod' / 'art' / 'shapes' / 'vrrevolver'
ART = '/art/shapes/vrrevolver/'

CZ = -0.0155          # cylinder axis height (the top chamber lines up with the bore)
CYL_R = 0.0215        # cylinder radius
CYL_LEN = 0.043
CHAMBER_R = 0.0155    # chamber circle radius
FRAME_FRONT = 0.047
MUZZLE = 0.218

GRIP_UV = (-0.075, -0.145, 0.105)  # grip texture: (y, z) of the UV origin and size in metres

PIVOTS = {
    'frame': (0.0, 0.0, 0.0),
    'cylinder': (0.0, 0.0, CZ),
    'hammer': (0.0, -0.0175, -0.006),
    'trigger': (0.0, 0.012, -0.040),
    'flash': (0.0, MUZZLE, 0.0),
}

# ----------------------------------------------------------------- scene ---

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mesh_object(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def apply_mods(ob):
    bpy.context.view_layer.objects.active = ob
    for m in list(ob.modifiers):
        bpy.ops.object.modifier_apply(modifier=m.name)


def bevel(ob, width, segments=2, angle=40):
    m = ob.modifiers.new('bevel', 'BEVEL')
    m.width = width
    m.segments = segments
    m.limit_method = 'ANGLE'
    m.angle_limit = math.radians(angle)
    m.harden_normals = False
    apply_mods(ob)
    return ob


def boolean(ob, cutter, op='DIFFERENCE', keep=False):
    m = ob.modifiers.new('bool', 'BOOLEAN')
    m.operation = op
    m.object = cutter
    m.solver = 'EXACT'
    apply_mods(ob)
    if not keep:
        bpy.data.objects.remove(cutter)
    return ob


def join(obs, name):
    bpy.ops.object.select_all(action='DESELECT')
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    bpy.ops.object.join()
    obs[0].name = name
    return obs[0]


def smooth_outline(pts, iters=3, sharp=()):
    """Chaikin corner cutting on a closed outline; vertices in sharp keep their corner."""
    pts = [(p, i in sharp) for i, p in enumerate(pts)]
    for _ in range(iters):
        out = []
        n = len(pts)
        for i in range(n):
            (p, ps), (q, qs) = pts[i], pts[(i + 1) % n]
            if ps:
                out.append((p, True))
            else:
                out.append(((0.75 * p[0] + 0.25 * q[0], 0.75 * p[1] + 0.25 * q[1]), False))
            if qs:
                continue
            out.append(((0.25 * p[0] + 0.75 * q[0], 0.25 * p[1] + 0.75 * q[1]), False))
        pts = out
    return [p for p, _ in pts]


def profile(name, pts_yz, width, x0=None):
    """Extrudes a side outline (Y, Z) across X, centred unless x0 is given."""
    bm = bmesh.new()
    x0 = -width / 2 if x0 is None else x0
    lo = [bm.verts.new((x0, y, z)) for y, z in pts_yz]
    face = bm.faces.new(lo)
    bmesh.ops.recalc_face_normals(bm, faces=[face])
    if face.normal.x > 0:
        face.normal_flip()
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    verts = [v for v in ext['geom'] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=verts, vec=(width, 0, 0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm)


def cylinder(name, r, y0, y1, center=(0, 0), segs=48, r1=None, axis='Y'):
    """Cylinder (or cone when r1 is given) from y0 to y1 along `axis`."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                          radius1=r, radius2=r if r1 is None else r1, depth=y1 - y0)
    ob = mesh_object(name, bm)
    a, b = center
    if axis == 'Y':
        # create_cone points along +Z; tip +Z onto +Y.
        ob.matrix_world = Matrix.Translation((a, (y0 + y1) / 2, b)) @ Matrix.Rotation(-math.pi / 2, 4, 'X')
    else:
        ob.matrix_world = Matrix.Translation(((y0 + y1) / 2, a, b)) @ Matrix.Rotation(math.pi / 2, 4, 'Y')
    bake(ob)
    return ob


def box(name, x, y, z):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    ob = mesh_object(name, bm)
    ob.matrix_world = Matrix.Translation(((x[0] + x[1]) / 2, (y[0] + y[1]) / 2, (z[0] + z[1]) / 2)) @ \
        Matrix.Diagonal((x[1] - x[0], y[1] - y[0], z[1] - z[0], 1))
    bake(ob)
    return ob


def sphere(name, r, c, segs=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=segs // 2, radius=r)
    ob = mesh_object(name, bm)
    ob.matrix_world = Matrix.Translation(c)
    bake(ob)
    return ob


def tube_curve(name, pts, radius, scale_x=1.0):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(pts) - 1)
    for bp, p in zip(sp.bezier_points, pts):
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = 'AUTO'
    cu.bevel_depth = radius
    cu.bevel_resolution = 4
    cu.resolution_u = 10
    cu.use_fill_caps = True
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.ops.object.convert(target='MESH')
    ob = bpy.context.view_layer.objects.active
    ob.scale = (scale_x, 1, 1)
    bake(ob)
    return ob


def bake(ob):
    ob.data.transform(ob.matrix_world)
    ob.matrix_world = Matrix.Identity(4)


def smooth(ob, angle=35):
    me = ob.data
    for p in me.polygons:
        p.use_smooth = True
    me.set_sharp_from_angle(angle=math.radians(angle))


def set_material(ob, name):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    ob.data.materials.clear()
    ob.data.materials.append(mat)


def project_uv(ob, axis_u, axis_v, scale, offset=(0, 0)):
    """Planar UVs: u, v = (p . axis) / scale. Good enough for side-on textures."""
    me = ob.data
    uv = me.uv_layers.get('UVMap') or me.uv_layers.new(name='UVMap')
    au, av = Vector(axis_u), Vector(axis_v)
    for loop in me.loops:
        p = me.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (p.dot(au) / scale + offset[0], p.dot(av) / scale + offset[1])


def box_uv(ob, scale):
    """Box projection by face normal, so any part can carry a tiling texture."""
    me = ob.data
    uv = me.uv_layers.get('UVMap') or me.uv_layers.new(name='UVMap')
    for poly in me.polygons:
        n = poly.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        u_i, v_i = [(1, 2), (0, 2), (0, 1)][ax]
        for li in poly.loop_indices:
            p = me.vertices[me.loops[li].vertex_index].co
            uv.data[li].uv = (p[u_i] / scale, p[v_i] / scale)

# ----------------------------------------------------------------- parts ---

def build_frame():
    parts = []

    # Side outline: top strap, a round hump concentric with the hammer, backstrap
    # down into the grip, then the frame bottom forward to the barrel.
    _, hy, hz = PIVOTS['hammer']
    hump = [(hy + 0.0145 * math.cos(math.radians(a)), hz + 0.0135 * math.sin(math.radians(a)))
            for a in range(50, 210, 10)]
    outline = [(0.058, 0.0140), (-0.004, 0.0140)] + hump + [
        (-0.0375, -0.0230), (-0.033, -0.0330), (-0.022, -0.0410), (-0.006, -0.0470),
        (0.020, -0.0460), (0.044, -0.0440), (0.058, -0.0300),
    ]
    frame = profile('frame', smooth_outline(outline, 2, sharp=(0, 1, len(outline) - 1)), 0.034)
    # Rounded top strap: trim the upper corners with a lengthwise cylinder.
    strap = cylinder('strapTrim', 0.0205, -0.05, 0.07, center=(0, -0.0065), segs=64)
    keep = box('strapKeep', (-0.05, 0.05), (-0.05, 0.07), (-0.08, -0.004))
    strap = boolean(strap, keep, 'UNION')
    frame = boolean(frame, strap, 'INTERSECT')
    # Cylinder window, open on both sides.
    frame = boolean(frame, box('window', (-0.03, 0.03), (-0.0005, 0.0445), (-0.0405, 0.0078)))
    # Hammer slot.
    frame = boolean(frame, box('hammerSlot', (-0.0048, 0.0048), (-0.045, -0.0105), (-0.022, 0.03)))
    # Trigger slot through the frame bottom.
    frame = boolean(frame, box('triggerSlot', (-0.0045, 0.0045), (0.005, 0.024), (-0.06, -0.034)))
    bevel(frame, 0.0012, 2)
    parts.append(frame)

    # Recoil shield: round boss behind the cylinder.
    shield = cylinder('shield', CYL_R - 0.0005, -0.0105, -0.0005, center=(0, CZ), segs=64)
    shield = boolean(shield, box('shieldCut', (-0.03, 0.03), (-0.02, 0.01), (-0.003, 0.03)))
    bevel(shield, 0.0008, 2)
    parts.append(shield)

    # Barrel: slightly tapered tube with a ventilated-style top rib.
    barrel = cylinder('barrel', 0.0105, FRAME_FRONT, MUZZLE, segs=64, r1=0.0095)
    rib = box('rib', (-0.0042, 0.0042), (FRAME_FRONT, MUZZLE), (0.004, 0.0136))
    barrel = boolean(barrel, rib, 'UNION')
    # Ejector rod shroud and the web joining it to the barrel.
    shroud = cylinder('shroud', 0.0072, FRAME_FRONT, 0.122, center=(0, CZ), segs=48)
    # Ejector rod tip peeking out of the shroud.
    shroud = boolean(shroud, cylinder('rodTip', 0.0036, 0.12, 0.1265, center=(0, CZ), segs=24), 'UNION')
    web = box('web', (-0.0048, 0.0048), (FRAME_FRONT, 0.118), (CZ, -0.004))
    barrel = boolean(barrel, shroud, 'UNION')
    barrel = boolean(barrel, web, 'UNION')
    bore = cylinder('bore', 0.0055, FRAME_FRONT - 0.01, MUZZLE + 0.01, segs=32)
    barrel = boolean(barrel, bore)
    bevel(barrel, 0.0009, 2)
    parts.append(barrel)

    # Bore liner, so the muzzle reads as a dark hole instead of see-through.
    liner = cylinder('liner', 0.0054, MUZZLE - 0.03, MUZZLE - 0.004, segs=32)
    set_material(liner, 'vrrevolver_bore')

    # Front sight: ramp on the rib with a red insert.
    ramp = profile('ramp', [(0.196, 0.0136), (0.212, 0.0222), (0.2175, 0.0222), (0.2175, 0.0136)], 0.0034)
    bevel(ramp, 0.0004, 1)
    parts.append(ramp)
    insert = profile('insert', [(0.2055, 0.0166), (0.2125, 0.0204), (0.2155, 0.0204), (0.2155, 0.0166)], 0.0036)
    set_material(insert, 'vrrevolver_red')

    # Adjustable rear sight: base on the top strap, blade with a square notch.
    rear = box('rearSight', (-0.0065, 0.0065), (-0.004, 0.011), (0.0125, 0.0172))
    blade = box('rearBlade', (-0.0068, 0.0068), (-0.006, -0.0025), (0.0125, 0.0205))
    rear = boolean(rear, blade, 'UNION')
    rear = boolean(rear, box('notch', (-0.0015, 0.0015), (-0.01, 0.0), (0.0175, 0.025)))
    bevel(rear, 0.0004, 1)
    parts.append(rear)

    # Trigger guard: flat oval strap looping under the trigger.
    guard = tube_curve('guard', [
        (0, 0.040, -0.0435), (0, 0.037, -0.058), (0, 0.025, -0.0665),
        (0, 0.006, -0.0655), (0, -0.003, -0.057), (0, -0.0045, -0.046)], 0.0025, scale_x=1.8)
    parts.append(guard)

    # Frame screws, a nice touch on the sideplate.
    for y, z in [(0.030, -0.039), (-0.012, -0.030), (0.012, -0.012)]:
        for side in (1, -1):
            s = cylinder('screw', 0.0024, -0.0006, 0.0006, center=(y, z), axis='X', segs=20)
            s.data.transform(Matrix.Translation((side * 0.0170, 0, 0)))
            parts.append(s)

    steel = join(parts, 'frameSteel')
    smooth(steel)
    set_material(steel, 'vrrevolver_blued')
    box_uv(steel, 0.05)

    grip = build_grip()
    for ob in (liner, insert):
        smooth(ob)
        box_uv(ob, 0.05)
    return [steel, grip, liner, insert]


def build_grip():
    # Oversize 1960s target stocks: full backstrap, flared base, filler behind the guard.
    outline = [
        (-0.0045, -0.0465), (-0.009, -0.058), (-0.014, -0.074), (-0.017, -0.092),
        (-0.019, -0.110), (-0.022, -0.124), (-0.030, -0.131), (-0.048, -0.133),
        (-0.062, -0.127), (-0.064, -0.112), (-0.059, -0.088), (-0.051, -0.064),
        (-0.043, -0.046), (-0.036, -0.032), (-0.031, -0.0215), (-0.024, -0.020),
        (-0.016, -0.034),
    ]
    grip = profile('grip', smooth_outline(outline, sharp=(0, 16)), 0.037)
    bevel(grip, 0.006, 2, angle=30)
    sub = grip.modifiers.new('sub', 'SUBSURF')
    sub.levels = sub.render_levels = 1
    apply_mods(grip)
    smooth(grip, 60)
    set_material(grip, 'vrrevolver_wood')
    # Side-on UVs over the grip's footprint, which the wood texture is laid out for.
    project_uv(grip, (0, 1, 0), (0, 0, 1), GRIP_UV[2], offset=(-GRIP_UV[0] / GRIP_UV[2], -GRIP_UV[1] / GRIP_UV[2]))
    return grip


def build_cylinder():
    pz = PIVOTS['cylinder'][2]
    cyl = cylinder('cyl', CYL_R, 0.0, CYL_LEN, center=(0, pz), segs=96)
    # Flutes between the chambers.
    for k in range(6):
        a = math.radians(30 + 60 * k)
        c = (math.sin(a) * (CYL_R + 0.0016), math.cos(a) * (CYL_R + 0.0016) + pz)
        flute = cylinder('flute', 0.0046, 0.012, 0.036, center=c, segs=24)
        flute = boolean(flute, sphere('fA', 0.0046, (c[0], 0.012, c[1])), 'UNION')
        flute = boolean(flute, sphere('fB', 0.0046, (c[0], 0.036, c[1])), 'UNION')
        cyl = boolean(cyl, flute)
    # Chambers and cylinder stop notches.
    for k in range(6):
        a = math.radians(60 * k)
        c = (math.sin(a) * CHAMBER_R, math.cos(a) * CHAMBER_R + pz)
        cyl = boolean(cyl, cylinder('chamber', 0.0059, -0.01, CYL_LEN + 0.01, center=c, segs=32))
        n = Vector((math.sin(a), 0, math.cos(a)))
        notch = box('notch', (-0.0016, 0.0016), (0.0045, 0.0105), (-0.0012, 0.0012))
        notch.data.transform(Matrix.Translation(n * CYL_R) @ Matrix.Rotation(-a, 4, 'Y'))
        notch.data.transform(Matrix.Translation((0, 0, pz)))
        cyl = boolean(cyl, notch)
    bevel(cyl, 0.0009, 2, angle=50)
    # Ejector rod stub out the front.
    rod = cylinder('rod', 0.0034, CYL_LEN - 0.001, FRAME_FRONT + 0.002, center=(0, pz), segs=24)
    steel = join([cyl, rod], 'cylinderSteel')
    smooth(steel)
    set_material(steel, 'vrrevolver_blued')
    box_uv(steel, 0.05)

    # Six loaded rounds: brass case heads at the back, lead noses up front.
    brass, lead = [], []
    for k in range(6):
        a = math.radians(60 * k)
        c = (math.sin(a) * CHAMBER_R, math.cos(a) * CHAMBER_R + pz)
        brass.append(cylinder('case', 0.0058, 0.0004, CYL_LEN - 0.010, center=c, segs=24))
        brass.append(cylinder('rim', 0.0066, -0.0008, 0.0004, center=c, segs=24))
        nose = cylinder('nose', 0.0054, CYL_LEN - 0.011, CYL_LEN - 0.0045, center=c, segs=24)
        nose = boolean(nose, sphere('tip', 0.0049, (c[0], CYL_LEN - 0.0050, c[1]), segs=16), 'UNION')
        lead.append(nose)
    brass = join(brass, 'cylinderBrass')
    lead = join(lead, 'cylinderLead')
    for ob, mat in ((brass, 'vrrevolver_brass'), (lead, 'vrrevolver_lead')):
        smooth(ob, 40)
        set_material(ob, mat)
        box_uv(ob, 0.05)
    return [steel, brass, lead]


def build_hammer():
    px, py, pz = PIVOTS['hammer']
    # Outline relative to the pivot: nose forward, wide checkered spur up and back.
    outline = [
        (0.0045, -0.0050), (0.0050, 0.0110), (0.0020, 0.0165), (-0.0040, 0.0190),
        (-0.0120, 0.0225), (-0.0195, 0.0245), (-0.0225, 0.0215), (-0.0170, 0.0160),
        (-0.0105, 0.0090), (-0.0085, -0.0040), (-0.0040, -0.0100),
    ]
    body = profile('hammer', [(y + py, z + pz) for y, z in outline], 0.0088)
    spur = profile('spur', [(y + py, z + pz) for y, z in
                            [(-0.0110, 0.0205), (-0.0130, 0.0236), (-0.0195, 0.0250),
                             (-0.0228, 0.0218), (-0.0170, 0.0170)]], 0.0120)
    body = boolean(body, spur, 'UNION')
    bevel(body, 0.0007, 2)
    pin = cylinder('pin', 0.0022, -0.0050, 0.0050, center=(py, pz), axis='X', segs=20)
    ob = join([body, pin], 'hammer')
    smooth(ob)
    set_material(ob, 'vrrevolver_casehardened')
    box_uv(ob, 0.03)
    return [ob]


def build_trigger():
    px, py, pz = PIVOTS['trigger']
    # Wide smooth target trigger, curved blade.
    outline = [
        (-0.0035, 0.0030), (0.0045, 0.0030), (0.0058, -0.0060), (0.0052, -0.0140),
        (0.0028, -0.0205), (-0.0012, -0.0215), (-0.0010, -0.0150), (-0.0006, -0.0080),
        (-0.0030, -0.0030),
    ]
    blade = profile('trigger', [(y + py, z + pz) for y, z in outline], 0.0082)
    bevel(blade, 0.0012, 3)
    ob = blade
    smooth(ob, 45)
    set_material(ob, 'vrrevolver_casehardened')
    box_uv(ob, 0.03)
    return [ob]


def build_flash():
    # Three crossed cards plus a front-facing disc, textured with a soft burst.
    _, my, _ = PIVOTS['flash']
    length, width = 0.13, 0.075
    obs = []
    for k in range(3):
        bm = bmesh.new()
        a = math.radians(60 * k)
        ax = Vector((math.cos(a), 0, math.sin(a))) * (width / 2)
        quad = [Vector((0, my, 0)) - ax, Vector((0, my, 0)) + ax,
                Vector((0, my + length, 0)) + ax, Vector((0, my + length, 0)) - ax]
        verts = [bm.verts.new(v) for v in quad]
        bm.faces.new(verts)
        ob = mesh_object('card', bm)
        uv = ob.data.uv_layers.new(name='UVMap')
        for li, (u, v) in enumerate([(0, 0), (0.5, 0), (0.5, 1), (0, 1)]):
            uv.data[li].uv = (u, v)
        obs.append(ob)
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, segments=16, radius=0.035)
    disc = mesh_object('disc', bm)
    disc.matrix_world = Matrix.Translation((0, my + 0.004, 0)) @ Matrix.Rotation(math.pi / 2, 4, 'X')
    bake(disc)
    project_uv(disc, (1, 0, 0), (0, 0, 1), 0.07, offset=(0.75, 0.5))
    obs.append(disc)
    ob = join(obs, 'flash')
    set_material(ob, 'vrrevolver_flash')
    return [ob]

# -------------------------------------------------------------- textures ---

def noise2d(shape, scale, seed, octaves=4):
    """Smooth value noise in [0, 1]."""
    rng = np.random.default_rng(seed)
    h, w = shape
    out = np.zeros(shape)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        s = max(2, int(scale * 2 ** o))
        grid = rng.random((s + 1, s + 1))
        ys, xs = np.linspace(0, s, h, endpoint=False), np.linspace(0, s, w, endpoint=False)
        y0, x0 = ys.astype(int), xs.astype(int)
        fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
        fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
        a = grid[y0][:, x0]
        b = grid[y0][:, x0 + 1]
        c = grid[y0 + 1][:, x0]
        d = grid[y0 + 1][:, x0 + 1]
        out += amp * ((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy)
        total += amp
        amp *= 0.5
    return out / total


def save_png(path, rgb):
    from PIL import Image
    arr = np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)
    Image.fromarray(arr, 'RGBA' if arr.shape[2] == 4 else 'RGB').save(path, optimize=True)


def grip_mask(n):
    """Checkered panel on the grip, in the grip's side-on UV space."""
    from PIL import Image, ImageDraw
    img = Image.new('L', (n, n), 0)
    d = ImageDraw.Draw(img)
    y0, z0, size = GRIP_UV
    def px(y, z):  # image row 0 is v = 1
        return ((y - y0) / size * n, (1 - (z - z0) / size) * n)
    d.polygon([px(-0.016, -0.064), px(-0.019, -0.112), px(-0.030, -0.121),
               px(-0.052, -0.121), px(-0.056, -0.096), px(-0.047, -0.066),
               px(-0.038, -0.054), px(-0.024, -0.054)], fill=255)
    return np.asarray(img, dtype=float) / 255


def make_textures():
    n = 1024
    yy, xx = np.mgrid[0:n, 0:n] / n

    # Goncalo Alves: warm brown with dark streaky grain running along the grip.
    warp = noise2d((n, n), 2, 1) * 2.5 + noise2d((n, n), 7, 3) * 0.6
    grain = np.sin((xx + yy * 0.15) * 140 + warp * 6) * 0.5 + 0.5
    streak = np.sin((xx + yy * 0.1) * 31 + warp * 3) * 0.5 + 0.5
    grain = 0.6 * grain ** 4 + 0.4 * streak ** 6
    figure = noise2d((n, n), 6, 2)
    base = np.array([0.30, 0.12, 0.05])
    dark = np.array([0.09, 0.035, 0.015])
    wood = base[None, None] * (0.8 + 0.4 * figure[..., None])
    wood = wood * (1 - 0.7 * grain[..., None]) + dark[None, None] * 0.7 * grain[..., None]

    # Diamond checkering in the panel: two sets of grooves at +-30 degrees.
    mask = grip_mask(n)
    pitch = 0.011  # in UV units, about 20 lines per inch
    u1 = (xx * math.cos(math.radians(60)) + yy * math.sin(math.radians(60))) / pitch
    u2 = (xx * math.cos(math.radians(120)) + yy * math.sin(math.radians(120))) / pitch
    t1, t2 = np.abs(u1 % 1 - 0.5) * 2, np.abs(u2 % 1 - 0.5) * 2
    height = np.minimum(t1, t2)                       # 1 at the diamond peaks
    shade = 0.5 + 0.4 * height
    wood = wood * (1 - mask[..., None]) + wood * shade[..., None] * mask[..., None]
    save_png(OUT / 'revolver_wood_baseColor.png', wood)

    # Normal map from the checkering height (flat outside the panel).
    hgt = height * mask
    gy, gx = np.gradient(hgt)
    strength = 2.5
    nx, ny, nz = -gx * strength, gy * strength, np.ones_like(hgt)
    l = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    normal = np.stack([nx / l, ny / l, nz / l], -1) * 0.5 + 0.5
    save_png(OUT / 'revolver_wood_normal.png', normal)

    # Colour case hardening: mottled blues, straws and purples over grey.
    m = 512
    a = noise2d((m, m), 4, 7)
    b = noise2d((m, m), 6, 8)
    c = noise2d((m, m), 9, 9)
    pal = np.array([[0.32, 0.32, 0.33], [0.22, 0.25, 0.33], [0.42, 0.36, 0.26],
                    [0.28, 0.24, 0.30], [0.16, 0.17, 0.20], [0.34, 0.34, 0.35]])
    idx = (a * 2.2 + b * 1.6 + c) % 1.0 * (len(pal) - 1)
    i0 = np.floor(idx).astype(int)
    f = (idx - i0)[..., None]
    ch = pal[i0] * (1 - f) + pal[np.minimum(i0 + 1, len(pal) - 1)] * f
    save_png(OUT / 'revolver_casehardened_baseColor.png', ch)

    # Muzzle flash: left half is a side-on burst for the cards, right half a star for the disc.
    k = 256
    fy, fx = np.mgrid[0:k, 0:k] / k
    side = np.zeros((k, k, 4))
    across = np.abs(fx * 2 - 1)                       # 0 on the axis
    along = 1 - fy                                    # 0 at the muzzle (v = 0 is the bottom row)
    flame = np.clip(1 - across / np.maximum(0.15, 0.9 * np.sin(np.clip(along, 0, 1) * math.pi) ** 0.7 + 0.05), 0, 1)
    flame *= np.clip(1.1 - along, 0, 1) ** 1.5
    flame *= 0.75 + 0.25 * noise2d((k, k), 8, 11)
    side[..., 0], side[..., 1], side[..., 2] = 1.0, 0.55 + 0.4 * flame, 0.15 + 0.5 * flame ** 3
    side[..., 3] = flame
    cy, cx = fy * 2 - 1, fx * 2 - 1
    r = np.sqrt(cx ** 2 + cy ** 2)
    ang = np.arctan2(cy, cx)
    star = np.clip(1 - r / (0.45 + 0.4 * np.abs(np.cos(ang * 3)) ** 4), 0, 1) ** 1.5
    front = np.zeros((k, k, 4))
    front[..., 0], front[..., 1], front[..., 2] = 1.0, 0.6 + 0.4 * star, 0.2 + 0.6 * star ** 2
    front[..., 3] = star
    save_png(OUT / 'revolver_flash.png', np.concatenate([side, front], 1))


MATERIALS = {
    'vrrevolver_blued': {'baseColorFactor': [0.035, 0.040, 0.055, 1], 'metallicFactor': 1.0, 'roughnessFactor': 0.22},
    'vrrevolver_bore': {'baseColorFactor': [0.01, 0.01, 0.01, 1], 'metallicFactor': 0.6, 'roughnessFactor': 0.7},
    'vrrevolver_red': {'baseColorFactor': [0.75, 0.04, 0.03, 1], 'metallicFactor': 0.0, 'roughnessFactor': 0.35},
    'vrrevolver_wood': {'baseColorMap': ART + 'revolver_wood_baseColor.png', 'normalMap': ART + 'revolver_wood_normal.png',
                        'metallicFactor': 0.0, 'roughnessFactor': 0.38},
    'vrrevolver_casehardened': {'baseColorMap': ART + 'revolver_casehardened_baseColor.png',
                                'metallicFactor': 1.0, 'roughnessFactor': 0.28},
    'vrrevolver_brass': {'baseColorFactor': [0.78, 0.58, 0.27, 1], 'metallicFactor': 1.0, 'roughnessFactor': 0.3},
    'vrrevolver_lead': {'baseColorFactor': [0.42, 0.42, 0.44, 1], 'metallicFactor': 0.8, 'roughnessFactor': 0.55},
    'vrrevolver_flash': {'baseColorMap': ART + 'revolver_flash.png', 'emissiveMap': ART + 'revolver_flash.png',
                         'emissiveFactor': [6, 4, 1.6], 'metallicFactor': 0.0, 'roughnessFactor': 1.0},
}
FLASH_FLAGS = {'translucent': True, 'translucentBlendOp': 'AddAlpha', 'translucentZWrite': False,
               'doubleSided': True, 'castShadows': False}


def write_materials():
    import json
    out = {}
    for name, stage in MATERIALS.items():
        m = {'name': name, 'mapTo': name, 'class': 'Material', 'version': 1.5,
             'Stages': [stage, {}, {}, {}], 'activeLayers': 1}
        if name == 'vrrevolver_flash':
            m.update(FLASH_FLAGS)
        out[name] = m
    (OUT / 'main.materials.json').write_text(json.dumps(out, indent=2) + '\n')

# ------------------------------------------------------------- COLLADA ---

def fmt(values):
    return ' '.join('%.6g' % v for v in values)


def write_dae(path, objects, pivot):
    """Minimal COLLADA 1.4.1, Z up, metres: one node and geometry per object."""
    shift = Matrix.Translation(-Vector(pivot))
    geoms, nodes, mats = [], [], set()
    for ob in objects:
        me = ob.data
        me.transform(shift)
        me.calc_loop_triangles()
        name = ob.name.replace('.', '_')
        pos = [c for v in me.vertices for c in v.co]
        normals = [c for cn in me.corner_normals for c in cn.vector]
        uv_layer = me.uv_layers.get('UVMap') or (me.uv_layers[0] if me.uv_layers else None)
        uvs = [c for d in uv_layer.data for c in d.uv] if uv_layer else [0, 0] * len(me.loops)
        by_mat = {}
        for tri in me.loop_triangles:
            by_mat.setdefault(tri.material_index, []).append(tri)
        tri_xml = []
        for mi, tris in sorted(by_mat.items()):
            mat = me.materials[mi].name
            mats.add(mat)
            idx = []
            for t in tris:
                for li in t.loops:
                    idx += [me.loops[li].vertex_index, li, li]
            tri_xml.append(
                f'<triangles material="{mat}" count="{len(tris)}">'
                f'<input semantic="VERTEX" source="#{name}-verts" offset="0"/>'
                f'<input semantic="NORMAL" source="#{name}-normals" offset="1"/>'
                f'<input semantic="TEXCOORD" source="#{name}-uv" offset="2" set="0"/>'
                f'<p>{" ".join(map(str, idx))}</p></triangles>')

        def source(sid, data, params):
            n = len(params)
            p = ''.join(f'<param name="{x}" type="float"/>' for x in params)
            return (f'<source id="{name}-{sid}"><float_array id="{name}-{sid}-array" count="{len(data)}">'
                    f'{fmt(data)}</float_array><technique_common><accessor source="#{name}-{sid}-array" '
                    f'count="{len(data) // n}" stride="{n}">{p}</accessor></technique_common></source>')
        geoms.append(
            f'<geometry id="{name}-mesh" name="{name}"><mesh>'
            + source('positions', pos, 'XYZ') + source('normals', normals, 'XYZ') + source('uv', uvs, 'ST')
            + f'<vertices id="{name}-verts"><input semantic="POSITION" source="#{name}-positions"/></vertices>'
            + ''.join(tri_xml) + '</mesh></geometry>')
        binds = ''.join(f'<instance_material symbol="{m}" target="#{m}"/>' for m in sorted(
            {me.materials[mi].name for mi in by_mat}))
        nodes.append(f'<node id="{name}" name="{name}" type="NODE"><instance_geometry url="#{name}-mesh" name="{name}">'
                     f'<bind_material><technique_common>{binds}</technique_common></bind_material>'
                     '</instance_geometry></node>')
    effects = ''.join(f'<effect id="{m}-effect"><profile_COMMON><technique sid="common"><lambert><diffuse>'
                      f'<color sid="diffuse">0.8 0.8 0.8 1</color></diffuse></lambert></technique></profile_COMMON></effect>'
                      for m in sorted(mats))
    materials = ''.join(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-effect"/></material>' for m in sorted(mats))
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
           '<asset><contributor><authoring_tool>build_revolver.py</authoring_tool></contributor>'
           '<unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>'
           f'<library_effects>{effects}</library_effects><library_materials>{materials}</library_materials>'
           f'<library_geometries>{"".join(geoms)}</library_geometries>'
           '<library_visual_scenes><visual_scene id="Scene" name="Scene">'
           f'{"".join(nodes)}</visual_scene></library_visual_scenes>'
           '<scene><instance_visual_scene url="#Scene"/></scene></COLLADA>\n')
    path.write_text(xml)
    tris = sum(len(o.data.loop_triangles) for o in objects)
    print(f'{path.name}: {tris} triangles')
    me_shift_back = Matrix.Translation(Vector(pivot))
    for ob in objects:
        ob.data.transform(me_shift_back)

# ----------------------------------------------------------------- main ---

def build():
    reset()
    OUT.mkdir(parents=True, exist_ok=True)
    return {
        'frame': build_frame(),
        'cylinder': build_cylinder(),
        'hammer': build_hammer(),
        'trigger': build_trigger(),
        'flash': build_flash(),
    }


def main():
    parts = build()
    for name, obs in parts.items():
        write_dae(OUT / f'{name}.dae', obs, PIVOTS[name])
    make_textures()
    write_materials()


if __name__ == '__main__':
    main()
