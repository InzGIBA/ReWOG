"""Showcase scene state: a circular carousel display layout.

Registered through the ``wog_dump.scene_states`` entry-point group.
``wog-dump build-scene <weapon> --state showcase`` arranges every
outermost part evenly on a circle in the glTF XZ plane, bottoms on the
y=0 plane, ordered by the game removal order when available.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from wog_dump.core.scene_states import PartPlacement, normalize_part_name
from wog_dump.core.transforms import (
    compose_matrix,
    identity_matrix,
    invert_matrix,
    multiply_matrix,
    transform_point,
)

if TYPE_CHECKING:
    from wog_dump.core.scene import _GameObjectInfo

__all__ = ["ShowcaseState"]

# Footprint padding along the circumference, glTF meters (matches the grid)
_PAD = 0.05

Bounds = tuple[list[float], list[float]]


@dataclass
class _Slot:
    """A part node with the world matrices captured before any move."""

    node: _GameObjectInfo
    world: list[float]
    parent_world: list[float]
    ancestors: list[int]
    order: int


def _has_mesh(node: _GameObjectInfo) -> bool:
    if node.mesh is not None:
        return True
    return any(_has_mesh(child) for child in node.children)


def _walk(root: _GameObjectInfo) -> list[_Slot]:
    """BFS part nodes (mesh in subtree), parents before children.

    Mirrors ``scene_states._walk_world``: the root itself is never a
    part and direct ``*_in``/``*_out`` children are removal anchors.
    """
    slots: list[_Slot] = []
    queue: deque[tuple[_GameObjectInfo, list[float], list[int], int]] = deque(
        (child, identity_matrix(), [], 1) for child in root.children
    )
    order = 0
    while queue:
        node, parent_world, ancestors, depth = queue.popleft()
        local = compose_matrix(node.translation, node.rotation, node.scale)
        world = multiply_matrix(parent_world, local)
        anchor = depth == 1 and node.name.lower().endswith(("_in", "_out"))
        if not anchor and _has_mesh(node):
            slots.append(_Slot(node, world, parent_world, ancestors, order))
            order += 1
        for child in node.children:
            queue.append((child, world, ancestors + [id(node)], depth + 1))
    return slots


def _outermost(slots: list[_Slot]) -> tuple[list[_Slot], int]:
    """Containment in tree order: nested parts ride with their parent."""
    moved: set[int] = set()
    outer: list[_Slot] = []
    rode = 0
    for slot in slots:
        if any(a in moved for a in slot.ancestors):
            rode += 1
            continue
        moved.add(id(slot.node))
        outer.append(slot)
    return outer, rode


def _entries(slots: list[_Slot], part_order: list[str], stats: Any) -> list[_Slot]:
    """The layout universe: the game parts list when available.

    Mirrors the built-in grid: containment is computed within the
    listed parts only, so a grouping node absent from the list (the
    ak47 ``receiver`` carries every other part) does not collapse the
    display into one cell. Without a list, every part is a candidate.
    """
    if not part_order:
        return slots
    index: dict[str, list[_Slot]] = {}
    for slot in slots:
        index.setdefault(normalize_part_name(slot.node.name), []).append(slot)
    entries: list[_Slot] = []
    seen: set[int] = set()
    for name in part_order:
        candidates = index.get(normalize_part_name(name))
        if not candidates:
            stats.skipped["showcase_unmatched"] = (
                stats.skipped.get("showcase_unmatched", 0) + 1
            )
            continue
        slot = candidates[0]
        if id(slot.node) not in seen:
            seen.add(id(slot.node))
            entries.append(slot)
    return entries


def _subtree_bounds_gl(
    slot: _Slot,
    unit_scale: float,
    mesh_bounds_fn: Callable[[Any], Any] | None,
) -> Bounds | None:
    """World glTF bounds of the subtree (mirrors ``_subtree_bounds_gl``)."""
    if mesh_bounds_fn is None:
        return None
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    found = False
    queue: deque[tuple[_GameObjectInfo, list[float]]] = deque(
        [(slot.node, slot.parent_world)]
    )
    while queue:
        node, parent_world = queue.popleft()
        world = multiply_matrix(
            parent_world, compose_matrix(node.translation, node.rotation, node.scale)
        )
        if node.mesh is not None:
            mesh = mesh_bounds_fn(node.mesh)
            if mesh is not None and getattr(mesh, "local_min", None):
                found = True
                for cx in (mesh.local_min[0], mesh.local_max[0]):
                    for cy in (mesh.local_min[1], mesh.local_max[1]):
                        for cz in (mesh.local_min[2], mesh.local_max[2]):
                            p = transform_point(world, (cx, cy, cz))
                            gl = (
                                p[0] * unit_scale,
                                p[1] * unit_scale,
                                -p[2] * unit_scale,
                            )
                            for axis in range(3):
                                lo[axis] = min(lo[axis], gl[axis])
                                hi[axis] = max(hi[axis], gl[axis])
        for child in node.children:
            queue.append((child, world))
    if not found:
        return None
    return lo, hi


def _circle_radius(laid: list[tuple[_Slot, Bounds]]) -> float:
    """Radius from the footprint circumference, floored by the largest
    footprint diagonal; a lone part sits at the origin."""
    if len(laid) == 1:
        return 0.0
    circumference = 0.0
    min_radius = 0.0
    for _, (lo, hi) in laid:
        width = hi[0] - lo[0]
        depth = hi[2] - lo[2]
        circumference += max(width, depth) + _PAD
        min_radius = max(min_radius, math.hypot(width, depth))
    return max(circumference / (2.0 * math.pi), min_radius)


class ShowcaseState:
    """Even circular carousel around the origin, bottoms on y=0."""

    name: str = "showcase"

    def layout(
        self,
        root: _GameObjectInfo,
        unit_scale: float,
        stats: Any,
        **options: Any,
    ) -> list[PartPlacement]:
        """Rewrite translations in Unity space; return glTF placements.

        Options: ``part_order`` (game removal order), ``mesh_bounds_fn``
        (Unity mesh -> local AABB). ``explode_distance`` is accepted for
        signature parity with the built-ins and ignored.
        """
        part_order: list[str] = list(options.get("part_order") or [])
        mesh_bounds_fn: Callable[[Any], Any] | None = options.get("mesh_bounds_fn")

        slots = _walk(root)
        entries = _entries(slots, part_order, stats)
        display, rode = _outermost(entries)
        if rode:
            stats.skipped["rode_along"] = stats.skipped.get("rode_along", 0) + rode

        laid: list[tuple[_Slot, Bounds]] = []
        for slot in display:
            bounds = _subtree_bounds_gl(slot, unit_scale, mesh_bounds_fn)
            if bounds is None:
                stats.skipped["showcase_no_bounds"] = (
                    stats.skipped.get("showcase_no_bounds", 0) + 1
                )
                continue
            laid.append((slot, bounds))
        if not laid:
            return []

        # All bounds up front: moves never read stale world matrices
        radius = _circle_radius(laid)
        count = len(laid)
        placements: list[PartPlacement] = []
        for i, (slot, (lo, hi)) in enumerate(laid):
            angle = 2.0 * math.pi * i / count
            delta_gl = (
                radius * math.cos(angle) - (lo[0] + hi[0]) / 2.0,
                -lo[1],
                radius * math.sin(angle) - (lo[2] + hi[2]) / 2.0,
            )
            origin = transform_point(slot.world, (0.0, 0.0, 0.0))
            target_gl = (
                origin[0] * unit_scale + delta_gl[0],
                origin[1] * unit_scale + delta_gl[1],
                -origin[2] * unit_scale + delta_gl[2],
            )
            target_unity = (
                target_gl[0] / unit_scale,
                target_gl[1] / unit_scale,
                -target_gl[2] / unit_scale,
            )
            new_local = transform_point(invert_matrix(slot.parent_world), target_unity)
            old = slot.node.translation
            slot.node.translation = new_local
            placements.append(
                PartPlacement(
                    name=slot.node.name,
                    offset=(
                        (new_local[0] - old[0]) * unit_scale,
                        (new_local[1] - old[1]) * unit_scale,
                        -(new_local[2] - old[2]) * unit_scale,
                    ),
                )
            )
        return placements
