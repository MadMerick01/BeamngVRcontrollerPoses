"""Builds the VR magnum pistol model and writes it into the mod.

A big gas-operated .50-calibre semi-automatic in the style of the classic
"hand cannon": a 6" barrel with a sight rail on top, a short slide with slanted
rear serrations, ring hammer, squared trigger guard and wrap-around pebbled
grips. Modelled from scratch here, with no maker's markings or logos, so the
mod owns the asset outright. Moving parts are separate shapes, each with its
origin on its own pivot, so vrPistol.lua can animate them:

  frame.dae     frame, barrel, rail, front sight, trigger guard, grips, magazine
  slide.dae     slide, rear sight, safety levers; moves back along -Y when it fires
  hammer.dae    origin on the hammer pivot, cocks about +X
  trigger.dae   origin on the trigger pivot, pulls about +X

Gun space matches the revolver's (tools/revolverModel/build_revolver.py): +X
right, +Y along the barrel, +Z up, metres, bore on the Y axis. The whole gun is
shifted along Y so its grip sits where the revolver's grip sits, so the same
vrPistol modelOffset fits both. PIVOTS gives each part's origin in gun space.

Run:  pip install bpy pillow numpy  &&  python tools/magnumModel/build_magnum.py
      python tools/revolverModel/build_lowpoly.py magnum
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'revolverModel'))
import build_revolver as br  # noqa: E402  (shared Blender helpers and the COLLADA writer)
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix  # noqa: E402

OUT = ROOT / 'mod' / 'art' / 'shapes' / 'vrmagnum'
ART = '/art/shapes/vrmagnum/'

SHIFT = 0.012          # moves the grip onto the revolver's grip position
SLIDE_REAR, SLIDE_FRONT = -0.078, 0.052
SLIDE_TOP, SLIDE_BOTTOM, SLIDE_HALF = 0.0165, -0.0125, 0.0150
MUZZLE = 0.192
FRAME_FRONT = 0.178
BARREL_HALF = 0.0135
BORE_R = 0.0064        # .50 calibre

GRIP_UV = (-0.100, -0.140, 0.110)  # grip texture: (y, z) of the UV origin and size in metres (before SHIFT)

# Authored pivots (before SHIFT).
_PIVOTS = {
    'frame': (0.0, 0.0, 0.0),
    'slide': (0.0, 0.0, 0.0),
    'hammer': (0.0, -0.0790, -0.0200),
    'trigger': (0.0, 0.0285, -0.0300),
}
PIVOTS = {k: (x, y + SHIFT, z) for k, (x, y, z) in _PIVOTS.items()}

profile, cylinder, box, boolean, bevel, join = br.profile, br.cylinder, br.box, br.boolean, br.bevel, br.join
smooth, set_material, box_uv, project_uv = br.smooth, br.set_material, br.box_uv, br.project_uv
smooth_outline = br.smooth_outline


def prism(name, pts_xz, y0, y1):
    """Extrudes a cross-section (X, Z) along Y from y0 to y1."""
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y0, z)) for x, z in pts_xz])
    bmesh.ops.recalc_face_normals(bm, faces=[face])
    if face.normal.y > 0:
        face.normal_flip()
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    verts = [v for v in ext['geom'] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=verts, vec=(0, y1 - y0, 0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return br.mesh_object(name, bm)


def mirrored(make):
    """Builds a part on the right (+X) side and a mirror copy on the left."""
    a, b = make(1), make(-1)
    return [a, b]

# ----------------------------------------------------------------- parts ---

def build_frame():
    parts = []

    # Side outline: dust cover under the barrel, trigger area, raked grip, short tang.
    outline = [
        (FRAME_FRONT, -0.0120), (FRAME_FRONT, -0.0300), (0.060, -0.0320), (0.000, -0.0330),
        (-0.0130, -0.0360), (-0.0190, -0.0600), (-0.0265, -0.0900), (-0.0330, -0.1180),
        (-0.0370, -0.1235), (-0.0850, -0.1235), (-0.0885, -0.1185),
        (-0.0805, -0.0900), (-0.0715, -0.0560), (-0.0680, -0.0360),
        (-0.0740, -0.0275), (-0.0815, -0.0245), (-0.0835, -0.0200), (-0.0800, -0.0125),
        (0.040, -0.0125),
    ]
    sharp = (0, 1, 8, 9, 18)
    frame = profile('frame', smooth_outline(outline, 2, sharp=sharp), 0.026)
    # Hammer slot at the rear and the trigger slot through the bottom.
    frame = boolean(frame, box('hammerSlot', (-0.0048, 0.0048), (-0.095, -0.060), (-0.034, 0.0)))
    frame = boolean(frame, box('triggerSlot', (-0.0040, 0.0040), (0.018, 0.042), (-0.045, -0.024)))
    # Slide rails: a shallow step along the top of the frame sides.
    frame = boolean(frame, box('railCutR', (0.0115, 0.02), (-0.081, 0.045), (-0.0175, -0.0110)))
    frame = boolean(frame, box('railCutL', (-0.02, -0.0115), (-0.081, 0.045), (-0.0175, -0.0110)))
    br.bevel(frame, 0.0010, 2)
    parts.append(frame)

    # Barrel: tall flat-sided block with a sight rail on top.
    h = BARREL_HALF
    section = [(-0.0115, -0.0115), (0.0115, -0.0115), (h, -0.0040), (h, 0.0085), (0.0085, 0.0135),
               (0.0060, 0.0135), (0.0060, SLIDE_TOP), (-0.0060, SLIDE_TOP), (-0.0060, 0.0135),
               (-0.0085, 0.0135), (-h, 0.0085), (-h, -0.0040)]
    barrel = prism('barrel', section, SLIDE_FRONT - 0.002, MUZZLE)
    # Cross slots in the rail.
    slots = [box('slot', (-0.01, 0.01), (y, y + 0.0035), (0.0148, 0.02)) for y in np.arange(0.064, 0.176, 0.010)]
    barrel = boolean(barrel, join(slots, 'slots'))
    # Recessed side panel along the barrel flats.
    for side in (1, -1):
        x = (h - 0.0004, h + 0.01) if side > 0 else (-h - 0.01, -h + 0.0004)
        barrel = boolean(barrel, box('panel', x, (0.070, 0.176), (-0.0010, 0.0060)))
    barrel = boolean(barrel, cylinder('bore', BORE_R, MUZZLE - 0.05, MUZZLE + 0.01, segs=32))
    br.bevel(barrel, 0.0007, 2)
    # Chamber end of the barrel, hidden in the slide until it blows back.
    hood = cylinder('hood', 0.0105, -0.034, SLIDE_FRONT, segs=48)
    hood = boolean(hood, box('hoodTop', (-0.0075, 0.0075), (-0.034, SLIDE_FRONT), (0.0, 0.0125)), 'UNION')
    br.bevel(hood, 0.0006, 1)
    parts += [barrel, hood]

    # Bore liner, so the muzzle reads as a dark hole.
    liner = cylinder('liner', BORE_R - 0.0001, MUZZLE - 0.045, MUZZLE - 0.004, segs=32)
    set_material(liner, 'vrmagnum_bore')

    # Front sight blade at the muzzle end of the rail.
    sight = profile('frontSight', [(0.178, SLIDE_TOP), (0.183, 0.0232), (0.1895, 0.0232), (0.1895, SLIDE_TOP)], 0.0036)
    br.bevel(sight, 0.0004, 1)
    parts.append(sight)

    # Squared-off trigger guard.
    guard = br.tube_curve('guard', [
        (0, 0.0560, -0.0300), (0, 0.0575, -0.0500), (0, 0.0530, -0.0625), (0, 0.0350, -0.0665),
        (0, 0.0080, -0.0650), (0, -0.0060, -0.0570), (0, -0.0150, -0.0440)], 0.0034, scale_x=2.0)
    parts.append(guard)

    # Slide stop lever (left), takedown lever (right) and frame pins.
    stop = profile('slideStop', smooth_outline([(-0.002, -0.0205), (0.024, -0.0205), (0.030, -0.0170),
                                                 (0.024, -0.0140), (-0.002, -0.0140)], 1), 0.0016, x0=-0.0146)
    take = profile('takedown', smooth_outline([(0.040, -0.0230), (0.050, -0.0230), (0.052, -0.0180),
                                                (0.040, -0.0170)], 1), 0.0016, x0=0.0130)
    parts += [stop, take]
    for y, z in [(-0.004, -0.0285), (0.012, -0.0175), (0.045, -0.0205), (-0.0790, -0.0200)]:
        pin = cylinder('pin', 0.0019, -0.0137, 0.0137, center=(y, z), axis='X', segs=16)
        parts.append(pin)

    # Magazine floor plate.
    plate = profile('magPlate', smooth_outline([(-0.0345, -0.1225), (-0.0320, -0.1300), (-0.0365, -0.1345),
                                                (-0.0870, -0.1345), (-0.0880, -0.1225)], 1, sharp=(0, 4)), 0.0235)
    br.bevel(plate, 0.0008, 2)
    parts.append(plate)

    steel = join(parts, 'frameSteel')
    smooth(steel, 35)
    set_material(steel, 'vrmagnum_black')
    box_uv(steel, 0.05)

    grip = build_grips()
    for ob in (liner,):
        smooth(ob)
        box_uv(ob, 0.05)
    return [steel, grip, liner]


def build_grips():
    """Wrap-around pebbled panels on both sides of the grip, slightly proud of the frame."""
    outline = [(-0.0155, -0.0380), (-0.0205, -0.0620), (-0.0275, -0.0920), (-0.0320, -0.1160),
               (-0.0855, -0.1160), (-0.0775, -0.0880), (-0.0690, -0.0560), (-0.0660, -0.0390),
               (-0.0560, -0.0330), (-0.0280, -0.0330)]
    panels = []
    for side in (1, -1):
        p = profile('gripPanel', smooth_outline(outline, 3), 0.0055, x0=0.0105 if side > 0 else -0.0160)
        br.bevel(p, 0.0022, 3, angle=30)
        panels.append(p)
    grip = join(panels, 'grip')
    smooth(grip, 50)
    set_material(grip, 'vrmagnum_grip')
    y0, z0, size = GRIP_UV
    project_uv(grip, (0, 1, 0), (0, 0, 1), size, offset=(-y0 / size, -z0 / size))
    return grip


def build_slide():
    s = SLIDE_HALF
    section = [(-s, SLIDE_BOTTOM), (s, SLIDE_BOTTOM), (s, 0.0080), (0.0100, SLIDE_TOP),
               (-0.0100, SLIDE_TOP), (-s, 0.0080)]
    slide = prism('slide', section, SLIDE_REAR, SLIDE_FRONT)
    # Rear face raked forward at the top, like the original's.
    slide = boolean(slide, box('rake', (-0.03, 0.03), (SLIDE_REAR - 0.02, SLIDE_REAR), (-0.03, 0.03)))
    rake = box('rakeWedge', (-0.03, 0.03), (-0.004, 0.004), (-0.04, 0.04))
    rake.data.transform(Matrix.Translation((0, SLIDE_REAR, 0)) @ Matrix.Rotation(math.radians(-14), 4, 'X'))
    rake.data.transform(Matrix.Translation((0, -0.004, 0)))
    slide = boolean(slide, rake)
    # Slanted grip serrations on both sides of the rear half.
    grooves = []
    for i in range(12):
        y = -0.0705 + i * 0.0040
        for side in (1, -1):
            g = box('groove', (-0.0012, 0.0012), (-0.0007, 0.0007), (-0.0100, 0.0100))
            g.data.transform(Matrix.Translation((side * (s + 0.0004), y, -0.0010)) @
                             Matrix.Rotation(math.radians(-16), 4, 'X'))
            grooves.append(g)
    slide = boolean(slide, join(grooves, 'grooves'))
    # Ejection port on the right.
    slide = boolean(slide, box('port', (0.0110, 0.02), (-0.004, 0.030), (-0.0020, 0.0125)))
    br.bevel(slide, 0.0007, 2)

    # Rear sight: square-notch blade on a dovetail base.
    rear = box('rearBase', (-0.0095, 0.0095), (-0.0710, -0.0600), (SLIDE_TOP - 0.001, SLIDE_TOP + 0.0030))
    blade = box('rearBlade', (-0.0095, 0.0095), (-0.0710, -0.0665), (SLIDE_TOP, SLIDE_TOP + 0.0070))
    rear = boolean(rear, blade, 'UNION')
    rear = boolean(rear, box('notch', (-0.0016, 0.0016), (-0.08, -0.06), (SLIDE_TOP + 0.0030, 0.03)))
    br.bevel(rear, 0.0004, 1)

    # Ambidextrous safety levers: round pivot boss and a thumb lever swept back.
    levers = []
    for side in (1, -1):
        x0 = s - 0.0003 if side > 0 else -s - 0.0022
        boss = cylinder('boss', 0.0050, x0, x0 + 0.0025, center=(-0.0610, 0.0040), axis='X', segs=32)
        lever = profile('lever', smooth_outline([(-0.0640, 0.0020), (-0.0560, 0.0075), (-0.0480, 0.0105),
                                                 (-0.0470, 0.0085), (-0.0560, 0.0030)], 2), 0.0020,
                        x0=x0 + (0.0012 if side > 0 else -0.0007))
        levers += [boss, lever]
    port_dark = box('portBack', (0.0108, 0.0112), (-0.004, 0.030), (-0.0020, 0.0125))
    steel = join([slide, rear] + levers, 'slideSteel')
    smooth(steel, 35)
    set_material(steel, 'vrmagnum_black')
    box_uv(steel, 0.05)

    # Red fire dot under the left safety, and brass in the ejection port.
    dot = cylinder('dot', 0.0013, -s - 0.0002, -s + 0.0002, center=(-0.0545, -0.0040), axis='X', segs=16)
    set_material(dot, 'vrmagnum_red')
    case = cylinder('case', 0.0068, -0.002, 0.028, center=(0.0035, 0.0010), segs=32)
    set_material(case, 'vrmagnum_brass')
    port_dark.name = 'portBack'
    set_material(port_dark, 'vrmagnum_bore')
    for ob in (dot, case, port_dark):
        smooth(ob)
        box_uv(ob, 0.05)
    return [steel, dot, case, port_dark]


def build_hammer():
    px, py, pz = _PIVOTS['hammer']
    # Down (fired) position: a neck from the pivot up behind the slide into a ring spur.
    ring_c = (py - 0.0095, pz + 0.0225)
    neck = profile('neck', [(py + 0.0035, pz - 0.0040), (py + 0.0030, pz + 0.0040), (py + 0.0008, pz + 0.0075),
                            (ring_c[0] + 0.0040, ring_c[1] - 0.0010), (ring_c[0] - 0.0040, ring_c[1] - 0.0030),
                            (py - 0.0060, pz - 0.0020), (py - 0.0040, pz - 0.0050)], 0.0080)
    ring = cylinder('ring', 0.0072, -0.0040, 0.0040, center=ring_c, axis='X', segs=40)
    ring = boolean(ring, cylinder('hole', 0.0036, -0.01, 0.01, center=ring_c, axis='X', segs=32))
    body = boolean(neck, ring, 'UNION')
    br.bevel(body, 0.0006, 2)
    pin = cylinder('hpin', 0.0024, -0.0045, 0.0045, center=(py, pz), axis='X', segs=20)
    ob = join([body, pin], 'hammer')
    smooth(ob, 40)
    set_material(ob, 'vrmagnum_black')
    box_uv(ob, 0.03)
    return [ob]


def build_trigger():
    px, py, pz = _PIVOTS['trigger']
    # Curved blade, tip curling forward.
    outline = [(-0.0030, 0.0030), (0.0035, 0.0030), (0.0025, -0.0080), (0.0035, -0.0170), (0.0070, -0.0240),
               (0.0090, -0.0262), (0.0050, -0.0270), (0.0005, -0.0215), (-0.0025, -0.0120), (-0.0035, -0.0030)]
    blade = profile('trigger', [(y + py, z + pz) for y, z in smooth_outline(outline, 2, sharp=(0, 1))], 0.0068)
    br.bevel(blade, 0.0009, 2)
    smooth(blade, 45)
    set_material(blade, 'vrmagnum_black')
    box_uv(blade, 0.03)
    return [blade]

# -------------------------------------------------------------- textures ---

def grip_mask(n):
    """Where the pebbling goes on the grip panels, in their side-on UV space."""
    from PIL import Image, ImageDraw
    img = Image.new('L', (n, n), 0)
    d = ImageDraw.Draw(img)
    y0, z0, size = GRIP_UV

    def px(y, z):  # image row 0 is v = 1
        return ((y - y0) / size * n, (1 - (z - z0) / size) * n)
    d.polygon([px(-0.0195, -0.0420), px(-0.0310, -0.1110), px(-0.0815, -0.1110),
               px(-0.0650, -0.0420), px(-0.0560, -0.0375), px(-0.0290, -0.0375)], fill=255)
    return np.asarray(img, dtype=float) / 255


def make_textures():
    n = 1024
    rng = np.random.default_rng(5)
    # Pebble grain: a field of small random bumps (Worley-style cells) on hard black rubber.
    cells = 5200
    pts = rng.random((cells, 2)) * n
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    dmin = np.full((n, n), 1e9, dtype=np.float32)
    cell = 32
    grid = {}
    for i, (x, y) in enumerate(pts):
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y))
    for (cx, cy), plist in grid.items():
        for x, y in plist:
            x0, x1 = int(max(0, x - 10)), int(min(n, x + 10))
            y0, y1 = int(max(0, y - 10)), int(min(n, y + 10))
            d = np.sqrt((xx[y0:y1, x0:x1] - x) ** 2 + (yy[y0:y1, x0:x1] - y) ** 2)
            dmin[y0:y1, x0:x1] = np.minimum(dmin[y0:y1, x0:x1], d)
    bump = np.clip(1 - dmin / 7.5, 0, 1) ** 0.6
    mask = grip_mask(n)
    height = bump * mask
    shade = 0.045 + 0.02 * br.noise2d((n, n), 8, 11) + 0.018 * height
    base = np.stack([shade, shade * 1.02, shade * 1.06], -1)
    br.save_png(OUT / 'magnum_grip_baseColor.png', base)
    gy, gx = np.gradient(height)
    strength = 3.0
    nx, ny, nz = -gx * strength, gy * strength, np.ones_like(height)
    length = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    normal = np.stack([nx / length, ny / length, nz / length], -1) * 0.5 + 0.5
    br.save_png(OUT / 'magnum_grip_normal.png', normal)


MATERIALS = {
    'vrmagnum_black': {'baseColorFactor': [0.030, 0.031, 0.034, 1], 'metallicFactor': 0.85, 'roughnessFactor': 0.40},
    'vrmagnum_grip': {'baseColorMap': ART + 'magnum_grip_baseColor.png', 'normalMap': ART + 'magnum_grip_normal.png',
                      'metallicFactor': 0.0, 'roughnessFactor': 0.78},
    'vrmagnum_bore': {'baseColorFactor': [0.01, 0.01, 0.01, 1], 'metallicFactor': 0.6, 'roughnessFactor': 0.7},
    'vrmagnum_red': {'baseColorFactor': [0.75, 0.04, 0.03, 1], 'metallicFactor': 0.0, 'roughnessFactor': 0.35},
    'vrmagnum_brass': {'baseColorFactor': [0.78, 0.58, 0.27, 1], 'metallicFactor': 1.0, 'roughnessFactor': 0.3},
}


def write_materials():
    out = {name: {'name': name, 'mapTo': name, 'class': 'Material', 'version': 1.5,
                  'Stages': [stage, {}, {}, {}], 'activeLayers': 1} for name, stage in MATERIALS.items()}
    (OUT / 'main.materials.json').write_text(json.dumps(out, indent=2) + '\n')

# ----------------------------------------------------------------- main ---

def build():
    br.reset()
    OUT.mkdir(parents=True, exist_ok=True)
    parts = {
        'frame': build_frame(),
        'slide': build_slide(),
        'hammer': build_hammer(),
        'trigger': build_trigger(),
    }
    move = Matrix.Translation((0, SHIFT, 0))
    for obs in parts.values():
        for ob in obs:
            ob.data.transform(move)
    return parts


def main():
    parts = build()
    for name, obs in parts.items():
        br.write_dae(OUT / f'{name}.dae', obs, PIVOTS[name])
    make_textures()
    write_materials()
    if '--blend' in sys.argv:
        bpy.ops.wm.save_as_mainfile(filepath=sys.argv[sys.argv.index('--blend') + 1])


if __name__ == '__main__':
    main()
