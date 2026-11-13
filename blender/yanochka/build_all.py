# Yanochka 3D: build the whole scene from scratch.
# Blender: Scripting tab -> Open -> build_all.py -> Run Script (about 10 s).
base = 'C:/Users/anbre/NotionAgentWorkspace/blender/yanochka/'
ns = {'__name__': '__main__'}
for f in ('p1_helpers.py', 'p2_character.py', 'p3_scene.py', 'p4_polish.py'):
    exec(compile(open(base + f, encoding='utf-8').read(), base + f, 'exec'), ns)
