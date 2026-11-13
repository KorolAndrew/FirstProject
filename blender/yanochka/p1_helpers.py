# Yanochka 3D - part 1: setup, helpers, materials, head
import bpy, bmesh, math, random, os
from mathutils import Vector, Matrix

random.seed(7)
sc = bpy.context.scene
for n in ('Cube', 'Light', 'Camera'):
    o = bpy.data.objects.get(n)
    if o is not None:
        bpy.data.objects.remove(o, do_unlink=True)
old = bpy.data.collections.get('Yanochka')
if old is not None:
    for o in list(old.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections.remove(old)
col = bpy.data.collections.new('Yanochka')
sc.collection.children.link(col)
ZUP = Vector((0, 0, 1))

def bsdf_of(m):
    return next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')

def setin(b, names, val):
    for nm in names:
        s = b.inputs.get(nm)
        if s is not None:
            s.default_value = val
            return True
    return False

def mat(name, color, rough=0.5, metal=0.0, emit=None, es=0.0, coat=0.0, sss=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    b = bsdf_of(m)
    setin(b, ['Base Color'], (color[0], color[1], color[2], 1.0))
    setin(b, ['Roughness'], rough)
    setin(b, ['Metallic'], metal)
    if coat:
        setin(b, ['Coat Weight', 'Clearcoat'], coat)
    if sss:
        setin(b, ['Subsurface Weight', 'Subsurface'], sss)
    if emit is not None:
        setin(b, ['Emission Color', 'Emission'], (emit[0], emit[1], emit[2], 1.0))
        setin(b, ['Emission Strength'], es)
    m.diffuse_color = (color[0], color[1], color[2], 1.0)
    return m

def link(o):
    col.objects.link(o)
    return o

def finish(name, bm, m=None, only_sides=False):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = (abs(p.normal.z) < 0.5) if only_sides else True
    o = link(bpy.data.objects.new(name, me))
    if m is not None:
        me.materials.append(m)
    return o

def sphere(name, loc, r, scale=(1, 1, 1), m=None, seg=32, ring=16, rot=None):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=ring, radius=r)
    o = finish(name, bm, m)
    o.location = loc
    o.scale = scale
    if rot is not None:
        o.rotation_mode = 'QUATERNION'
        o.rotation_quaternion = rot
    return o

def limb(name, a, b, r, m):
    a = Vector(a); b = Vector(b); d = b - a
    return sphere(name, (a + b) / 2, 1.0, scale=(r, r, d.length / 2 + r * 0.6), m=m,
                  rot=ZUP.rotation_difference(d.normalized()))

def cyl(name, a, b, r, m, seg=24):
    a = Vector(a); b = Vector(b); d = b - a
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg, radius1=r, radius2=r, depth=d.length)
    o = finish(name, bm, m, only_sides=True)
    o.location = (a + b) / 2
    o.rotation_mode = 'QUATERNION'
    o.rotation_quaternion = ZUP.rotation_difference(d.normalized())
    return o

def box(name, loc, size, m):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((size[0], size[1], size[2], 1.0)))
    o = finish(name, bm, m)
    o.location = loc
    return o

def torus(name, loc, R, r, m, rot=None, segs=40, rsegs=12):
    bm = bmesh.new()
    rows = []
    for i in range(segs):
        u = 2 * math.pi * i / segs
        row = []
        for j in range(rsegs):
            v = 2 * math.pi * j / rsegs
            row.append(bm.verts.new(((R + r * math.cos(v)) * math.cos(u), (R + r * math.cos(v)) * math.sin(u), r * math.sin(v))))
        rows.append(row)
    for i in range(segs):
        for j in range(rsegs):
            bm.faces.new((rows[i][j], rows[(i + 1) % segs][j], rows[(i + 1) % segs][(j + 1) % rsegs], rows[i][(j + 1) % rsegs]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    o = finish(name, bm, m)
    o.location = loc
    if rot is not None:
        o.rotation_mode = 'QUATERNION'
        o.rotation_quaternion = rot
    return o

def curve(name, pts, bev, m, radii=None, res=4, kind='POLY'):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = bev
    cu.bevel_resolution = res
    cu.resolution_u = 12
    try:
        cu.use_fill_caps = True
    except Exception:
        pass
    sp = cu.splines.new(kind)
    sp.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        sp.points[i].co = (p[0], p[1], p[2], 1.0)
        if radii:
            sp.points[i].radius = radii[i]
    if kind == 'NURBS':
        sp.order_u = 4
        sp.use_endpoint_u = True
    sp.use_smooth = True
    o = link(bpy.data.objects.new(name, cu))
    if m is not None:
        cu.materials.append(m)
    return o

skin = mat('Skin', (1.0, 0.84, 0.78), rough=0.55, sss=0.15)
wool = mat('Wool', (0.94, 0.93, 0.96), rough=0.9)
black = mat('Suit', (0.018, 0.016, 0.02), rough=0.42, coat=0.3)
hornm = mat('Horn', (0.03, 0.026, 0.03), rough=0.28, coat=0.6)
silver = mat('Silver', (0.85, 0.86, 0.9), rough=0.2, metal=1.0)
gold = mat('Gold', (1.0, 0.72, 0.28), rough=0.22, metal=1.0)
eyeb = mat('EyeBlue', (0.22, 0.45, 0.9), rough=0.08, coat=1.0)
pupil = mat('Pupil', (0.02, 0.04, 0.12), rough=0.1)
shine = mat('Shine', (1, 1, 1), rough=0.1, emit=(1, 1, 1), es=4.0)
blush = mat('Blush', (1.0, 0.42, 0.52), rough=0.6)
mouthm = mat('Mouth', (0.45, 0.12, 0.15), rough=0.6)
steel = mat('Steel', (0.8, 0.8, 0.83), rough=0.25, metal=1.0)
rubber = mat('Rubber', (0.05, 0.05, 0.06), rough=0.7)
bagm = mat('IVBag', (1.0, 0.55, 0.72), rough=0.2, emit=(1.0, 0.35, 0.6), es=3.0)
crossm = mat('Cross', (1, 1, 1), rough=0.3, emit=(1, 1, 1), es=2.0)
tubem = mat('Tube', (1.0, 0.6, 0.75), rough=0.15, emit=(1.0, 0.4, 0.6), es=0.6)
tapem = mat('Tape', (0.95, 0.95, 0.92), rough=0.7)

H = Vector((-0.15, -0.05, 1.22))
sphere('Head', H, 0.22, scale=(1.0, 0.94, 0.93), m=skin, seg=48, ring=24)
for s in (-1, 1):
    e = H + Vector((s * 0.08, -0.188, -0.035))
    sphere('Eye_%d' % s, e, 0.052, scale=(0.95, 0.42, 1.15), m=eyeb)
    sphere('Pupil_%d' % s, e + Vector((0, -0.016, -0.005)), 0.03, scale=(0.95, 0.4, 1.15), m=pupil)
    sphere('Shine_%d' % s, e + Vector((0.012, -0.03, 0.022)), 0.011, m=shine, seg=12, ring=8)
    sphere('Blush_%d' % s, H + Vector((s * 0.125, -0.150, -0.085)), 0.036, scale=(1.0, 0.3, 0.6), m=blush)
sphere('Mouth', H + Vector((0.0, -0.172, -0.115)), 0.012, scale=(1.5, 0.4, 0.7), m=mouthm, seg=16, ring=8)
sphere('Nose', H + Vector((0.0, -0.193, -0.075)), 0.007, m=skin, seg=12, ring=8)
print('part1 ok')
