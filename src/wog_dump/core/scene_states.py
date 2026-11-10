"""Disassembly scene states: field-strip, exploded view and grid layout.

Data source: ``docs/research-disassembly-data.md``. Every weapon bundle
carries ``<part>_in``/``<part>_out`` anchor transforms as direct children
of the weapon root, and ``_out`` marks where the game puts a removed
part. The state functions rewrite node translations in Unity space
before scene emission, so the rest of the pipeline (coordinate
conversion, mesh handling, camera rig) stays untouched.

This module never imports from ``scene.py``: the tree type arrives as
a structural annotation, keeping the dependency direction one-way
(scene -> scene_states).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .transforms import (
    compose_matrix,
    identity_matrix,
    invert_matrix,
    multiply_matrix,
    transform_point,
)

if TYPE_CHECKING:
    from .scene import _GameObjectInfo

__all__ = [
    "PartPlacement",
    "SCENE_STATES",
    "apply_exploded",
    "apply_field_strip",
    "apply_grid_layout",
    "normalize_part_name",
]

SCENE_STATES = frozenset({"assembled", "field-strip", "exploded", "parts"})

# Grid layout padding between parts, glTF meters
_GRID_PAD = 0.05
# Prefix matches shorter than this are too ambiguous to trust
_MIN_PREFIX_MATCH = 4
# Direction vectors shorter than this count as degenerate (no direction)
_EPSILON = 1e-6


@dataclass
class PartPlacement:
    """One part displacement, recorded for the scene manifest."""

    name: str
    offset: tuple[float, float, float]  # glTF meters, Z already mirrored


def normalize_part_name(name: str) -> str:
    """Lowercase and strip leading underscores for anchor/part matching."""
    return name.lower().lstrip("_")


def _gltf_delta(
    delta: tuple[float, float, float], unit_scale: float
) -> tuple[float, float, float]:
    """Unity-space displacement to glTF meters (same convention as
    ``scene.convert_position``)."""
    return (delta[0] * unit_scale, delta[1] * unit_scale, -delta[2] * unit_scale)


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Normalize a direction; a degenerate vector maps to +Y."""
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < _EPSILON:
        return (0.0, 1.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _sub(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


@dataclass
class _PartSlot:
    """A tree node with the world matrices needed to retarget it."""

    node: _GameObjectInfo
    world: list[float]
    parent_world: list[float]
    ancestors: list[int]  # ids of ancestor nodes, root first
    order: int = 0  # BFS walk index, parents before children


def _has_mesh_in_subtree(node: _GameObjectInfo) -> bool:
    if node.mesh is not None:
        return True
    return any(_has_mesh_in_subtree(child) for child in node.children)


def _walk_world(
    root: _GameObjectInfo,
) -> dict[str, _PartSlot]:
    """Index every non-root node: normalized name -> slot (BFS order).

    Direct root children named ``*_in``/``*_out`` are the removal
    anchors, not parts, so they stay out of the index.
    """
    index: dict[str, _PartSlot] = {}
    root_world = identity_matrix()
    queue: list[tuple[_GameObjectInfo, list[float], list[int], int]] = [
        (child, root_world, [], 1) for child in root.children
    ]
    order = 0
    while queue:
        node, parent_world, ancestors, depth = queue.pop(0)
        local = compose_matrix(node.translation, node.rotation, node.scale)
        world = multiply_matrix(parent_world, local)
        is_anchor = depth == 1 and node.name.lower().endswith(("_in", "_out"))
        if not is_anchor and _has_mesh_in_subtree(node):
            index.setdefault(
                normalize_part_name(node.name),
                _PartSlot(node, world, parent_world, ancestors, order),
            )
            order += 1
        child_ancestors = ancestors + [id(node)]
        for child in node.children:
            queue.append((child, world, child_ancestors, depth + 1))
    return index


def _collect_removal_targets(
    root: _GameObjectInfo,
) -> dict[str, tuple[float, float, float]]:
    """Map anchor base name -> world position of its ``_out`` node.

    Zero-position anchors are unused slots and get skipped.
    """
    targets: dict[str, tuple[float, float, float]] = {}
    for child in root.children:
        name = child.name.lower()
        if not name.endswith("_out"):
            continue
        base = normalize_part_name(child.name[:-4])
        t = child.translation
        if abs(t[0]) + abs(t[1]) + abs(t[2]) < _EPSILON:
            continue
        local = compose_matrix(t, child.rotation, child.scale)
        targets.setdefault(base, transform_point(local, (0.0, 0.0, 0.0)))
    return targets


def _match_slot(base: str, index: dict[str, _PartSlot]) -> _PartSlot | None:
    """Find the part node for an anchor base: exact, then prefix match."""
    if base in index:
        return index[base]
    candidates = sorted(
        norm
        for norm in index
        if len(norm) >= _MIN_PREFIX_MATCH
        and len(base) >= _MIN_PREFIX_MATCH
        and (norm.startswith(base) or base.startswith(norm))
    )
    if candidates:
        return index[candidates[0]]
    return None


def _move_to(
    slot: _PartSlot, target_world: tuple[float, float, float], unit_scale: float
) -> tuple[float, float, float]:
    """Retarget a node so its origin lands on ``target_world``.

    Returns the applied glTF-space displacement (for the manifest).
    """
    new_local = transform_point(invert_matrix(slot.parent_world), target_world)
    old = slot.node.translation
    delta = (
        new_local[0] - old[0],
        new_local[1] - old[1],
        new_local[2] - old[2],
    )
    slot.node.translation = new_local
    return _gltf_delta(delta, unit_scale)


def _apply_containment(
    slots: list[_PartSlot],
) -> tuple[list[_PartSlot], int]:
    """Keep only outermost slots; nested ones ride with their parent.

    ``slots`` must come in tree order (parents first), which
    ``_walk_world`` guarantees.
    """
    moved_ids: set[int] = set()
    outer: list[_PartSlot] = []
    rode = 0
    for slot in sorted(slots, key=lambda s: s.order):
        if any(a in moved_ids for a in slot.ancestors):
            rode += 1
            continue
        moved_ids.add(id(slot.node))
        outer.append(slot)
    return outer, rode


def _subtree_bounds_gl(
    slot: _PartSlot,
    unit_scale: float,
    mesh_bounds_fn: Callable[[Any], Any],
) -> tuple[list[float], list[float]] | None:
    """World-space glTF bounds of a slot's subtree, or None without meshes."""
    bounds: list[list[float]] = [[math.inf] * 3, [-math.inf] * 3]
    found = False
    queue: list[tuple[_GameObjectInfo, list[float]]] = [(slot.node, slot.parent_world)]
    while queue:
        node, parent_world = queue.pop(0)
        world = multiply_matrix(
            parent_world, compose_matrix(node.translation, node.rotation, node.scale)
        )
        if node.mesh is not None:
            mesh = mesh_bounds_fn(node.mesh)
            if mesh is not None and getattr(mesh, "local_min", None):
                found = True
                _accumulate(bounds, world, mesh.local_min, mesh.local_max, unit_scale)
        for child in node.children:
            queue.append((child, world))
    if not found:
        return None
    return (bounds[0], bounds[1])


def _accumulate(
    bounds: list[list[float]],
    world: list[float],
    local_min: list[float],
    local_max: list[float],
    unit_scale: float,
) -> None:
    """Grow glTF-space bounds with one mesh AABB (mirrors
    ``scene._accumulate_bounds``)."""
    for cx in (local_min[0], local_max[0]):
        for cy in (local_min[1], local_max[1]):
            for cz in (local_min[2], local_max[2]):
                p = transform_point(world, (cx, cy, cz))
                gl = (p[0] * unit_scale, p[1] * unit_scale, -p[2] * unit_scale)
                for axis in range(3):
                    if gl[axis] < bounds[0][axis]:
                        bounds[0][axis] = gl[axis]
                    if gl[axis] > bounds[1][axis]:
                        bounds[1][axis] = gl[axis]


def apply_field_strip(
    root: _GameObjectInfo,
    unit_scale: float,
    stats: Any,
) -> list[PartPlacement]:
    """Move every anchored part to its game removal position.

    Parts nested inside an already moved part keep their relative
    position (they ride along), recorded in ``stats.skipped``.
    """
    index = _walk_world(root)
    targets = _collect_removal_targets(root)

    slots: list[tuple[str, _PartSlot]] = []
    for base in sorted(targets):
        slot = _match_slot(base, index)
        if slot is None:
            stats.skipped["unmatched_anchor"] = (
                stats.skipped.get("unmatched_anchor", 0) + 1
            )
            continue
        slots.append((base, slot))

    outer, rode = _apply_containment([slot for _, slot in slots])
    if rode:
        stats.skipped["rode_along"] = stats.skipped.get("rode_along", 0) + rode

    placements: list[PartPlacement] = []
    moved_ids = {id(slot.node) for slot in outer}
    for base, slot in slots:
        if id(slot.node) not in moved_ids:
            continue
        target = targets[base]
        offset = _move_to(slot, target, unit_scale)
        placements.append(PartPlacement(name=slot.node.name, offset=offset))
    return placements


def apply_exploded(
    root: _GameObjectInfo,
    unit_scale: float,
    explode_distance: float,
    stats: Any,
) -> list[PartPlacement]:
    """Push every top-level mesh part away from the assembled pose.

    ``explode_distance`` is the displacement magnitude in glTF meters.
    Anchored parts move along their game removal direction, everything
    else moves radially away from the parts centroid. Nested parts ride
    with their parent.
    """
    index = _walk_world(root)
    targets = _collect_removal_targets(root)

    anchor_target_by_id: dict[int, tuple[float, float, float]] = {}
    for base in sorted(targets):
        slot = _match_slot(base, index)
        if slot is None:
            stats.skipped["unmatched_anchor"] = (
                stats.skipped.get("unmatched_anchor", 0) + 1
            )
            continue
        anchor_target_by_id[id(slot.node)] = targets[base]

    outer, rode = _apply_containment(list(index.values()))
    if rode:
        stats.skipped["rode_along"] = stats.skipped.get("rode_along", 0) + rode

    origins = {
        id(slot.node): transform_point(slot.world, (0.0, 0.0, 0.0)) for slot in outer
    }
    centroid = [0.0, 0.0, 0.0]
    for pos in origins.values():
        for axis in range(3):
            centroid[axis] += pos[axis]
    count = max(len(origins), 1)
    centroid = [c / count for c in centroid]

    distance_unity = explode_distance / unit_scale if unit_scale > 0 else 0.0
    centroid_t = (centroid[0], centroid[1], centroid[2])
    placements: list[PartPlacement] = []
    for slot in outer:
        nid = id(slot.node)
        origin = origins[nid]
        candidate: tuple[float, float, float] | None = None
        if nid in anchor_target_by_id:
            removal = _sub(anchor_target_by_id[nid], origin)
            length = math.sqrt(sum(c * c for c in removal))
            if length >= _EPSILON:
                candidate = removal
        if candidate is None:
            candidate = _sub(origin, centroid_t)
        direction = _unit(candidate)
        target_world = (
            origin[0] + direction[0] * distance_unity,
            origin[1] + direction[1] * distance_unity,
            origin[2] + direction[2] * distance_unity,
        )
        offset = _move_to(slot, target_world, unit_scale)
        placements.append(PartPlacement(name=slot.node.name, offset=offset))
    return placements


def apply_grid_layout(
    root: _GameObjectInfo,
    unit_scale: float,
    part_order: list[str],
    mesh_bounds_fn: Callable[[Any], Any],
    stats: Any,
) -> list[PartPlacement]:
    """Lay out the weapon's parts in a grid, bottoms on the y=0 plane.

    ``part_order`` is the ``<weapon>_parts`` list (game removal order).
    Nested parts stay inside their parent's cell.
    """
    index = _walk_world(root)

    entries: list[_PartSlot] = []
    seen_names: set[str] = set()
    for name in part_order:
        norm = normalize_part_name(name)
        if norm in seen_names:
            continue
        slot = index.get(norm)
        if slot is None:
            stats.skipped["grid_unmatched"] = stats.skipped.get("grid_unmatched", 0) + 1
            continue
        seen_names.add(norm)
        entries.append(slot)

    outer, rode = _apply_containment(entries)
    if rode:
        stats.skipped["rode_along"] = stats.skipped.get("rode_along", 0) + rode

    laid_out: list[tuple[_PartSlot, list[float], list[float]]] = []
    for slot in outer:
        bounds = _subtree_bounds_gl(slot, unit_scale, mesh_bounds_fn)
        if bounds is None:
            stats.skipped["grid_no_bounds"] = stats.skipped.get("grid_no_bounds", 0) + 1
            continue
        laid_out.append((slot, bounds[0], bounds[1]))

    if not laid_out:
        return []

    widths = [bmax[0] - bmin[0] for _, bmin, bmax in laid_out]
    depths = [bmax[2] - bmin[2] for _, bmin, bmax in laid_out]
    columns = max(1, math.ceil(math.sqrt(len(laid_out))))

    # Cell centers up front, so every part moves exactly once (a second
    # pass would read stale world matrices)
    cell_centers: list[tuple[float, float]] = []
    x_cursor = 0.0
    z_cursor = 0.0
    total_width = 0.0
    for start in range(0, len(laid_out), columns):
        row = range(start, min(start + columns, len(laid_out)))
        row_depth = max(depths[i] for i in row)
        x_cursor = 0.0
        for i in row:
            cell_centers.append((x_cursor + widths[i] / 2, z_cursor + row_depth / 2))
            x_cursor += widths[i] + _GRID_PAD
        total_width = max(total_width, x_cursor)
        z_cursor += row_depth + _GRID_PAD
    half_width = total_width / 2
    half_depth = z_cursor / 2

    placements: list[PartPlacement] = []
    for (slot, bmin, bmax), (cell_x, cell_z) in zip(laid_out, cell_centers):
        center = ((bmin[0] + bmax[0]) / 2, (bmin[2] + bmax[2]) / 2)
        # Grid centered at the origin, part bottoms on the y=0 plane
        delta_gl = (
            cell_x - half_width - center[0],
            -bmin[1],
            cell_z - half_depth - center[1],
        )
        origin = transform_point(slot.world, (0.0, 0.0, 0.0))
        target_world = (
            origin[0] + delta_gl[0] / unit_scale,
            origin[1] + delta_gl[1] / unit_scale,
            origin[2] - delta_gl[2] / unit_scale,
        )
        offset = _move_to(slot, target_world, unit_scale)
        placements.append(PartPlacement(name=slot.node.name, offset=offset))
    return placements
