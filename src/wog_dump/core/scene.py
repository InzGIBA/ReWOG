"""Blender-ready scene export from decrypted weapon bundles.

Reads a decrypted ``<weapon>.unity3d`` bundle, rebuilds the game object
hierarchy (the same transforms the game uses - parts are positioned
exactly as in the assembled weapon), strips UI-only objects and writes
a glTF 2.0 scene consumable by Blender:

* ``runtime/scenes/<weapon>/<weapon>.glb``  - full scene with camera
  and studio lights
* ``runtime/scenes/<weapon>/textures/``     - source PNG textures
* ``runtime/scenes/<weapon>/scene.json``    - scene manifest
* ``runtime/scenes/<weapon>/blender_setup.py`` - one-click Blender setup

Coordinate conversion (Unity is left-handed Y-up, glTF right-handed
Y-up) mirrors the Z axis: positions ``(x, y, -z)``, quaternions
``(-x, -y, z, w)``, triangle winding flipped, UV V inverted.  Unity
centimeters are baked to meters by ``unit_scale``.
"""

from __future__ import annotations

import io
import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import UnityPy
from UnityPy.helpers import MeshHelper

from ..utils.logging import get_logger
from ..utils.normal_map import is_unity_swizzled_normal, unswizzle_normal_map
from .anim import extract_tracks
from .config import WOGConfig, get_config
from .export import EXPORT_FORMATS, ExportError, ExportOptions, convert_format
from .glb import AnimationChannel, GLBWriter, MeshPrimitive, SceneError
from .paths import contained_path, is_safe_weapon_name, safe_object_name
from .scene_states import (
    SCENE_STATES,
    PartPlacement,
    apply_exploded,
    apply_field_strip,
    apply_grid_layout,
)
from .transforms import (
    compose_matrix,
    identity_matrix,
    invert_matrix,
    multiply_matrix,
    transform_point,
)
from .viewer import copy_viewer_assets, render_viewer_html

__all__ = [
    "SceneBuilder",
    "SceneStats",
    "SceneError",
    "SceneExportError",
    "classify_game_object",
    "convert_position",
    "convert_rotation",
]

# Matrix helpers live in core/transforms.py; the underscore names stay
# for the established call sites and tests
_compose_matrix = compose_matrix
_identity_matrix = identity_matrix
_multiply_matrix = multiply_matrix

# Unity material names that mark UI/visual-effect objects rather than
# real weapon geometry
ARROW_MATERIALS = frozenset({"arrow_in", "arrow_out"})
CUT_MATERIALS = frozenset({"lambert4"})
VFX_MATERIALS = frozenset({"gas", "muzzle_1", "muzzle_big"})

# Look direction of the scene camera (glTF space, Y up)
_CAMERA_DIRECTION = (0.25, 0.35, 1.0)


class SceneExportError(SceneError):
    """Raised when a scene cannot be built from a bundle."""


def _atomic_write(path: Path, data: bytes) -> None:
    """Write bytes to path via temp-file + rename.

    ``os.replace`` swaps the directory entry itself, so a pre-planted
    symlink at the destination is replaced instead of being followed -
    the same placement discipline the download path uses.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


@dataclass
class SceneStats:
    """Summary counters for one exported scene."""

    weapon: str = ""
    meshes: int = 0
    materials: int = 0
    textures: int = 0
    nodes: int = 0
    state: str = "assembled"
    placements: list[PartPlacement] = field(default_factory=list)
    animations: list[dict[str, object]] = field(default_factory=list)
    annotations: list[dict[str, object]] = field(default_factory=list)
    annotate: bool = False
    exports: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    field_strip: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


@dataclass
class _GameObjectInfo:
    name: str
    mesh: Any | None = None  # UnityPy Mesh data
    material_pids: list[int] = field(default_factory=list)
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    children: list[_GameObjectInfo] = field(default_factory=list)


@dataclass
class _MeshData:
    """Extracted Unity mesh geometry plus its local AABB."""

    name: str
    vertices: list[Any]
    normals: list[Any]
    uvs: list[Any]
    submeshes: list[list[tuple[int, ...]]]
    local_min: list[float]
    local_max: list[float]


def classify_game_object(name: str, materials: list[str]) -> str:
    """Categorize a game object: ``gun``, ``arrows``, ``cut`` or ``vfx``.

    Classification is driven by the materials the game itself uses for
    its UI overlays; the mesh name is a fallback for cutaway meshes.
    """
    if any(m in ARROW_MATERIALS for m in materials):
        return "arrows"
    if name.endswith("_cut") or any(m in CUT_MATERIALS for m in materials):
        return "cut"
    if any(m in VFX_MATERIALS for m in materials):
        return "vfx"
    return "gun"


def collect_annotations(
    root: _GameObjectInfo,
    world_by_path: dict[str, list[float]],
    unit_scale: float,
    stats: SceneStats,
    mesh_data_fn: Any | None = None,
) -> list[dict[str, object]]:
    """One annotation per listed part: glTF center, group and bbox size.

    Centers come from the emitted world matrices; sizes are the local
    mesh extents scaled (a good-enough proxy, orientations ignored).
    """
    annotations: list[dict[str, object]] = []
    seen: set[str] = set()
    for group in ("parts", "field_strip"):
        for name in getattr(stats, group):
            if name in seen:
                continue
            path = _longest_path_for_name(root, world_by_path, name)
            if path is None:
                continue
            seen.add(name)
            world = world_by_path[path]
            center = convert_position((world[12], world[13], world[14]), unit_scale)
            annotations.append(
                {
                    "name": name,
                    "node": path,
                    "center": [round(c, 6) for c in center],
                    "group": group,
                    "bbox_size": _bbox_size(root, path, unit_scale, mesh_data_fn),
                }
            )
    return annotations


def _longest_path_for_name(
    root: _GameObjectInfo, world_by_path: dict[str, list[float]], name: str
) -> str | None:
    """The deepest emitted path whose last segment matches name."""
    best: str | None = None
    for path in world_by_path:
        segment = path.rsplit("/", 1)[-1]
        if segment == name and (best is None or len(path) > len(best)):
            best = path
    return best


def _node_at_path(root: _GameObjectInfo, path: str) -> _GameObjectInfo | None:
    node = None
    current = list(root.children)
    for segment in path.split("/"):
        node = next((child for child in current if child.name == segment), None)
        if node is None:
            return None
        current = node.children
    return node


def _bbox_size(
    root: _GameObjectInfo,
    path: str,
    unit_scale: float,
    mesh_data_fn: Any | None,
) -> list[float]:
    """Local mesh extents scaled to glTF meters (orientation ignored)."""
    node = _node_at_path(root, path)
    if node is None or node.mesh is None or mesh_data_fn is None:
        return [0.0, 0.0, 0.0]
    mesh = mesh_data_fn(node.mesh)
    if mesh is None or not getattr(mesh, "local_min", None):
        return [0.0, 0.0, 0.0]
    return [
        round((mesh.local_max[i] - mesh.local_min[i]) * unit_scale, 6) for i in range(3)
    ]


def convert_position(
    v: tuple[float, float, float], unit_scale: float = 1.0
) -> tuple[float, float, float]:
    """Unity position to glTF: mirror Z, apply unit scale."""
    return (v[0] * unit_scale, v[1] * unit_scale, -v[2] * unit_scale)


def convert_rotation(
    q: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Unity quaternion (x, y, z, w) to glTF under Z mirroring."""
    return (-q[0], -q[1], q[2], q[3])


class SceneBuilder:
    """Builds glTF scenes from decrypted weapon bundles."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self._mesh_cache: dict[int, _MeshData | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_scene(
        self,
        weapon: str,
        include_extras: frozenset[str] = frozenset(),
        unit_scale: float = 0.01,
        input_dir: Path | None = None,
        output_dir: Path | None = None,
        state: str = "assembled",
        explode_distance: float = 0.35,
        animations: bool = False,
        animation_name: str | None = None,
        export_formats: frozenset[str] = frozenset(),
        export_options: ExportOptions | None = None,
        viewer: bool = False,
        viewer_link: bool = False,
        assembly_steps: int = 0,
        steps_from: str = "parts",
        animated: bool = False,
        annotate: bool = False,
    ) -> Path:
        """Export one weapon to ``<output_dir>/<weapon>.glb``.

        ``include_extras`` may contain ``"arrows"``, ``"cut"`` and/or
        ``"vfx"`` to also export UI arrows, cutaway meshes and visual
        effects (hidden in the game's default assembled view).

        ``state`` selects the disassembly pose: ``assembled`` (default),
        ``field-strip`` (parts at their game removal positions),
        ``exploded`` (parts pushed apart along removal directions) or
        ``parts`` (grid layout). Non-assembled states export to
        ``<weapon>-<state>`` directories unless ``output_dir`` is set.

        ``animations`` embeds the bundle's operate animation as glTF
        animation tracks; ``animation_name`` limits the export to one
        clip.

        ``export_formats`` requests extra formats alongside the GLB:
        ``stl``, ``usd``, ``usdz`` and/or ``fbx`` (see core/export.py).

        ``viewer`` writes a self-contained ``viewer.html`` (GLB and the
        three.js runtime embedded, opens offline). ``viewer_link``
        writes an HTML that loads the GLB and ``./vendor/`` from the
        scene directory instead (smaller file, same folder layout).

        ``assembly_steps`` exports a step-by-step build instead: N GLBs
        in a ``<weapon>-steps`` directory, each with more parts placed,
        plus ``steps.json`` and (with ``animated``) one ``assembly.glb``
        whose animation places part after part. ``steps_from`` picks
        the ordering source: the ``_parts`` list (default) or the
        field-strip list.
        """
        # The weapon string builds both the bundle path and the output
        # directory: reject anything outside the name alphabet
        if not is_safe_weapon_name(weapon):
            raise SceneExportError(f"Invalid weapon name: {weapon!r}")
        if state not in SCENE_STATES:
            raise SceneExportError(
                f"Invalid state {state!r}, expected one of: {sorted(SCENE_STATES)}"
            )
        if explode_distance < 0:
            raise SceneExportError(
                f"Explode distance must be non-negative, got {explode_distance}"
            )
        if assembly_steps and state != "assembled":
            raise SceneExportError(
                "--assembly-steps defines its own poses; drop --state or "
                "build the states separately"
            )
        if assembly_steps and steps_from not in ("parts", "field_strip"):
            raise SceneExportError(
                f"Invalid steps source {steps_from!r}, expected parts or " "field_strip"
            )

        bundle = (input_dir or self.config.decrypted_dir) / f"{weapon}.unity3d"
        if not bundle.exists():
            raise SceneExportError(
                f"Decrypted bundle not found: {bundle} (run decrypt-assets first)"
            )

        if assembly_steps:
            dir_name = f"{weapon}-steps"
            output_dir = (
                output_dir
                if output_dir is not None
                else self.config.base_dir / "runtime" / "scenes" / dir_name
            )
        elif output_dir is None:
            dir_name = weapon if state == "assembled" else f"{weapon}-{state}"
            output_dir = self.config.base_dir / "runtime" / "scenes" / dir_name
        output_dir.mkdir(parents=True, exist_ok=True)
        # A pre-existing output tree is provenance-unknown content: a
        # symlinked directory would redirect every subsequent write
        if output_dir.is_symlink():
            raise SceneExportError(
                f"Refusing symlinked scene output directory: {output_dir}"
            )

        with self.logger.time_operation(f"scene:{weapon}"):
            env = UnityPy.load(str(bundle))

            stats = SceneStats(weapon=weapon)
            root = self._build_game_object_tree(env)
            if root is None:
                raise SceneExportError(f"No transform hierarchy found in {bundle}")

            # Part lists feed the grid layout, so they are read before
            # the state pass rewrites translations
            self._read_part_lists(env, weapon, stats)

            if assembly_steps:
                return self._build_assembly_steps(
                    env,
                    root,
                    weapon,
                    output_dir,
                    include_extras,
                    unit_scale,
                    stats,
                    assembly_steps,
                    steps_from,
                    animated,
                )

            if state == "field-strip":
                stats.placements = apply_field_strip(root, unit_scale, stats)
            elif state == "exploded":
                stats.placements = apply_exploded(
                    root, unit_scale, explode_distance, stats
                )
            elif state == "parts":
                stats.placements = apply_grid_layout(
                    root, unit_scale, stats.parts, self._mesh_data, stats
                )
            stats.state = state
            stats.annotate = annotate or viewer or viewer_link
            if annotate and not (viewer or viewer_link):
                # Annotations live in the HTML layer only: generating them
                # without a viewer makes an empty gesture, so a viewer
                # comes along automatically
                viewer = True

            glb_path, _ = self._emit_and_write(
                env,
                root,
                weapon,
                output_dir,
                include_extras,
                unit_scale,
                stats,
                animations=animations,
                animation_name=animation_name,
            )

            extra_formats = export_formats - {"glb"}
            unknown = extra_formats - EXPORT_FORMATS
            if unknown:
                raise SceneExportError(
                    f"Unknown export format(s): {sorted(unknown)}, "
                    f"expected one of {sorted(EXPORT_FORMATS)}"
                )
            if extra_formats:
                stats.exports = self._export_extra_formats(
                    glb_path, output_dir, extra_formats, export_options
                )

            self._write_manifest(output_dir, weapon, include_extras, unit_scale, stats)
            self._write_blender_setup(output_dir)

            if viewer or viewer_link:
                manifest = json.loads(
                    (output_dir / "scene.json").read_text(encoding="utf-8")
                )
                html = render_viewer_html(
                    glb_path.read_bytes(), manifest, linked=viewer_link
                )
                _atomic_write(output_dir / "viewer.html", html.encode("utf-8"))
                if viewer_link:
                    copy_viewer_assets(output_dir)

        state_note = (
            f", state={state} ({len(stats.placements)} parts moved)"
            if state != "assembled"
            else ""
        )
        self.logger.info(
            f"Scene {weapon}: {stats.meshes} meshes, {stats.materials} materials, "
            f"{stats.textures} textures{state_note} -> {glb_path}"
        )
        return glb_path

    # ------------------------------------------------------------------
    # Scene emission (shared by the single-scene and steps paths)
    # ------------------------------------------------------------------

    def _emit_and_write(
        self,
        env: Any,
        root: _GameObjectInfo,
        weapon: str,
        output_dir: Path,
        include_extras: frozenset[str],
        unit_scale: float,
        stats: SceneStats,
        animations: bool = False,
        animation_name: str | None = None,
        extra_animation: (
            Callable[[dict[str, int]], tuple[str, list[AnimationChannel]]] | None
        ) = None,
        glb_name: str | None = None,
    ) -> tuple[Path, dict[str, int]]:
        """Emit the tree into a GLB and write it to output_dir.

        Returns the written path and the emitted node path map (the
        animation binding address space).
        """
        # The mesh cache is keyed by id(mesh): CPython reuses freed
        # addresses, so entries from a previous build could hand one
        # scene another part's geometry. One build, one cache.
        self._mesh_cache.clear()
        materials_by_pid = {
            obj.path_id: obj.read()
            for obj in env.objects
            if obj.type.name == "Material"
        }
        textures_by_pid = {
            obj.path_id: obj.read()
            for obj in env.objects
            if obj.type.name == "Texture2D"
        }

        writer = GLBWriter(generator="WOG Dump scene exporter")

        gltf_materials = self._convert_materials(
            writer, materials_by_pid, textures_by_pid, output_dir, stats
        )

        world_bounds = [[math.inf] * 3, [-math.inf] * 3]
        node_paths: dict[str, int] = {}
        world_by_path: dict[str, list[float]] = {}
        self._emit_nodes(
            root,
            parent=None,
            matrix=_identity_matrix(),
            include_extras=include_extras,
            unit_scale=unit_scale,
            gltf_materials=gltf_materials,
            materials_by_pid=materials_by_pid,
            writer=writer,
            stats=stats,
            world_bounds=world_bounds,
            node_paths=node_paths,
            world_by_path=world_by_path,
            path="",
        )
        stats.nodes = len(writer._nodes)
        self._add_studio_rig(writer, world_bounds)

        if animations:
            stats.animations = self._export_animations(
                env, writer, node_paths, unit_scale, animation_name
            )
        if extra_animation is not None:
            built = extra_animation(node_paths)
            if built is not None:
                name, channels = built
                if channels:
                    writer.add_animation(name, channels)

        if getattr(stats, "annotate", False):
            stats.annotations = collect_annotations(
                root, world_by_path, unit_scale, stats, self._mesh_data
            )

        glb_path = output_dir / (glb_name or f"{weapon}.glb")
        writer.write(glb_path)
        return glb_path, node_paths

    def _build_assembly_steps(
        self,
        env: Any,
        root: _GameObjectInfo,
        weapon: str,
        output_dir: Path,
        include_extras: frozenset[str],
        unit_scale: float,
        stats: SceneStats,
        steps: int,
        steps_from: str,
        animated: bool,
    ) -> Path:
        """Export the step-by-step build (see build_scene docs)."""
        from .anim import AnimationTrack
        from .paths import contained_path
        from .scene_states import (
            _apply_containment,
            _collect_removal_targets,
            _match_slot,
            _move_to,
            _walk_world,
            normalize_part_name,
        )
        from .scene_steps import (
            assembled_counts,
            assembly_windows,
            node_paths_by_id,
        )

        if steps < 1:
            raise SceneExportError(f"Assembly steps must be >= 1, got {steps}")

        index = _walk_world(root)
        targets = _collect_removal_targets(root)
        if not targets:
            raise SceneExportError(
                f"{weapon}: no removal anchors in the bundle, step export "
                "needs the _in/_out placeholders"
            )

        # Anchor base -> matched slot, the reverse lookup for list names
        base_of_slot: dict[int, str] = {}
        slot_by_base: dict[str, Any] = {}
        for base in sorted(targets):
            slot = _match_slot(base, index)
            if slot is not None:
                base_of_slot[id(slot.node)] = base
                slot_by_base[base] = slot

        order_source = stats.field_strip if steps_from == "field_strip" else stats.parts
        # List order drives the build sequence; unmatched entries fall
        # through to anchor order below
        ordered: list[tuple[str, Any]] = []
        seen_ids: set[int] = set()
        for name in order_source:
            slot = index.get(normalize_part_name(name))
            if slot is None or id(slot.node) in seen_ids:
                continue
            slot_base = base_of_slot.get(id(slot.node))
            if slot_base is None or slot_base not in targets:
                continue
            seen_ids.add(id(slot.node))
            ordered.append((slot_base, slot))
        for base in sorted(targets):
            slot = _match_slot(base, index)
            if slot is None or id(slot.node) in seen_ids:
                continue
            seen_ids.add(id(slot.node))
            ordered.append((base, slot))

        kept_ids = {
            id(slot.node)
            for slot in _apply_containment([slot for _, slot in ordered])[0]
        }
        outer_by_base = [
            (base, slot) for base, slot in ordered if id(slot.node) in kept_ids
        ]
        if not outer_by_base:
            raise SceneExportError(f"{weapon}: no anchored parts to step through")

        slot_targets = {id(slot): targets[base] for base, slot in outer_by_base}

        def target_of(slot: Any) -> tuple[float, float, float]:
            return slot_targets[id(slot)]

        total = len(outer_by_base)
        counts = assembled_counts(total, steps)
        windows = assembly_windows(total, steps)

        home: dict[int, tuple[float, float, float]] = {}
        exploded_local: dict[int, tuple[float, float, float]] = {}
        for _, slot in outer_by_base:
            home[id(slot.node)] = slot.node.translation
            parent_inverse = invert_matrix(slot.parent_world)
            exploded_local[id(slot.node)] = transform_point(
                parent_inverse, target_of(slot)
            )

        # Reuse the material cache across steps: the textures land in the
        # shared output directory once, later writes are identical bytes
        glb_paths = []
        for step_index, count in enumerate(counts, start=1):
            assembled_ids = {id(slot.node) for _, slot in outer_by_base[:count]}
            for _, slot in outer_by_base:
                node_id = id(slot.node)
                slot.node.translation = (
                    home[node_id]
                    if node_id in assembled_ids
                    else exploded_local[node_id]
                )
            step_stats = SceneStats(weapon=weapon, state=f"step-{step_index}")
            step_stats.parts = list(stats.parts)
            step_stats.field_strip = list(stats.field_strip)
            glb_path, _ = self._emit_and_write(
                env,
                root,
                weapon,
                output_dir,
                include_extras,
                unit_scale,
                step_stats,
                glb_name=f"step_{step_index:02d}.glb",
            )
            glb_paths.append(glb_path.name)
            self.logger.info(
                f"Step {step_index}/{len(counts)}: {count} of {total} parts placed"
            )

        extra_animation: (
            Callable[[dict[str, int]], tuple[str, list[AnimationChannel]]] | None
        ) = None
        if animated:
            # Parts start exploded and slide home, one per window; the
            # channel builder resolves node indices from the emitted paths
            for _, slot in outer_by_base:
                slot.node.translation = exploded_local[id(slot.node)]
            paths_by_id = node_paths_by_id(root)
            exploded_start = {
                id(slot.node): exploded_local[id(slot.node)]
                for _, slot in outer_by_base
            }
            home_start = {
                id(slot.node): home[id(slot.node)] for _, slot in outer_by_base
            }

            def build_assembly_animation(
                node_paths: dict[str, int],
            ) -> tuple[str, list[AnimationChannel]]:
                channels: list[AnimationChannel] = []
                unmatched = 0
                for part_index, (_, slot) in enumerate(outer_by_base):
                    node_id = id(slot.node)
                    node_index = node_paths.get(paths_by_id[node_id])
                    if not node_index:
                        unmatched += 1
                        continue
                    start, end = windows[part_index]
                    channels.append(
                        AnimationChannel(
                            node_index=node_index,
                            property="translation",
                            times=[start, end],
                            values=[
                                convert_position(exploded_start[node_id], unit_scale),
                                convert_position(home_start[node_id], unit_scale),
                            ],
                        )
                    )
                if unmatched:
                    self.logger.warning(
                        f"Assembly animation: {unmatched} unmatched parts skipped"
                    )
                return "assembly", channels

            animation_stats = SceneStats(weapon=weapon, state="animated")
            self._emit_and_write(
                env,
                root,
                weapon,
                output_dir,
                include_extras,
                unit_scale,
                animation_stats,
                glb_name="assembly.glb",
                extra_animation=build_assembly_animation,
            )

        manifest = {
            "weapon": weapon,
            "steps": len(counts),
            "parts": total,
            "placed_per_step": counts,
            "windows": windows,
            "files": glb_paths,
            "animated": animated,
        }
        _atomic_write(
            contained_path(output_dir, "steps.json"),
            json.dumps(manifest, indent=2).encode("utf-8"),
        )
        self._write_blender_setup(output_dir)

        first = output_dir / "step_01.glb"
        self.logger.info(
            f"Assembly steps {weapon}: {len(counts)} steps, {total} parts "
            f"-> {output_dir}"
        )
        return first

    # ------------------------------------------------------------------
    # Bundle indexing
    # ------------------------------------------------------------------

    @staticmethod
    def _deref(pointer: Any) -> Any | None:
        try:
            return pointer.deref()
        except Exception:
            return None

    def _build_game_object_tree(self, env: Any) -> _GameObjectInfo | None:
        """Resolve the GameObject hierarchy starting at the root Transform."""
        transforms: dict[int, Any] = {}
        root_transform = None
        for obj in env.objects:
            if obj.type.name == "Transform":
                data = obj.read()
                transforms[obj.path_id] = data
                if data.m_Father.path_id == 0:
                    root_transform = data

        if root_transform is None:
            return None

        meshes_by_pid = {
            obj.path_id: obj for obj in env.objects if obj.type.name == "Mesh"
        }

        def build(transform: Any) -> _GameObjectInfo:
            go_object = self._deref(transform.m_GameObject)
            if go_object is None:
                return _GameObjectInfo(name="object")
            go = go_object.read()
            mesh = None
            material_pids: list[int] = []
            for component_ref in go.m_Components:
                component = self._deref(
                    component_ref[1]
                    if isinstance(component_ref, (list, tuple))
                    else component_ref
                )
                if component is None:
                    continue
                if component.type.name == "MeshFilter":
                    mesh_pointer = component.read().m_Mesh
                    mesh_obj = meshes_by_pid.get(mesh_pointer.path_id)
                    if mesh_obj is not None:
                        mesh = mesh_obj.read()
                elif component.type.name == "MeshRenderer":
                    for material_pointer in component.read().m_Materials:
                        material_pids.append(material_pointer.path_id)

            info = _GameObjectInfo(
                name=go.m_Name or "object",
                mesh=mesh,
                material_pids=material_pids,
                translation=_vector3(transform.m_LocalPosition),
                rotation=_quaternion(transform.m_LocalRotation),
                scale=_vector3(transform.m_LocalScale),
            )
            for child in transform.m_Children:
                child_data = transforms.get(child.path_id)
                if child_data is not None:
                    info.children.append(build(child_data))
            return info

        return build(root_transform)

    # ------------------------------------------------------------------
    # Materials and textures
    # ------------------------------------------------------------------

    def _convert_materials(
        self,
        writer: GLBWriter,
        materials_by_pid: dict[int, Any],
        textures_by_pid: dict[int, Any],
        output_dir: Path,
        stats: SceneStats,
    ) -> dict[int, int]:
        """Create glTF materials; returns Unity material pid -> glTF index."""
        textures_dir = output_dir / "textures"
        textures_dir.mkdir(parents=True, exist_ok=True)
        if textures_dir.is_symlink():
            raise SceneExportError(
                f"Refusing symlinked textures directory: {textures_dir}"
            )

        gltf_texture_by_pid: dict[int, int] = {}
        gltf_materials: dict[int, int] = {}

        for pid, material in materials_by_pid.items():
            props = material.m_SavedProperties
            color = (1.0, 1.0, 1.0, 1.0)
            shininess = 0.078125
            main_texture_pid = 0

            for name, value in props.m_Colors:
                if name == "_Color":
                    color = (value.r, value.g, value.b, value.a)
            for name, value in props.m_Floats:
                if name == "_Shininess":
                    shininess = float(value)
            for name, tex_env in props.m_TexEnvs:
                if name == "_MainTex" and tex_env.m_Texture.path_id:
                    main_texture_pid = tex_env.m_Texture.path_id

            base_color_texture = None
            if main_texture_pid and main_texture_pid in textures_by_pid:
                if main_texture_pid not in gltf_texture_by_pid:
                    texture_data = textures_by_pid[main_texture_pid]
                    image_bytes = self._texture_png(texture_data)
                    try:
                        texture_name = safe_object_name(str(texture_data.m_Name))
                    except ValueError:
                        self.logger.warning(
                            f"Skipping texture with unsafe name "
                            f"{texture_data.m_Name!r}"
                        )
                        stats.skipped["unsafe_texture_name"] = (
                            stats.skipped.get("unsafe_texture_name", 0) + 1
                        )
                        continue
                    _atomic_write(
                        contained_path(textures_dir, f"{texture_name}.png"), image_bytes
                    )
                    image = writer.add_image(image_bytes)
                    gltf_texture_by_pid[main_texture_pid] = writer.add_texture(image)
                    stats.textures += 1
                base_color_texture = gltf_texture_by_pid[main_texture_pid]

            alpha_mode = "OPAQUE"
            double_sided = False
            if material.m_Name in VFX_MATERIALS:
                alpha_mode = "BLEND"
                double_sided = True

            gltf_materials[pid] = writer.add_material(
                name=material.m_Name,
                base_color_factor=color if base_color_texture is None else None,
                base_color_texture=base_color_texture,
                metallic=0.0,
                roughness=_shininess_to_roughness(shininess),
                alpha_mode=alpha_mode,
                double_sided=double_sided,
            )
            stats.materials += 1

        return gltf_materials

    @staticmethod
    def _texture_png(texture: Any) -> bytes:
        """Texture2D to PNG bytes (UnityPy decodes; normal maps unswizzled)."""
        image = texture.image
        if image is None:
            raise SceneExportError(f"Cannot decode texture {texture.m_Name}")
        if is_unity_swizzled_normal(image):
            image = unswizzle_normal_map(image)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # Mesh extraction
    # ------------------------------------------------------------------

    def _mesh_data(self, mesh: Any) -> _MeshData | None:
        """Extract and cache a Unity mesh's geometry (returns None on failure)."""
        cache_key = id(mesh)
        if cache_key in self._mesh_cache:
            return self._mesh_cache[cache_key]

        data: _MeshData | None = None
        try:
            handler = MeshHelper.MeshHandler(mesh)
            handler.process()
            vertices = handler.m_Vertices or []
            if vertices:
                local_min = [math.inf] * 3
                local_max = [-math.inf] * 3
                for v in vertices:
                    for axis in range(3):
                        if v[axis] < local_min[axis]:
                            local_min[axis] = v[axis]
                        if v[axis] > local_max[axis]:
                            local_max[axis] = v[axis]
                data = _MeshData(
                    name=mesh.m_Name,
                    vertices=vertices,
                    normals=handler.m_Normals or [],
                    uvs=handler.m_UV0 or [],
                    submeshes=handler.get_triangles(),
                    local_min=local_min,
                    local_max=local_max,
                )
        except Exception as e:
            self.logger.debug(f"Mesh extraction failed for {mesh.m_Name}: {e}")

        self._mesh_cache[cache_key] = data
        return data

    # ------------------------------------------------------------------
    # Node emission
    # ------------------------------------------------------------------

    def _emit_nodes(
        self,
        node: _GameObjectInfo,
        parent: int | None,
        matrix: list[float],
        include_extras: frozenset[str],
        unit_scale: float,
        gltf_materials: dict[int, int],
        materials_by_pid: dict[int, Any],
        writer: GLBWriter,
        stats: SceneStats,
        world_bounds: list[list[float]],
        node_paths: dict[str, int],
        world_by_path: dict[str, list[float]],
        path: str,
    ) -> int | None:
        """Recursively emit kept nodes; returns the glTF node index or None.

        Emitted nodes register their Unity hierarchy path in
        ``node_paths`` (animation bindings address nodes by path).
        """
        material_names = [
            materials_by_pid[pid].m_Name
            for pid in node.material_pids
            if pid in materials_by_pid
        ]
        category = classify_game_object(node.name, material_names)

        if category != "gun" and category not in include_extras:
            stats.skipped[category] = stats.skipped.get(category, 0) + 1
            return None

        local_matrix = _compose_matrix(node.translation, node.rotation, node.scale)
        world_matrix = _multiply_matrix(matrix, local_matrix)

        child_indices: list[int] = []
        for child in node.children:
            child_path = f"{path}/{child.name}" if path else child.name
            child_index = self._emit_nodes(
                child,
                None,
                world_matrix,
                include_extras,
                unit_scale,
                gltf_materials,
                materials_by_pid,
                writer,
                stats,
                world_bounds,
                node_paths,
                world_by_path,
                child_path,
            )
            if child_index is not None:
                child_indices.append(child_index)

        mesh_data = self._mesh_data(node.mesh) if node.mesh is not None else None
        if mesh_data is None and not child_indices:
            stats.skipped["empty"] = stats.skipped.get("empty", 0) + 1
            return None  # structural empty without kept geometry

        mesh_index = None
        if mesh_data is not None:
            mesh_index = self._convert_mesh(
                node, mesh_data, gltf_materials, writer, stats, unit_scale
            )
            _accumulate_bounds(world_bounds, world_matrix, mesh_data, unit_scale)

        node_index = writer.add_node(
            name=node.name,
            mesh=mesh_index,
            translation=convert_position(node.translation, unit_scale),
            rotation=(
                convert_rotation(node.rotation)
                if node.rotation != (0.0, 0.0, 0.0, 1.0)
                else None
            ),
            scale=node.scale if node.scale != (1.0, 1.0, 1.0) else None,
            parent=parent,
        )
        for child_index in child_indices:
            writer.attach_child(node_index, child_index)
        node_paths[path] = node_index
        world_by_path[path] = world_matrix

        return node_index

    def _convert_mesh(
        self,
        node: _GameObjectInfo,
        mesh_data: _MeshData,
        gltf_materials: dict[int, int],
        writer: GLBWriter,
        stats: SceneStats,
        unit_scale: float = 1.0,
    ) -> int:
        """Convert one Unity mesh to glTF primitives (one per submesh)."""
        vertices = mesh_data.vertices
        normals = mesh_data.normals
        uvs = mesh_data.uvs

        primitives: list[MeshPrimitive] = []
        for submesh_index, triangles in enumerate(mesh_data.submeshes):
            if not triangles:
                continue

            positions: list[float] = []
            primitive_normals: list[float] = []
            primitive_uvs: list[float] = []
            indices: list[int] = []
            corner_to_local: dict[int, int] = {}

            def emit(corner: int) -> int:
                if corner not in corner_to_local:
                    corner_to_local[corner] = len(corner_to_local)
                    v = vertices[corner]
                    positions.extend(
                        (v[0] * unit_scale, v[1] * unit_scale, -v[2] * unit_scale)
                    )
                    if normals:
                        n = normals[corner]
                        primitive_normals.extend((n[0], n[1], -n[2]))
                    if uvs:
                        uv = uvs[corner]
                        primitive_uvs.extend((uv[0], 1.0 - uv[1]))
                return corner_to_local[corner]

            # Unity winds triangles clockwise (left-handed); glTF is
            # counter-clockwise - flip each triangle
            for triangle in triangles:
                a, b, c = (int(corner) for corner in triangle)
                indices.extend((emit(a), emit(c), emit(b)))

            material_index: int | None = None
            if submesh_index < len(node.material_pids):
                material_index = gltf_materials.get(node.material_pids[submesh_index])

            primitives.append(
                MeshPrimitive(
                    positions=positions,
                    indices=indices,
                    normals=primitive_normals or None,
                    uvs=primitive_uvs or None,
                    material=material_index,
                )
            )

        stats.meshes += 1
        return writer.add_mesh(mesh_data.name, primitives)

    # ------------------------------------------------------------------
    # Animations
    # ------------------------------------------------------------------

    def _export_animations(
        self,
        env: Any,
        writer: GLBWriter,
        node_paths: dict[str, int],
        unit_scale: float,
        animation_name: str | None,
    ) -> list[dict[str, object]]:
        """Write every AnimationClip as a glTF animation.

        Returns manifest entries (name, duration, tracks, skipped
        bindings) for clips that produced at least one channel.
        """
        entries: list[dict[str, object]] = []
        for obj in env.objects:
            if obj.type.name != "AnimationClip":
                continue
            clip = obj.read()
            if animation_name is not None and clip.m_Name != animation_name:
                continue
            clip_tracks = extract_tracks(clip)
            if clip_tracks is None:
                self.logger.debug(f"Animation clip {clip.m_Name!r}: no TRS curves")
                continue

            channels: list[AnimationChannel] = []
            skipped = 0
            for track in clip_tracks.tracks:
                node_index = node_paths.get(track.path)
                if node_index is None:
                    skipped += 1
                    continue
                values: list[tuple[float, ...]]
                if track.property == "translation":
                    values = [
                        convert_position((v[0], v[1], v[2]), unit_scale)
                        for v in track.values
                    ]
                else:
                    values = [
                        convert_rotation((v[0], v[1], v[2], v[3])) for v in track.values
                    ]
                channels.append(
                    AnimationChannel(
                        node_index=node_index,
                        property=track.property,
                        times=track.times,
                        values=values,
                    )
                )
            if not channels:
                stats_note = "all bindings unmatched"
                self.logger.debug(
                    f"Animation clip {clip.m_Name!r}: {stats_note}, not written"
                )
                continue

            writer.add_animation(clip_tracks.name, channels)
            entries.append(
                {
                    "name": clip_tracks.name,
                    "duration": round(clip_tracks.duration, 6),
                    "tracks": len(channels),
                    "skipped_bindings": skipped,
                }
            )
            self.logger.info(
                f"Animation {clip_tracks.name!r}: {len(channels)} tracks, "
                f"{skipped} unmatched bindings"
            )
        return entries

    # ------------------------------------------------------------------
    # Scene dressing
    # ------------------------------------------------------------------

    def _add_studio_rig(
        self, writer: GLBWriter, world_bounds: list[list[float]]
    ) -> None:
        """Add a camera and three-point lighting fitted to the weapon."""
        if world_bounds[0][0] == math.inf:
            return

        center = _vec3_combine(world_bounds[0], world_bounds[1], 0.5)
        radius = max((world_bounds[1][i] - world_bounds[0][i]) / 2 for i in range(3))
        if radius <= 0:
            radius = 0.5

        camera = writer.add_camera("WOG_Camera")
        direction = _normalize3(_CAMERA_DIRECTION)
        distance = radius / math.tan(math.radians(25.0)) * 1.35
        eye = _vec3_combine(center, direction, distance)
        writer.add_camera_node("WOG_Camera", camera, eye, center)

        light_rig = (
            ("Key", (1.0, 1.5, 0.9), 3.0),
            ("Fill", (-1.4, 0.6, 0.8), 1.2),
            ("Rim", (-0.2, 0.9, -1.6), 1.8),
        )
        for name, offset, intensity in light_rig:
            light = writer.add_directional_light(
                f"WOG_Light_{name}", (1.0, 1.0, 1.0), intensity
            )
            light_eye = _vec3_combine(center, offset, radius)
            writer.add_light_node(f"WOG_Light_{name}", light, light_eye, center)

    # ------------------------------------------------------------------
    # Extra formats
    # ------------------------------------------------------------------

    def _export_extra_formats(
        self,
        glb_path: Path,
        output_dir: Path,
        formats: frozenset[str],
        export_options: ExportOptions | None,
    ) -> list[str]:
        """Convert the built GLB to the requested extra formats."""
        written: list[str] = []
        options = export_options or ExportOptions()
        for format_name in sorted(formats):
            try:
                paths = convert_format(glb_path, format_name, output_dir, options)
            except ExportError as e:
                raise SceneExportError(str(e)) from e
            written.extend(str(path) for path in paths)
            self.logger.info(f"Exported {format_name}: {paths[-1]}")
        return written

    # ------------------------------------------------------------------
    # Manifest and helper files
    # ------------------------------------------------------------------

    def _read_part_lists(self, env: Any, weapon: str, stats: SceneStats) -> None:
        """Extract removable-parts and field-strip lists from TextAssets."""
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            data = obj.read()
            name = data.m_Name
            if name not in (f"{weapon}_parts", f"{weapon}_field_strip"):
                continue
            script = data.m_Script
            if isinstance(script, bytes):
                text = script.decode("utf-8", errors="replace")
            else:
                text = str(script)
            entries = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                if ":" in line:
                    line = line.split(":", 1)[1].strip()
                if line:
                    entries.append(line)
            if name.endswith("_parts"):
                stats.parts = entries
            else:
                stats.field_strip = entries

    def _write_manifest(
        self,
        output_dir: Path,
        weapon: str,
        include_extras: frozenset[str],
        unit_scale: float,
        stats: SceneStats,
    ) -> None:
        manifest = {
            "weapon": weapon,
            "source": f"decrypted/{weapon}.unity3d",
            "unit_scale": unit_scale,
            "units": "meters",
            "state": stats.state,
            "placements": [
                {"name": p.name, "offset": list(p.offset)} for p in stats.placements
            ],
            "extras_included": sorted(include_extras),
            "camera": "WOG_Camera",
            "meshes": stats.meshes,
            "materials": stats.materials,
            "textures": stats.textures,
            "nodes": stats.nodes,
            "parts": stats.parts,
            "field_strip": stats.field_strip,
            "skipped": stats.skipped,
        }
        if stats.animations:
            manifest["animations"] = stats.animations
        if stats.exports:
            manifest["exports"] = stats.exports
        if stats.annotations:
            manifest["annotations"] = stats.annotations
        _atomic_write(
            output_dir / "scene.json",
            json.dumps(manifest, indent=2).encode("utf-8"),
        )

    def _write_blender_setup(self, output_dir: Path) -> None:
        template = (
            resources.files("wog_dump") / "resources" / "blender_setup.py"
        ).read_text(encoding="utf-8")
        _atomic_write(output_dir / "blender_setup.py", template.encode("utf-8"))


# ----------------------------------------------------------------------
# Matrix helpers live in core/transforms.py (imported above)
# ----------------------------------------------------------------------


def _accumulate_bounds(
    world_bounds: list[list[float]],
    world_matrix: list[float],
    mesh_data: _MeshData,
    unit_scale: float,
) -> None:
    """Grow glTF-space ``world_bounds`` with the mesh's world-space AABB."""
    for corner_x in (mesh_data.local_min[0], mesh_data.local_max[0]):
        for corner_y in (mesh_data.local_min[1], mesh_data.local_max[1]):
            for corner_z in (mesh_data.local_min[2], mesh_data.local_max[2]):
                world_x = (
                    world_matrix[0] * corner_x
                    + world_matrix[4] * corner_y
                    + world_matrix[8] * corner_z
                    + world_matrix[12]
                )
                world_y = (
                    world_matrix[1] * corner_x
                    + world_matrix[5] * corner_y
                    + world_matrix[9] * corner_z
                    + world_matrix[13]
                )
                world_z = (
                    world_matrix[2] * corner_x
                    + world_matrix[6] * corner_y
                    + world_matrix[10] * corner_z
                    + world_matrix[14]
                )
                mirrored = (
                    world_x * unit_scale,
                    world_y * unit_scale,
                    -world_z * unit_scale,
                )
                for axis in range(3):
                    if mirrored[axis] < world_bounds[0][axis]:
                        world_bounds[0][axis] = mirrored[axis]
                    if mirrored[axis] > world_bounds[1][axis]:
                        world_bounds[1][axis] = mirrored[axis]


def _normalize3(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(c * c for c in v))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _vec3_combine(
    a: tuple[float, float, float] | list[float],
    b: tuple[float, float, float] | list[float],
    factor: float,
) -> tuple[float, float, float]:
    """``a + b * factor`` for 3-vectors."""
    return (a[0] + b[0] * factor, a[1] + b[1] * factor, a[2] + b[2] * factor)


def _shininess_to_roughness(shininess: float) -> float:
    """Map Unity blinn-phong shininess (0..1) to a PBR roughness."""
    return max(0.05, min(1.0, 1.0 - 2.0 * shininess))


def _vector3(v: Any) -> tuple[float, float, float]:
    return (float(v.x), float(v.y), float(v.z))


def _quaternion(q: Any) -> tuple[float, float, float, float]:
    return (float(q.x), float(q.y), float(q.z), float(q.w))
