"""WOG Dump addon: catalog panel and scene import for Blender 4.2+.

Requires the ``wog-dump`` CLI installed and reachable (set the path in
addon preferences). Everything bpy-related lives here; the CLI client
in ``wog_client.py`` imports cleanly outside Blender.
"""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import (
    AddonPreferences,
    CollectionProperty,
    Operator,
    Panel,
    PropertyGroup,
    UIList,
)

from .wog_client import WogClientError, load_catalog, scene_glb_path

BL_ID = "wog_dump"


class WOGWeaponItem(PropertyGroup):
    name: StringProperty(name="Weapon")
    status: StringProperty(name="Status")


class WOGAddonPreferences(AddonPreferences):
    bl_idname = BL_ID

    wog_path: StringProperty(
        name="wog-dump path",
        description="Path or command name of the wog-dump CLI",
        default="wog-dump",
    )
    base_dir: StringProperty(
        name="Runtime directory",
        description="Working directory whose runtime/ holds the scenes "
        "(empty: inherit Blender's current directory)",
        default="",
    )

    def draw(self, context) -> None:
        layout = self.layout
        layout.prop(self, "wog_path")
        layout.prop(self, "base_dir")


class WOG_OT_refresh(Operator):
    bl_idname = "wog.refresh"
    bl_label = "Refresh catalog"
    bl_description = "Run wog-dump catalog --json and reload the list"

    def execute(self, context):
        preferences = bpy.context.preferences.addons[BL_ID].preferences
        try:
            entries = load_catalog(preferences.wog_path, preferences.base_dir or None)
        except WogClientError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        weapons = context.scene.wog_weapons
        weapons.clear()
        for entry in entries:
            item = weapons.add()
            item.name = entry.name
            item.status = "scene ready" if entry.has_scene else "not built"
        self.report({"INFO"}, f"{len(entries)} weapons loaded")
        return {"FINISHED"}


class WOG_OT_import_scene(Operator):
    bl_idname = "wog.import_scene"
    bl_label = "Import scene"
    bl_description = (
        "Build the scene with wog-dump if needed, then import the GLB "
        "into the current blend file"
    )

    def execute(self, context):
        index = context.scene.wog_index
        weapons = context.scene.wog_weapons
        if index < 0 or index >= len(weapons):
            self.report({"WARNING"}, "Select a weapon first")
            return {"CANCELLED"}
        weapon = weapons[index].name

        preferences = bpy.context.preferences.addons[BL_ID].preferences
        glb = scene_glb_path(preferences.base_dir or None, weapon)
        if not glb.exists():
            self.report(
                {"INFO"}, f"No scene for {weapon}, running wog-dump build-scene..."
            )
            bpy.ops.wm.console_toggle() if False else None
            code, stdout, stderr = _run_blocking(
                preferences, ["build-scene", weapon]
            )
            if code != 0 or not glb.exists():
                detail = (stderr or stdout).strip().splitlines()[-1:]
                self.report(
                    {"ERROR"},
                    f"build-scene failed for {weapon}: {detail[0] if detail else 'no output'}",
                )
                return {"CANCELLED"}

        bpy.ops.import_scene.gltf(filepath=str(glb))
        self.report({"INFO"}, f"Imported {weapon}")
        return {"FINISHED"}


class WOG_OT_build_selected(Operator):
    bl_idname = "wog.build_selected"
    bl_label = "Build missing (selected)"
    bl_description = "Run wog-dump build-scene for every selected weapon without a scene"

    def execute(self, context):
        preferences = bpy.context.preferences.addons[BL_ID].preferences
        weapons = context.scene.wog_weapons
        selected = [item for item in weapons if item.status != "scene ready"]
        if not selected:
            self.report({"INFO"}, "Nothing to build")
            return {"FINISHED"}

        failures = 0
        for item in selected:
            code, stdout, stderr = _run_blocking(
                preferences, ["build-scene", item.name]
            )
            glb = scene_glb_path(preferences.base_dir or None, item.name)
            if code != 0 or not glb.exists():
                failures += 1
                self.report({"WARNING"}, f"Build failed: {item.name}")
            else:
                item.status = "scene ready"
        if failures:
            self.report({"ERROR"}, f"{failures} builds failed, see console")
        else:
            self.report({"INFO"}, "All selected weapons built")
        return {"FINISHED"}


def _run_blocking(preferences, args: list[str]) -> tuple[int, str, str]:
    from .wog_client import run_wog

    try:
        return run_wog(
            preferences.wog_path,
            args,
            base_dir=preferences.base_dir or None,
            timeout=3600,
        )
    except FileNotFoundError:
        return 1, "", f"wog-dump binary not found: {preferences.wog_path}"


class WOG_UL_weapons(UIList):
    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname
    ):
        layout.label(text=item.name, icon="MESH_DATA")
        layout.label(text=item.status)


class WOG_PT_panel(Panel):
    bl_label = "WOG Dump"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "WOG Dump"

    def draw(self, context) -> None:
        layout = self.layout
        scene = context.scene

        row = layout.row()
        row.template_list("WOG_UL_weapons", "", scene, "wog_weapons", scene, "wog_index")
        col = row.column(align=True)
        col.operator("wog.refresh", text="", icon="FILE_REFRESH")

        layout.prop(scene, "wog_filter", text="Filter", icon="VIEWZOOM")
        layout.operator("wog.import_scene", icon="IMPORT")
        layout.operator("wog.build_selected", icon="MOD_BUILD")


classes = (
    WOGWeaponItem,
    WOGAddonPreferences,
    WOG_OT_refresh,
    WOG_OT_import_scene,
    WOG_OT_build_selected,
    WOG_UL_weapons,
    WOG_PT_panel,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.wog_weapons = CollectionProperty(type=WOGWeaponItem)
    bpy.types.Scene.wog_index = bpy.props.IntProperty(default=0)
    bpy.types.Scene.wog_filter = StringProperty(
        name="Filter", description="Name substring", default=""
    )


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.wog_weapons
    del bpy.types.Scene.wog_index
    del bpy.types.Scene.wog_filter
