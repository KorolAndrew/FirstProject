# Yanochka 3D - part 4: polish - curly ringlets, bigger horns, light beam, warmer face light
for nm in [o.name for o in col.objects]:
    if nm == 'Hair' or nm.startswith('Horn_') or nm.startswith('Ring_') or nm.startswith('Ear_'):
        bpy.data.objects.remove(bpy.data.objects[nm], do_unlink=True)
setin(bsdf_of(skin), ['Base Color'], (1.0, 0.8, 0.72, 1.0))
random.seed(11)
bm = bmesh.new()
def blob(p, r):
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=r, matrix=Matrix.Translation(p))
ga = math.pi * (3 - math.sqrt(5))
for i in range(360):
    z = 1 - 2 * (i + 0.5) / 360
    rr = math.sqrt(1 - z * z)
    d = Vector((rr * math.cos(ga * i), rr * math.sin(ga * i), z))
    if d.z < -0.35 or (d.y < -0.2 and d.z < 0.45 and abs(d.x) < 0.8) or (d.y < 0 and d.z < -0.05 and abs(d.x) < 0.95):
        continue
    blob(H + d * (0.228 + random.uniform(0.0, 0.025)), random.uniform(0.04, 0.056))
for i in range(15):
    a = math.radians(-78 + 156 * i / 14)
    blob(H + Vector((0.2 * math.sin(a), -0.195 * math.cos(a) + 0.012, 0.118 - 0.06 * abs(math.sin(a)))), random.uniform(0.038, 0.048))
for i in range(70):
    d = Vector((random.uniform(-1, 1), random.uniform(0.15, 1), random.uniform(-0.8, 0.75))).normalized()
    blob(H + Vector((d.x * 0.27, 0.06 + d.y * 0.2, -0.03 + d.z * 0.26)), random.uniform(0.05, 0.07))
hair = finish('Hair', bm, wool)
md = hair.modifiers.new('Sub', 'SUBSURF'); md.levels = 1; md.render_levels = 1
tex = bpy.data.textures.get('WoolNoise') or bpy.data.textures.new('WoolNoise', 'CLOUDS')
tex.noise_scale = 0.02
md = hair.modifiers.new('Fluff', 'DISPLACE'); md.texture = tex; md.strength = 0.015
def ringlet(name, ang, L, rs=0.235, drift=0.08, tpm=14.0):
    a = math.radians(ang)
    radial = Vector((math.cos(a), math.sin(a), 0))
    tang = ZUP.cross(radial)
    start = H + Vector((rs * math.cos(a), 0.9 * rs * math.sin(a) + 0.03, -0.09))
    n = max(24, int(L * tpm * 14))
    ph = random.uniform(0, 6.28)
    pts = []; rad = []
    for i in range(n):
        u = i / (n - 1)
        c = start + radial * (drift * u) + Vector((0, 0, -L * u))
        an = ph + 2 * math.pi * tpm * L * u
        r = 0.03 * (1 - 0.25 * u)
        pts.append(c + radial * (r * math.cos(an)) + tang * (r * math.sin(an)))
        rad.append(1.0 - 0.35 * u)
    return curve(name, pts, 0.024, wool, rad, res=3)
locks = [(140, 0.62), (155, 0.68), (170, 0.72), (185, 0.7), (200, 0.6), (208, 0.5), (60, 0.5), (80, 0.55), (100, 0.58), (120, 0.6), (40, 0.42), (20, 0.35), (-20, 0.3)]
for k, (ang, L) in enumerate(locks):
    ringlet('Hair_Curl_%d' % k, ang + random.uniform(-5, 5), L)
for k, (ang, L) in enumerate([(160, 0.55), (190, 0.5), (110, 0.45)]):
    ringlet('Hair_CurlOut_%d' % k, ang, L, rs=0.28, drift=0.1)
for s in (-1, 1):
    C = H + Vector((s * 0.175, 0.0, 0.06))
    NP = 64
    T = 1.85 * math.pi
    pts = []; rad = []
    for i in range(NP):
        t = T * i / (NP - 1)
        R = 0.12 * (1 - 0.5 * t / T)
        pts.append(C + Vector((s * (0.02 + 0.03 * t), R * math.sin(t), R * math.cos(t))))
        rad.append(1.0 - 0.7 * t / T)
    curve('Horn_%d' % s, pts, 0.058, hornm, rad, res=6)
    for tt in (0.42, 0.6):
        i = int(tt * (NP - 1))
        tan = (pts[i + 1] - pts[i - 1]).normalized()
        torus('Ring_%d_%d' % (s, int(tt * 100)), pts[i], 0.058 * rad[i] + 0.007, 0.005, silver, rot=ZUP.rotation_difference(tan))
    e = sphere('Ear_%d' % s, H + Vector((s * 0.27, -0.07, -0.12)), 1.0, scale=(0.085, 0.03, 0.042), m=wool)
    e.rotation_euler = (0.0, s * math.radians(35), s * math.radians(-20))
wn = sc.world.node_tree
wo = next(n for n in wn.nodes if n.type == 'OUTPUT_WORLD')
for l in list(wn.links):
    if l.to_node == wo and l.to_socket.name == 'Volume':
        wn.links.remove(l)
for n in [n for n in wn.nodes if n.type == 'VOLUME_SCATTER']:
    wn.nodes.remove(n)
next(n for n in wn.nodes if n.type == 'BACKGROUND').inputs['Color'].default_value = (0.002, 0.0016, 0.0016, 1)
bmb = bmesh.new()
bmesh.ops.create_cone(bmb, cap_ends=True, cap_tris=False, segments=48, radius1=1.25, radius2=0.04, depth=4.25)
beam = finish('LightBeam', bmb)
beam.location = (-0.05, -0.15, 2.125)
try:
    beam.visible_shadow = False
except Exception:
    pass
bmm = bpy.data.materials.get('BeamVolume') or bpy.data.materials.new('BeamVolume')
try:
    bmm.use_nodes = True
except Exception:
    pass
nt = bmm.node_tree
for n in [n for n in nt.nodes if n.type != 'OUTPUT_MATERIAL']:
    nt.nodes.remove(n)
mo = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None) or nt.nodes.new('ShaderNodeOutputMaterial')
vsc = nt.nodes.new('ShaderNodeVolumeScatter')
vsc.inputs['Density'].default_value = 0.12
if vsc.inputs.get('Anisotropy') is not None:
    vsc.inputs['Anisotropy'].default_value = 0.3
nt.links.new(vsc.outputs['Volume'], mo.inputs['Volume'])
beam.data.materials.append(bmm)
for k, v in (('volumetric_samples', 96), ('volumetric_end', 15.0), ('use_volumetric_shadows', True)):
    try:
        setattr(sc.eevee, k, v)
    except Exception:
        pass
sp = bpy.data.objects['KeySpot']
sp.rotation_euler = (0.0, 0.0, 0.0)
sp.data.energy = 2600
ff = bpy.data.objects['FaceFill']
ff.location = (-0.7, -1.9, 1.7)
ff.rotation_euler = (H - ff.location).to_track_quat('-Z', 'Y').to_euler()
ff.data.energy = 55
ff.data.color = (1.0, 0.9, 0.85)
ld = bpy.data.lights.new('FaceKey', 'SPOT')
ld.energy = 35; ld.color = (1.0, 0.88, 0.84); ld.spot_size = math.radians(22); ld.spot_blend = 0.8
fk = link(bpy.data.objects.new('FaceKey', ld))
fk.location = (0.25, -1.3, 1.85)
fk.rotation_euler = (H - fk.location).to_track_quat('-Z', 'Y').to_euler()
for n in bpy.data.materials['Tiles'].node_tree.nodes:
    if n.type == 'TEX_BRICK':
        n.inputs['Color1'].default_value = (0.05, 0.038, 0.03, 1)
        n.inputs['Color2'].default_value = (0.028, 0.024, 0.022, 1)
print('part4 ok | objects', len(col.objects))
