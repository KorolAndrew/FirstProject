# Yanochka 3D - part 3: IV stand, room, light, cameras, render, save
cyl('Pole', (0, 0, 0.11), (0, 0, 2.36), 0.0125, steel)
cyl('PoleJoint', (0, 0, 1.30), (0, 0, 1.36), 0.017, steel)
cyl('PoleTip', (0, 0, 2.36), (0, 0, 2.43), 0.006, steel)
cyl('TopBar', (-0.16, 0, 2.30), (0.16, 0, 2.30), 0.009, steel)
for s in (-1, 1):
    curve('Hook_%d' % s, [(s * 0.16, 0, 2.30), (s * 0.2, 0, 2.33), (s * 0.215, 0, 2.38), (s * 0.19, 0, 2.415), (s * 0.165, 0, 2.40)], 0.0075, steel, kind='NURBS')
cyl('Hub', (0, 0, 0.09), (0, 0, 0.14), 0.03, steel)
for i in range(5):
    a = math.radians(90 + 72 * i)
    end = Vector((0.3 * math.cos(a), 0.3 * math.sin(a), 0.085))
    cyl('Leg_%d' % i, (0, 0, 0.11), end, 0.011, steel)
    cyl('Fork_%d' % i, end, end + Vector((0, 0, -0.03)), 0.006, steel)
    cyl('Wheel_%d' % i, end + Vector((0, -0.012, -0.05)), end + Vector((0, 0.012, -0.05)), 0.028, rubber)

bag = box('IVBag', (0.165, 0.0, 2.185), (0.15, 0.056, 0.23), bagm)
md = bag.modifiers.new('Bevel', 'BEVEL'); md.width = 0.02; md.segments = 4
md = bag.modifiers.new('Sub', 'SUBSURF'); md.levels = 2; md.render_levels = 2
torus('BagLoop', (0.165, 0.0, 2.315), 0.014, 0.003, steel, rot=ZUP.rotation_difference(Vector((0, 1, 0))))
box('CrossH', (0.165, -0.03, 2.2), (0.05, 0.004, 0.014), crossm)
box('CrossV', (0.165, -0.03, 2.2), (0.014, 0.004, 0.05), crossm)
cyl('DripChamber', (0.165, 0, 2.07), (0.165, 0, 1.99), 0.011, tubem)
curve('Tube', [(0.165, 0, 1.99), (0.168, -0.005, 1.8), (0.175, -0.015, 1.5), (0.17, -0.03, 1.2), (0.14, -0.06, 0.98), (0.07, -0.12, 0.86), (-0.02, -0.15, 0.86), (-0.075, -0.13, 0.885)], 0.0045, tubem, kind='NURBS')
sphere('Tape', (-0.075, -0.125, 0.886), 0.034, scale=(1.0, 1.0, 0.6), m=tapem)

fbm = bmesh.new()
bmesh.ops.create_grid(fbm, x_segments=1, y_segments=1, size=5.0)
floor = finish('Floor', fbm)
fm = bpy.data.materials.get('Tiles') or bpy.data.materials.new('Tiles')
try:
    fm.use_nodes = True
except Exception:
    pass
nt = fm.node_tree
fb = bsdf_of(fm)
br = nt.nodes.new('ShaderNodeTexBrick')
tc = nt.nodes.new('ShaderNodeTexCoord')
nt.links.new(tc.outputs['Object'], br.inputs['Vector'])
for k, v in (('Color1', (0.07, 0.05, 0.04, 1)), ('Color2', (0.04, 0.034, 0.03, 1)), ('Mortar', (0.006, 0.005, 0.005, 1)), ('Scale', 3.0), ('Mortar Size', 0.012), ('Brick Width', 1.0), ('Row Height', 1.0)):
    sk = br.inputs.get(k)
    if sk is not None:
        sk.default_value = v
nt.links.new(br.outputs['Color'], fb.inputs['Base Color'])
setin(fb, ['Roughness'], 0.5)
floor.data.materials.append(fm)

w = sc.world or bpy.data.worlds.new('World')
sc.world = w
try:
    w.use_nodes = True
except Exception:
    pass
wn = w.node_tree
bg = next((n for n in wn.nodes if n.type == 'BACKGROUND'), None) or wn.nodes.new('ShaderNodeBackground')
wo = next((n for n in wn.nodes if n.type == 'OUTPUT_WORLD'), None) or wn.nodes.new('ShaderNodeOutputWorld')
wn.links.new(bg.outputs['Background'], wo.inputs['Surface'])
bg.inputs['Color'].default_value = (0.004, 0.003, 0.003, 1)
bg.inputs['Strength'].default_value = 1.0
vs = wn.nodes.new('ShaderNodeVolumeScatter')
vs.inputs['Density'].default_value = 0.035
an = vs.inputs.get('Anisotropy')
if an is not None:
    an.default_value = 0.35
wn.links.new(vs.outputs['Volume'], wo.inputs['Volume'])

def add_light(name, kind, loc, energy, color, target=None, **kw):
    ld = bpy.data.lights.new(name, kind)
    ld.energy = energy
    ld.color = color
    for k, v in kw.items():
        try:
            setattr(ld, k, v)
        except Exception:
            pass
    o = link(bpy.data.objects.new(name, ld))
    o.location = loc
    if target is not None:
        o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    return o

add_light('KeySpot', 'SPOT', (-0.05, -0.15, 4.3), 2200, (1.0, 0.9, 0.78), target=(-0.08, 0, 0.9), spot_size=math.radians(32), spot_blend=0.55, shadow_soft_size=0.15)
add_light('BagGlow', 'POINT', (0.2, -0.1, 2.15), 25, (1.0, 0.35, 0.6), shadow_soft_size=0.05)
add_light('FaceFill', 'AREA', (-0.9, -2.2, 1.5), 40, (0.85, 0.85, 1.0), target=H, size=1.0)
add_light('Rim', 'POINT', (-0.6, 0.8, 1.6), 30, (1.0, 0.8, 0.85))

dbm = bmesh.new()
for i in range(70):
    h = random.uniform(0.3, 3.2)
    rad = 0.1 + 0.32 * (1 - h / 4.3)
    a = random.uniform(0, 6.28)
    rr = rad * math.sqrt(random.random())
    bmesh.ops.create_uvsphere(dbm, u_segments=6, v_segments=4, radius=random.uniform(0.002, 0.005), matrix=Matrix.Translation((-0.06 + rr * math.cos(a), -0.12 + rr * math.sin(a), h)))
finish('Dust', dbm, mat('DustGlow', (1, 0.9, 0.8), emit=(1.0, 0.85, 0.7), es=6.0))

def add_cam(name, loc, target, lens):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    o = link(bpy.data.objects.new(name, cd))
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    return o

cam = add_cam('Cam_Full', (0.15, -4.9, 1.75), (-0.02, 0, 1.18), 58)
add_cam('Cam_Close', (-0.05, -1.75, 1.2), (-0.1, 0, 1.0), 50)
sc.camera = cam

r = sc.render
for eng in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
    try:
        r.engine = eng
        break
    except TypeError:
        pass
r.resolution_x = 900
r.resolution_y = 1200
r.resolution_percentage = 100
try:
    sc.eevee.taa_render_samples = 64
except Exception:
    pass
out_dir = 'C:/Users/anbre/NotionAgentWorkspace/blender/yanochka'
os.makedirs(out_dir, exist_ok=True)
saved = 'no'
try:
    bpy.ops.wm.save_as_mainfile(filepath=out_dir + '/yanochka.blend')
    saved = 'yes'
except Exception as ex:
    saved = 'error: %s' % ex
print('part3 ok | engine', r.engine, '| objects', len(col.objects), '| saved', saved)
