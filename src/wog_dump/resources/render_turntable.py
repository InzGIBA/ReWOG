"""Headless Blender turntable: imports one scene GLB, renders N views.

Generated per run by core/render.py, which fills the placeholder
constants below. Runs inside Blender (``blender --background
--python this_file``), so all bpy symbols come from the host.
"""

import math

import bpy
import mathutils

GLB_PATH = "@@GLB_PATH@@"
OUTPUT_DIR = "@@OUTPUT_DIR@@"
ANGLE_COUNT = int("@@ANGLE_COUNT@@")
RENDER_SIZE = int("@@RENDER_SIZE@@")
ELEVATION_DEG = float("@@ELEVATION_DEG@@")
IMAGE_FORMAT = "@@IMAGE_FORMAT@@"

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB_PATH)

scene = bpy.context.scene

# Weapon bounds drive the camera orbit
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

# Prefer the scene camera the exporter baked in, otherwise create one
camera = None
for object in scene.objects:
    if object.type == "CAMERA":
        camera = object
        break
if camera is None:
    camera_data = bpy.data.cameras.new("PreviewCamera")
    camera = bpy.data.objects.new("PreviewCamera", camera_data)
    scene.collection.objects.link(camera)
if not scene.camera:
    scene.camera = camera

camera_data = camera.data
camera_data.clip_start = 0.001
camera_data.clip_end = 1000.0

# Studio lights when the scene has none
lights = [object for object in scene.objects if object.type == "LIGHT"]
if not lights:
    key_data = bpy.data.lights.new("Key", type="SUN")
    key_data.energy = 3.0
    key = bpy.data.objects.new("Key", key_data)
    key.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(key)
    fill_data = bpy.data.lights.new("Fill", type="SUN")
    fill_data.energy = 1.2
    fill = bpy.data.objects.new("Fill", fill_data)
    fill.rotation_euler = (math.radians(60), 0, math.radians(-120))
    scene.collection.objects.link(fill)

scene.render.resolution_x = RENDER_SIZE
scene.render.resolution_y = RENDER_SIZE
scene.render.image_settings.file_format = (
    "PNG" if IMAGE_FORMAT == "png" else "JPEG" if IMAGE_FORMAT == "jpg" else "WEBP"
)
scene.render.film_transparent = IMAGE_FORMAT == "png"
scene.render.threads_mode = "AUTO"

# Cycles renders on CPU everywhere; EEVEE needs a GPU context that
# headless hosts often lack
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 64

distance = radius / math.tan(math.radians(25.0)) * 1.35
elevation = math.radians(ELEVATION_DEG)

for index in range(ANGLE_COUNT):
    angle = 2.0 * math.pi * index / ANGLE_COUNT
    offset = (
        mathutils.Vector(
            (
                math.cos(angle) * math.cos(elevation),
                math.cos(elevation) * math.sin(angle),
                math.sin(elevation),
            )
        )
        * distance
    )
    camera.location = center + offset
    # Aim the camera at the weapon center
    direction = center - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    scene.render.filepath = f"{OUTPUT_DIR}/view_{index + 1:02d}.{IMAGE_FORMAT}"
    bpy.ops.render.render(write_still=True)

print("WOG TURNTABLE DONE")
