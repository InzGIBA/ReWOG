"""WOG Dump addon: catalog panel and scene import for Blender 4.2+.

Requires the ``wog-dump`` CLI installed and reachable (set the path in
addon preferences). Everything bpy-related lives here; the CLI client
in ``wog_client.py`` imports cleanly outside Blender.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList

from .wog_client import WogClientError, load_catalog, scene_glb_path

# Blender keys an enabled add-on by its module name: the preferences
# live under that key too. An installed extension is
# "bl_ext.<repo>.wog_dump", a plain add-on package is its own name.
ADDON_MODULE = __package__

READY = "scene ready"


class WOGWeaponItem(PropertyGroup):
    """One catalog row; ``name`` is the row's built-in key."""

    status: StringProperty(name="Status")


class WOGAddonPreferences(AddonPreferences):
    bl_idname = ADDON_MODULE

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


def _preferences() -> AddonPreferences:
    return bpy.context.preferences.addons[ADDON_MODULE].preferences


def _apply_filter(scene) -> None:
    """Refill the listed rows from the catalog: the names matching the filter."""
    needle = (scene.wog_filter or "").lower()
    listed = scene.wog_weapons
    listed.clear()
    for row in scene.wog_catalog:
        if needle in row.name.lower():
            shown = listed.add()
            shown.name = row.name
            shown.status = row.status
    scene.wog_index = 0


def _on_filter_changed(scene, context) -> None:
    _apply_filter(scene)


def _set_status(scene, weapon: str, status: str) -> None:
    """Mark a weapon in the catalog and in the listed rows alike."""
    scene.wog_catalog[weapon].status = status
    if weapon in scene.wog_weapons:
        scene.wog_weapons[weapon].status = status


class WOG_OT_refresh(Operator):
    bl_idname = "wog.refresh"
    bl_label = "Refresh catalog"
    bl_description = "Run wog-dump catalog --json and reload the list"

    def execute(self, context):
        preferences = _preferences()
        try:
            entries = load_catalog(preferences.wog_path, preferences.base_dir or None)
        except WogClientError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        catalog = context.scene.wog_catalog
        catalog.clear()
        for entry in entries:
            row = catalog.add()
            row.name = entry.name
            row.status = READY if entry.has_scene else "not built"
        _apply_filter(context.scene)
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
        return _import_weapon(self, context, rebuild=False)


class WOG_OT_rebuild_scene(Operator):
    bl_idname = "wog.rebuild_scene"
    bl_label = "Rebuild and import"
    bl_description = (
        "Run wog-dump build-scene again for the selected weapon (this "
        "applies the Animations option), then import the GLB"
    )

    def execute(self, context):
        return _import_weapon(self, context, rebuild=True)


def _build_command(scene, weapon: str) -> list[str]:
    """The build-scene arguments for one weapon under the scene's options."""
    args = ["build-scene", weapon]
    if scene.wog_animations:
        # Without it the GLB is static: nothing to play in Blender
        args.append("--animations")
    return args


def _fit_frame_range(scene) -> None:
    """Let the timeline cover the imported animation.

    The glTF importer leaves the scene range untouched, so a clip of
    75 seconds would play for as long as the default 250 frames last.
    """
    ranges = [
        obj.animation_data.action.frame_range
        for obj in scene.objects
        if obj.animation_data and obj.animation_data.action
    ]
    if not ranges:
        return
    scene.frame_start = int(min(start for start, _end in ranges))
    scene.frame_end = int(max(end for _start, end in ranges))
    scene.frame_set(scene.frame_start)


def _import_weapon(operator: Operator, context, rebuild: bool) -> set[str]:
    scene = context.scene
    weapons = scene.wog_weapons
    index = scene.wog_index
    if index < 0 or index >= len(weapons):
        operator.report({"WARNING"}, "Select a weapon first")
        return {"CANCELLED"}
    weapon = weapons[index].name

    preferences = _preferences()
    glb = scene_glb_path(preferences.base_dir or None, weapon)
    if rebuild or not glb.exists():
        operator.report({"INFO"}, f"Running wog-dump build-scene for {weapon}...")
        built = _run_blocking(operator, preferences, _build_command(scene, weapon))
        if built is None:
            return {"CANCELLED"}
        code, stdout, stderr = built
        if code != 0 or not glb.exists():
            detail = (stderr or stdout).strip().splitlines()[-1:]
            operator.report(
                {"ERROR"},
                f"build-scene failed for {weapon}: "
                f"{detail[0] if detail else 'no output'}",
            )
            return {"CANCELLED"}
        _set_status(scene, weapon, READY)

    bpy.ops.import_scene.gltf(filepath=str(glb))
    _fit_frame_range(scene)
    operator.report({"INFO"}, f"Imported {weapon}")
    return {"FINISHED"}


class WOG_OT_build_selected(Operator):
    bl_idname = "wog.build_selected"
    bl_label = "Build missing (selected)"
    bl_description = (
        "Run wog-dump build-scene for every selected weapon without a scene"
    )

    def execute(self, context):
        scene = context.scene
        preferences = _preferences()
        to_build = [row.name for row in scene.wog_weapons if row.status != READY]
        if not to_build:
            self.report({"INFO"}, "Nothing to build")
            return {"FINISHED"}

        failures = 0
        for weapon in to_build:
            built = _run_blocking(self, preferences, _build_command(scene, weapon))
            if built is None:
                return {"CANCELLED"}
            code, _stdout, _stderr = built
            glb = scene_glb_path(preferences.base_dir or None, weapon)
            if code != 0 or not glb.exists():
                failures += 1
                self.report({"WARNING"}, f"Build failed: {weapon}")
            else:
                _set_status(scene, weapon, READY)
        if failures:
            self.report({"ERROR"}, f"{failures} builds failed, see console")
        else:
            self.report({"INFO"}, "All selected weapons built")
        return {"FINISHED"}


def _run_blocking(
    operator: Operator, preferences: AddonPreferences, args: list[str]
) -> tuple[int, str, str] | None:
    """Run one wog-dump command for ``operator``; Blender waits for it.

    Returns None after reporting the reason when the CLI could not run
    at all (not where the preferences point, or it did not finish in
    time).
    """
    from .wog_client import WogClientError, run_wog

    try:
        return run_wog(
            preferences.wog_path,
            args,
            base_dir=preferences.base_dir or None,
            timeout=3600,
        )
    except WogClientError as e:
        operator.report({"ERROR"}, str(e))
        return None


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
        row.template_list(
            "WOG_UL_weapons", "", scene, "wog_weapons", scene, "wog_index"
        )
        col = row.column(align=True)
        col.operator("wog.refresh", text="", icon="FILE_REFRESH")

        layout.prop(scene, "wog_filter", text="Filter", icon="VIEWZOOM")
        layout.prop(scene, "wog_animations")
        layout.operator("wog.import_scene", icon="IMPORT")
        layout.operator("wog.rebuild_scene", icon="FILE_REFRESH")
        layout.operator("wog.build_selected", icon="MOD_BUILD")


classes = (
    WOGWeaponItem,
    WOGAddonPreferences,
    WOG_OT_refresh,
    WOG_OT_import_scene,
    WOG_OT_rebuild_scene,
    WOG_OT_build_selected,
    WOG_UL_weapons,
    WOG_PT_panel,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    # The catalog keeps every loaded row, the list shows the filtered view
    bpy.types.Scene.wog_catalog = CollectionProperty(type=WOGWeaponItem)
    bpy.types.Scene.wog_weapons = CollectionProperty(type=WOGWeaponItem)
    bpy.types.Scene.wog_index = IntProperty(default=0)
    bpy.types.Scene.wog_filter = StringProperty(
        name="Filter",
        description="Name substring",
        default="",
        update=_on_filter_changed,
    )
    bpy.types.Scene.wog_animations = BoolProperty(
        name="Animations",
        description="Build scenes with the weapon's operate animation, "
        "so there is something to play in Blender",
        default=True,
    )


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.wog_catalog
    del bpy.types.Scene.wog_weapons
    del bpy.types.Scene.wog_index
    del bpy.types.Scene.wog_filter
    del bpy.types.Scene.wog_animations
