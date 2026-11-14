"""Disassembly scene states: field-strip, exploded view and grid layout.

Data source: ``docs/research-disassembly-data.md``. Every weapon bundle
carries ``<part>_in``/``<part>_out`` anchor transforms as direct children
of the weapon root: ``_out`` marks where the game puts a removed part,
``_in`` disambiguates reused part names and verifies assembled origins.
The state functions rewrite node translations in Unity space before
scene emission, so the rest of the pipeline (coordinate conversion,
mesh handling, camera rig) stays untouched.

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
    "apply_plugin_state",
    "available_states",
    "normalize_part_name",
]

SCENE_STATES = frozenset({"assembled", "field-strip", "exploded", "parts"})


def available_states() -> frozenset[str]:
    """Built-in states plus any plugin-provided state names."""
    from .extensions import load_state_providers

    return SCENE_STATES | frozenset(load_state_providers())


def apply_plugin_state(
    name: str,
    root: _GameObjectInfo,
    unit_scale: float,
    stats: Any,
    **options: Any,
) -> list[PartPlacement] | None:
    """Run a plugin state layout; None when no provider owns the name."""
    from .extensions import load_state_providers

    provider = load_state_providers().get(name)
    if provider is None:
        return None
    return provider.layout(root, unit_scale, stats, **options)


class FieldStripState:
    """Built-in field-strip state as a registry provider."""

    name = "field-strip"

    def layout(
        self, root: _GameObjectInfo, unit_scale: float, stats: Any, **options: Any
    ) -> list[PartPlacement]:
        return apply_field_strip(root, unit_scale, stats)


class ExplodedState:
    """Built-in exploded state as a registry provider."""

    name = "exploded"

    def layout(
        self, root: _GameObjectInfo, unit_scale: float, stats: Any, **options: Any
    ) -> list[PartPlacement]:
        distance = float(options.get("explode_distance", 0.35))
        return apply_exploded(root, unit_scale, distance, stats)


class PartsState:
    """Built-in grid state as a registry provider."""

    name = "parts"

    def layout(
        self, root: _GameObjectInfo, unit_scale: float, stats: Any, **options: Any
    ) -> list[PartPlacement]:
        bounds_fn = options.get("mesh_bounds_fn")
        if bounds_fn is None:
            return []
        return apply_grid_layout(
            root,
            unit_scale,
            list(options.get("part_order") or []),
            bounds_fn,
            stats,
        )


# Grid layout padding between parts, glTF meters
_GRID_PAD = 0.05
# Prefix matches shorter than this are too ambiguous to trust
_MIN_PREFIX_MATCH = 4
# Direction vectors shorter than this count as degenerate (no direction)
_EPSILON = 1e-6
# Assembled-origin vs _in-anchor distance (Unity units) counted as a
# match for the verification report; pivots differ, so this is lenient
_IN_VERIFY_TOLERANCE = 5.0


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
    chain: list[_GameObjectInfo]  # ancestor nodes, root first (root excluded)
    order: int = 0  # BFS walk index, parents before children


def _has_mesh_in_subtree(node: _GameObjectInfo) -> bool:
    if node.mesh is not None:
        return True
    return any(_has_mesh_in_subtree(child) for child in node.children)


def _walk_world(
    root: _GameObjectInfo,
) -> dict[str, list[_PartSlot]]:
    """Index every non-root node: normalized name -> candidates (BFS order).

    Direct root children named ``*_in``/``*_out`` are the removal
    anchors, not parts, so they stay out of the index. Reused names keep
    all candidates; anchor matching disambiguates them via ``_in``
    positions.
    """
    index: dict[str, list[_PartSlot]] = {}
    root_world = identity_matrix()
    queue: list[
        tuple[_GameObjectInfo, list[float], list[int], list[_GameObjectInfo], int]
    ] = [(child, root_world, [], [], 1) for child in root.children]
    order = 0
    while queue:
        node, parent_world, ancestors, chain, depth = queue.pop(0)
        local = compose_matrix(node.translation, node.rotation, node.scale)
        world = multiply_matrix(parent_world, local)
        is_anchor = depth == 1 and node.name.lower().endswith(("_in", "_out"))
        if not is_anchor and _has_mesh_in_subtree(node):
            index.setdefault(normalize_part_name(node.name), []).append(
                _PartSlot(node, world, parent_world, ancestors, chain, order)
            )
            order += 1
        child_ancestors = ancestors + [id(node)]
        child_chain = chain + [node]
        for child in node.children:
            queue.append((child, world, child_ancestors, child_chain, depth + 1))
    return index


def _collect_anchor_positions(
    root: _GameObjectInfo, suffix: str
) -> dict[str, tuple[float, float, float]]:
    """Map anchor base name -> world position of its ``_<suffix>`` node.

    Zero-position anchors are unused slots and get skipped.
    """
    targets: dict[str, tuple[float, float, float]] = {}
    for child in root.children:
        name = child.name.lower()
        if not name.endswith(suffix):
            continue
        base = normalize_part_name(child.name[: -len(suffix)])
        t = child.translation
        if abs(t[0]) + abs(t[1]) + abs(t[2]) < _EPSILON:
            continue
        local = compose_matrix(t, child.rotation, child.scale)
        targets.setdefault(base, transform_point(local, (0.0, 0.0, 0.0)))
    return targets


def _collect_removal_targets(
    root: _GameObjectInfo,
) -> dict[str, tuple[float, float, float]]:
    return _collect_anchor_positions(root, "_out")


def _collect_in_positions(
    root: _GameObjectInfo,
) -> dict[str, tuple[float, float, float]]:
    return _collect_anchor_positions(root, "_in")


def _match_slot(
    base: str,
    index: dict[str, list[_PartSlot]],
    in_positions: dict[str, tuple[float, float, float]] | None = None,
) -> _PartSlot | None:
    """Find the part node for an anchor base: exact, then prefix match.

    With several candidates (reused names) and an ``_in`` anchor
    position, the candidate whose assembled origin sits closest to the
    anchor wins; otherwise the first in tree order is kept.
    """
    candidates = index.get(base)
    if not candidates:
        norms = sorted(
            norm
            for norm in index
            if len(norm) >= _MIN_PREFIX_MATCH
            and len(base) >= _MIN_PREFIX_MATCH
            and (norm.startswith(base) or base.startswith(norm))
        )
        if not norms:
            return None
        candidates = index[norms[0]]
    if len(candidates) == 1 or not in_positions or base not in in_positions:
        return candidates[0]
    anchor = in_positions[base]
    return min(
        candidates,
        key=lambda slot: _distance_sq(
            transform_point(slot.world, (0.0, 0.0, 0.0)), anchor
        ),
    )


def _distance_sq(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _verify_in_anchor(
    base: str,
    slot: _PartSlot,
    in_positions: dict[str, tuple[float, float, float]],
    stats: Any,
) -> None:
    """Report how close the assembled origin is to the ``_in`` anchor."""
    if base not in in_positions:
        return
    origin = transform_point(slot.world, (0.0, 0.0, 0.0))
    checks = stats.anchor_checks
    if _distance_sq(origin, in_positions[base]) <= _IN_VERIFY_TOLERANCE**2:
        checks["near"] = checks.get("near", 0) + 1
    else:
        checks["far"] = checks.get("far", 0) + 1


def _fresh_parent_world(slot: _PartSlot) -> list[float]:
    """Recompose the parent world matrix after ancestors have moved."""
    world = identity_matrix()
    for node in slot.chain:
        world = multiply_matrix(
            world, compose_matrix(node.translation, node.rotation, node.scale)
        )
    return world


def _move_to(
    slot: _PartSlot,
    target_world: tuple[float, float, float],
    unit_scale: float,
    parent_world: list[float] | None = None,
) -> tuple[float, float, float]:
    """Retarget a node so its origin lands on ``target_world``.

    Returns the applied glTF-space displacement (for the manifest).
    """
    if parent_world is None:
        parent_world = slot.parent_world
    new_local = transform_point(invert_matrix(parent_world), target_world)
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

    A nested part with its own anchor takes its own removal position
    (independent of the moved parent); nested parts without an anchor
    ride along, recorded in ``stats.skipped``.
    """
    index = _walk_world(root)
    targets = _collect_removal_targets(root)
    in_positions = _collect_in_positions(root)

    matched: dict[int, tuple[str, _PartSlot]] = {}
    for base in sorted(targets):
        slot = _match_slot(base, index, in_positions)
        if slot is None:
            stats.skipped["unmatched_anchor"] = (
                stats.skipped.get("unmatched_anchor", 0) + 1
            )
            continue
        _verify_in_anchor(base, slot, in_positions, stats)
        matched.setdefault(id(slot.node), (base, slot))

    placements: list[PartPlacement] = []
    moved_ids: set[int] = set()
    nested_independent = 0
    for base, slot in sorted(matched.values(), key=lambda item: item[1].order):
        nested = any(a in moved_ids for a in slot.ancestors)
        parent_world = _fresh_parent_world(slot) if nested else None
        if nested:
            nested_independent += 1
        offset = _move_to(slot, targets[base], unit_scale, parent_world)
        placements.append(PartPlacement(name=slot.node.name, offset=offset))
        moved_ids.add(id(slot.node))

    if nested_independent:
        stats.skipped["nested_independent"] = (
            stats.skipped.get("nested_independent", 0) + nested_independent
        )
    rode = sum(
        1
        for candidates in index.values()
        for slot in candidates
        if id(slot.node) not in moved_ids
        and any(a in moved_ids for a in slot.ancestors)
    )
    if rode:
        stats.skipped["rode_along"] = stats.skipped.get("rode_along", 0) + rode
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
    else moves radially away from the parts centroid. Nested parts with
    their own anchor explode independently; the rest ride with their
    parent.
    """
    index = _walk_world(root)
    targets = _collect_removal_targets(root)
    in_positions = _collect_in_positions(root)

    anchor_target_by_id: dict[int, tuple[float, float, float]] = {}
    for base in sorted(targets):
        slot = _match_slot(base, index, in_positions)
        if slot is None:
            stats.skipped["unmatched_anchor"] = (
                stats.skipped.get("unmatched_anchor", 0) + 1
            )
            continue
        _verify_in_anchor(base, slot, in_positions, stats)
        anchor_target_by_id[id(slot.node)] = targets[base]

    all_slots = [slot for candidates in index.values() for slot in candidates]
    outer, _ = _apply_containment(all_slots)

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
    moved_ids: set[int] = set()
    rode = 0
    nested_independent = 0
    for slot in sorted(all_slots, key=lambda s: s.order):
        nid = id(slot.node)
        nested = any(a in moved_ids for a in slot.ancestors)
        if nested and nid not in anchor_target_by_id:
            rode += 1
            continue
        origin = origins.get(nid) or transform_point(slot.world, (0.0, 0.0, 0.0))
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
        parent_world = _fresh_parent_world(slot) if nested else None
        if nested:
            nested_independent += 1
        offset = _move_to(slot, target_world, unit_scale, parent_world)
        placements.append(PartPlacement(name=slot.node.name, offset=offset))
        moved_ids.add(nid)

    if rode:
        stats.skipped["rode_along"] = stats.skipped.get("rode_along", 0) + rode
    if nested_independent:
        stats.skipped["nested_independent"] = (
            stats.skipped.get("nested_independent", 0) + nested_independent
        )
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
        candidates = index.get(norm)
        if not candidates:
            stats.skipped["grid_unmatched"] = stats.skipped.get("grid_unmatched", 0) + 1
            continue
        seen_names.add(norm)
        entries.append(candidates[0])

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
