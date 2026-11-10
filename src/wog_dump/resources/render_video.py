"""Headless Blender video render: imports a scene GLB and encodes MP4.

Generated per run by core/render.py (placeholders filled). Two modes:

- ORBIT: the camera circles the weapon, one turn per run
- clip mode: plays the imported animation (the operate clip), the
  first action matching CLIP_NAME

Output is one H.264 MP4 through Blender's FFMPEG writer.
"""

import math

import bpy
import mathutils

GLB_PATH = "@@GLB_PATH@@"
OUTPUT_PATH = "@@OUTPUT_PATH@@"
FRAME_COUNT = int("@@FRAME_COUNT@@")
FPS = int("@@FPS@@")
RES_X = int("@@RES_X@@")
RES_Y = int("@@RES_Y@@")
CLIP_NAME = "@@CLIP_NAME@@"
ORBIT = @@ORBIT@@

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB_PATH)

scene = bpy.context.scene

center = mathutils.Vector((0.0, 0.0, 0.0))
minimum = [float("inf")] * 3
maximum = [float("-inf")] * 3
for object in scene.objects:
    if object.type != "MESH":
        continue
    for corner in object.bound_box:
        world = object.matrix_world @ mathutils.Vector(corner)
        for axis in range(3):
            minimum[axis] = min(minimum[axis], world[axis])
            maximum[axis] = max(maximum[axis], world[axis])
for axis in range(3):
    center[axis] = (minimum[axis] + maximum[axis]) / 2
radius = max(maximum[axis] - minimum[axis] for axis in range(3)) / 2
if radius <= 0:
    radius = 0.5

camera = None
for object in scene.objects:
    if object.type == "CAMERA":
        camera = object
        break
if camera is None:
    camera_data = bpy.data.cameras.new("VideoCamera")
    camera = bpy.data.objects.new("VideoCamera", camera_data)
    scene.collection.objects.link(camera)
if not scene.camera:
    scene.camera = camera
camera.data.clip_start = 0.001
camera.data.clip_end = 1000.0

lights = [object for object in scene.objects if object.type == "LIGHT"]
if not lights:
    key_data = bpy.data.lights.new("Key", type="SUN")
    key_data.energy = 3.0
    key = bpy.data.objects.new("Key", key_data)
    key.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(key)

scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 48
scene.render.fps = FPS
scene.render.image_settings.file_format = "FFMPEG"
scene.render.ffmpeg.format = "MPEG4"
scene.render.ffmpeg.codec = "H264"
scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
scene.render.resolution_x = RES_X
scene.render.resolution_y = RES_Y

if ORBIT:
    scene.frame_start = 0
    scene.frame_end = FRAME_COUNT - 1

    def orbit_handler(scene):
        frame = scene.frame_current
        angle = 2.0 * math.pi * frame / FRAME_COUNT
        distance = radius / math.tan(math.radians(25.0)) * 1.35
        offset = mathutils.Vector(
            (
                math.cos(angle) * 0.94,
                math.cos(angle) * 0.25,
                math.sin(angle) * 0.25 + 0.18,
            )
        ) * distance
        camera.location = center + offset
        direction = center - camera.location
        camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    bpy.app.handlers.frame_change_pre.append(orbit_handler)
    orbit_handler(scene)
else:
    actions = list(bpy.data.actions)
    if not actions:
        print("WOG ERROR: scene has no animations")
        raise SystemExit(1)
    action = actions[0]
    if CLIP_NAME:
        matches = [
            candidate
            for candidate in actions
            if CLIP_NAME.lower() in candidate.name.lower()
        ]
        if not matches:
            print(f"WOG ERROR: no animation matches {CLIP_NAME!r}")
            raise SystemExit(1)
        action = matches[0]
    frame_start, frame_end = action.frame_range
    scene.frame_start = int(frame_start)
    scene.frame_end = int(frame_end)

scene.render.filepath = OUTPUT_PATH
total = scene.frame_end - scene.frame_start + 1

# Progress: render(animation=True) drives frame_set, the handler prints
def progress_handler(scene):
    frame = scene.frame_current - scene.frame_start + 1
    print(f"WOG FRAME {frame}/{total}")


bpy.app.handlers.frame_change_post.append(progress_handler)
bpy.ops.render.render(animation=True)
print("WOG VIDEO DONE")
