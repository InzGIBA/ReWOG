"""Blender scene setup for a WOG Dump exported weapon.

Generated next to the ``.glb`` by ``wog-dump build-scene``.  Run it
inside Blender (Scripting workspace -> Open -> Run Script), or from a
terminal:

    blender --background --python blender_setup.py -- [path/to/weapon.glb]

The script imports the glTF scene (game object hierarchy, materials,
textures, camera and studio lights), sorts objects into collections,
hides the optional extras (UI arrows, cutaway meshes, visual effects)
exactly like the game's default assembled view, and saves a ``.blend``
next to the GLB.

Configuration: edit the constants below.
"""

import bpy
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HIDE_EXTRAS = True  # hide Arrows/Cutaway/VFX collections after import
SET_ACTIVE_CAMERA = True  # make the imported WOG_Camera the scene camera
SAVE_BLEND = True  # write <weapon>.blend next to the GLB
CLEAR_DEFAULTS = True  # remove the default Cube/Camera/Light of a fresh file

COLLECTION_GUN = "Gun"
COLLECTION_ARROWS = "Arrows"
COLLECTION_CUTAWAY = "Cutaway"
COLLECTION_VFX = "VFX"
COLLECTION_STUDIO = "Studio"

ARROW_MATERIALS = ("arrow_in", "arrow_out")
CUT_MATERIALS = ("lambert4",)
VFX_MATERIALS = ("gas", "muzzle_1", "muzzle_big")


def find_glb():
    """GLB path from CLI args (after --) or the first *.glb beside this file."""
    import sys

    argv = sys.argv
    if "--" in argv:
        args = argv[argv.index("--") + 1:]
        if args:
            return Path(args[0])

    here = (
        Path(bpy.data.filepath).parent if bpy.data.filepath else Path(__file__).parent
    )
    # When opened via Scripting workspace __file__ is defined; fall back to cwd
    candidates = sorted(here.glob("*.glb")) or sorted(Path.cwd().glob("*.glb"))
    if not candidates:
        raise SystemExit(
            "No .glb found - pass it as argument: blender ... -- scene.glb"
        )
    return candidates[0]


def clear_defaults():
    for name in ("Cube", "Camera", "Light", "Lamp"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)


def get_collection(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(obj, collection):
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    collection.objects.link(obj)


def classify(obj):
    """Return the target collection name for an imported object."""
    if obj.type in {"CAMERA", "LIGHT"}:
        return COLLECTION_STUDIO

    mesh_name = (obj.data.name if obj.data else obj.name).lower()
    material_names = []
    for slot in obj.material_slots:
        if slot.material is not None:
            material_names.append(slot.material.name.lower())

    if any(any(marker in m for m in material_names) for marker in ARROW_MATERIALS):
        return COLLECTION_ARROWS
    if mesh_name.endswith("_cut") or any(
        any(marker in m for m in material_names) for marker in CUT_MATERIALS
    ):
        return COLLECTION_CUTAWAY
    if any(any(marker in m for m in material_names) for marker in VFX_MATERIALS):
        return COLLECTION_VFX
    return COLLECTION_GUN


def purge_orphans():
    """Remove unused data-blocks (context-free, works in background mode)."""
    for block in list(bpy.data.meshes):
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in list(bpy.data.materials):
        if block.users == 0:
            bpy.data.materials.remove(block)
    for block in list(bpy.data.images):
        if block.users == 0:
            bpy.data.images.remove(block)


def setup_scene(glb_path):
    if CLEAR_DEFAULTS:
        clear_defaults()

    print(f"[wog-setup] Importing {glb_path} ...")
    bpy.ops.import_scene.gltf(filepath=str(glb_path))

    counts = {}
    for obj in list(bpy.context.scene.objects):
        target = classify(obj)
        move_to_collection(obj, get_collection(target))
        counts[target] = counts.get(target, 0) + 1

    for name, count in sorted(counts.items()):
        print(f"[wog-setup]   {name}: {count} objects")

    if HIDE_EXTRAS:
        for name in (COLLECTION_ARROWS, COLLECTION_CUTAWAY, COLLECTION_VFX):
            collection = bpy.data.collections.get(name)
            if collection is not None and collection.objects:
                collection.hide_viewport = True
                collection.hide_render = True
                print(f"[wog-setup]   hidden: {name}")

    scene = bpy.context.scene
    if SET_ACTIVE_CAMERA:
        for obj in bpy.data.objects:
            if obj.type == "CAMERA" and "WOG_Camera" in obj.name:
                scene.camera = obj
                print(f"[wog-setup] Active camera: {obj.name}")
                break

    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0

    world = bpy.data.worlds.get("WOG World")
    if world is None:
        world = bpy.data.worlds.new("WOG World")
        scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        if background is not None:
            background.inputs[0].default_value = (0.05, 0.05, 0.06, 1.0)
            background.inputs[1].default_value = 1.0

    purge_orphans()

    if SAVE_BLEND:
        blend_path = glb_path.with_suffix(".blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        print(f"[wog-setup] Saved {blend_path}")


def main():
    glb_path = find_glb()
    setup_scene(glb_path)
    print(
        "[wog-setup] Done - scene ready (Gun collection visible, as assembled in game)"
    )


if __name__ == "__main__":
    main()
