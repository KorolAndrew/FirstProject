# Yanochka 3D - part 5: render both cameras and save
ims = sc.render.image_settings
if 'PNG' in [i.identifier for i in ims.bl_rna.properties['file_format'].enum_items]:
    ims.file_format = 'PNG'
out = 'C:/Users/anbre/NotionAgentWorkspace/blender/yanochka/'
for camname, fname in (('Cam_Full', 'yanochka_render'), ('Cam_Close', 'yanochka_close')):
    sc.camera = bpy.data.objects[camname]
    sc.render.filepath = out + fname + '.png'
    bpy.ops.render.render(write_still=True)
sc.camera = bpy.data.objects['Cam_Full']
bpy.ops.wm.save_mainfile()
print('render ok', sc.render.engine)
