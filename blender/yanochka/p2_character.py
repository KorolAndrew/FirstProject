# Yanochka 3D - part 2: wool hair, horns, ears, body
bm = bmesh.new()
def blob(p, r):
    bmesh.ops.create_uvsphere(bm, u_segments=14, v_segments=9, radius=r, matrix=Matrix.Translation(p))

N = 220
ga = math.pi * (3 - math.sqrt(5))
for i in range(N):
    z = 1 - 2 * (i + 0.5) / N
    rr = math.sqrt(1 - z * z)
    d = Vector((rr * math.cos(ga * i), rr * math.sin(ga * i), z))
    if d.z < -0.45:
        continue
    if d.y < -0.25 and d.z < 0.42 and abs(d.x) < 0.75:
        continue
    if d.y < 0 and d.z < -0.1 and abs(d.x) < 0.9:
        continue
    blob(H + d * (0.232 + random.uniform(0.0, 0.03)), random.uniform(0.055, 0.078))
for i in range(11):
    a = math.radians(-75 + 150 * i / 10)
    blob(H + Vector((0.205 * math.sin(a), -0.2 * math.cos(a) + 0.01, 0.115 - 0.05 * abs(math.sin(a)))), 0.05 + 0.012 * random.random())
for i in range(90):
    d = Vector((random.uniform(-1, 1), random.uniform(0.0, 1), random.uniform(-0.9, 0.8)))
    if d.length < 0.2:
        continue
    d.normalize()
    blob(H + Vector((0, 0.05, -0.04)) + Vector((d.x * 0.29, d.y * 0.2, d.z * 0.27)), random.uniform(0.07, 0.1))
locks = [(130, 8), (150, 9), (170, 10), (190, 10), (210, 9), (228, 7), (100, 7), (75, 6), (50, 5), (25, 4), (5, 3)]
for ang, steps in locks:
    a = math.radians(ang + random.uniform(-6, 6))
    bx = 0.25 * math.cos(a)
    by = 0.22 * math.sin(a) + 0.03
    ph = random.uniform(0, 6.28)
    for k in range(steps):
        out = 1.0 + 0.04 * k
        p = Vector((H.x + bx * out + 0.028 * math.cos(ph + 1.9 * k),
                    H.y + by * out + 0.028 * math.sin(ph + 1.9 * k),
                    H.z - 0.1 - 0.068 * k))
        blob(p, max(0.045, 0.078 - 0.0035 * k))
hair = finish('Hair', bm, wool)
md = hair.modifiers.new('Sub', 'SUBSURF'); md.levels = 1; md.render_levels = 1
tex = bpy.data.textures.get('WoolNoise') or bpy.data.textures.new('WoolNoise', 'CLOUDS')
tex.noise_scale = 0.035
md = hair.modifiers.new('Fluff', 'DISPLACE'); md.texture = tex; md.strength = 0.012

for s in (-1, 1):
    C = H + Vector((s * 0.15, 0.0, 0.08))
    NP = 56
    T = 1.8 * math.pi
    pts = []
    rad = []
    for i in range(NP):
        t = T * i / (NP - 1)
        R = 0.105 * (1 - 0.55 * t / T)
        pts.append(C + Vector((s * (0.02 + 0.032 * t), R * math.sin(t), R * math.cos(t))))
        rad.append(1.0 - 0.72 * t / T)
    curve('Horn_%d' % s, pts, 0.045, hornm, rad, res=6)
    for tt in (0.45, 0.62):
        i = int(tt * (NP - 1))
        tan = (pts[i + 1] - pts[i - 1]).normalized()
        torus('Ring_%d_%d' % (s, int(tt * 100)), pts[i], 0.045 * rad[i] + 0.006, 0.0045, silver, rot=ZUP.rotation_difference(tan))
    e = sphere('Ear_%d' % s, H + Vector((s * 0.27, -0.03, -0.075)), 1.0, scale=(0.085, 0.032, 0.042), m=wool)
    e.rotation_euler = (0.0, s * math.radians(35), s * math.radians(-15))

cyl('Neck', H + Vector((0.0, 0.0, -0.25)), H + Vector((0.0, 0.0, -0.17)), 0.038, skin)
torus('Collar', H + Vector((0.0, 0.0, -0.215)), 0.044, 0.011, black)
sphere('Bell', H + Vector((0.0, -0.058, -0.24)), 0.019, m=gold)
sphere('Torso', H + Vector((0.01, 0.0, -0.37)), 1.0, scale=(0.098, 0.082, 0.15), m=black)
sphere('Hips', H + Vector((0.01, 0.0, -0.485)), 1.0, scale=(0.1, 0.085, 0.075), m=black)
sphere('Tail', H + Vector((-0.02, 0.085, -0.47)), 0.05, m=wool)
SR = H + Vector((0.085, -0.01, -0.29))
SL = H + Vector((-0.085, -0.01, -0.29))
ER = Vector((H.x + 0.13, -0.11, H.z - 0.30))
HR = Vector((-0.022, -0.035, H.z - 0.17))
EL = Vector((H.x, -0.2, H.z - 0.36))
HL = Vector((-0.025, -0.07, H.z - 0.32))
for nm, a, b in (('ArmR_up', SR, ER), ('ArmR_lo', ER, HR), ('ArmL_up', SL, EL), ('ArmL_lo', EL, HL)):
    limb(nm, a, b, 0.03, black)
sphere('HandR', HR, 0.028, m=skin)
sphere('HandL', HL, 0.028, m=skin)

def leg(tag, hip, knee, ankle):
    hip = Vector(hip); knee = Vector(knee); ankle = Vector(ankle)
    mid = hip.lerp(knee, 0.4)
    limb('Thigh_' + tag, hip, mid, 0.043, skin)
    limb('Stocking_' + tag, mid, knee, 0.042, black)
    limb('Shin_' + tag, knee, ankle, 0.036, black)
    sphere('Foot_' + tag, ankle + Vector((0.0, -0.02, -0.015)), 1.0, scale=(0.035, 0.05, 0.03), m=black)

leg('R', (H.x + 0.045, -0.06, 0.74), (H.x + 0.11, -0.17, 0.66), (-0.035, -0.07, 0.55))
leg('L', (H.x - 0.035, -0.04, 0.73), (H.x + 0.03, -0.16, 0.59), (H.x + 0.085, -0.05, 0.47))
print('part2 ok')
