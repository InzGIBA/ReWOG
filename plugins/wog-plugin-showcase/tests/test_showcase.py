"""Tests for the showcase carousel scene state (no installation needed)."""

from __future__ import annotations

import math
from collections import deque
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from wog_plugin_showcase import ShowcaseState

from wog_dump.core.extensions import StateProvider
from wog_dump.core.scene import _GameObjectInfo
from wog_dump.core.scene_states import PartPlacement
from wog_dump.core.transforms import (
    compose_matrix,
    identity_matrix,
    multiply_matrix,
    transform_point,
)

BoundsFn = Callable[[Any], Any]


def make_mesh() -> SimpleNamespace:
    return SimpleNamespace()


def cube_bounds(mesh: Any) -> SimpleNamespace:
    return SimpleNamespace(local_min=[-1.0, -1.0, -1.0], local_max=[1.0, 1.0, 1.0])


def make_node(
    name: str,
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    mesh: Any | None = None,
    children: list[_GameObjectInfo] | None = None,
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> _GameObjectInfo:
    return _GameObjectInfo(
        name=name,
        mesh=mesh,
        translation=translation,
        rotation=rotation,
        scale=scale,
        children=children if children is not None else [],
    )


def run_layout(
    root: _GameObjectInfo, unit_scale: float = 1.0, **overrides: Any
) -> tuple[list[PartPlacement], SimpleNamespace]:
    stats = SimpleNamespace(skipped={})
    options: dict[str, Any] = {
        "explode_distance": 0.5,
        "part_order": [],
        "mesh_bounds_fn": cube_bounds,
    }
    options.update(overrides)
    placements = ShowcaseState().layout(root, unit_scale, stats, **options)
    return placements, stats


def world_matrix(root: _GameObjectInfo, target: _GameObjectInfo) -> list[float]:
    def visit(node: _GameObjectInfo, parent_world: list[float]) -> list[float] | None:
        world = multiply_matrix(
            parent_world, compose_matrix(node.translation, node.rotation, node.scale)
        )
        if node is target:
            return world
        for child in node.children:
            found = visit(child, world)
            if found is not None:
                return found
        return None

    result = visit(root, identity_matrix())
    assert result is not None
    return result


def subtree_bounds_gl(
    target: _GameObjectInfo,
    world: list[float],
    unit_scale: float,
    bounds_fn: BoundsFn = cube_bounds,
) -> tuple[list[float], list[float]]:
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    queue: deque[tuple[_GameObjectInfo, list[float]]] = deque([(target, world)])
    while queue:
        node, node_world = queue.popleft()
        if node.mesh is not None:
            bounds = bounds_fn(node.mesh)
            if bounds is not None and bounds.local_min:
                for cx in (bounds.local_min[0], bounds.local_max[0]):
                    for cy in (bounds.local_min[1], bounds.local_max[1]):
                        for cz in (bounds.local_min[2], bounds.local_max[2]):
                            p = transform_point(node_world, (cx, cy, cz))
                            gl = (
                                p[0] * unit_scale,
                                p[1] * unit_scale,
                                -p[2] * unit_scale,
                            )
                            for axis in range(3):
                                lo[axis] = min(lo[axis], gl[axis])
                                hi[axis] = max(hi[axis], gl[axis])
        for child in node.children:
            child_world = multiply_matrix(
                node_world,
                compose_matrix(child.translation, child.rotation, child.scale),
            )
            queue.append((child, child_world))
    return lo, hi


def bounds_gl(
    root: _GameObjectInfo,
    target: _GameObjectInfo,
    unit_scale: float = 1.0,
    bounds_fn: BoundsFn = cube_bounds,
) -> tuple[list[float], list[float]]:
    return subtree_bounds_gl(target, world_matrix(root, target), unit_scale, bounds_fn)


def center_xz(bounds: tuple[list[float], list[float]]) -> tuple[float, float]:
    lo, hi = bounds
    return ((lo[0] + hi[0]) / 2.0, (lo[2] + hi[2]) / 2.0)


def test_protocol_conformance() -> None:
    provider = ShowcaseState()
    assert isinstance(provider, StateProvider)
    assert provider.name == "showcase"
    assert callable(provider.layout)


def test_single_part_centered_near_origin_bottom_zero() -> None:
    part = make_node("Receiver", translation=(5.0, 3.0, -2.0), mesh=make_mesh())
    root = make_node("Weapon", children=[part])
    placements, stats = run_layout(root)
    assert [p.name for p in placements] == ["Receiver"]
    assert part.translation == pytest.approx((0.0, 1.0, 0.0))
    lo, hi = bounds_gl(root, part)
    cx, cz = center_xz((lo, hi))
    assert cx == pytest.approx(0.0, abs=1e-9)
    assert cz == pytest.approx(0.0, abs=1e-9)
    assert lo[1] == pytest.approx(0.0, abs=1e-9)
    assert stats.skipped == {}


def test_parts_evenly_spaced_on_circle() -> None:
    count = 5
    parts = [
        make_node(
            f"Part{i}",
            translation=(3.0 * i, float(i), -2.0 * i),
            mesh=make_mesh(),
        )
        for i in range(count)
    ]
    root = make_node("Weapon", children=parts)
    placements, stats = run_layout(root)
    assert [p.name for p in placements] == [f"Part{i}" for i in range(count)]

    expected_radius = max(count * (2.0 + 0.05) / (2.0 * math.pi), math.hypot(2.0, 2.0))
    centers = []
    for part in parts:
        lo, hi = bounds_gl(root, part)
        assert lo[1] == pytest.approx(0.0, abs=1e-9)
        centers.append(center_xz((lo, hi)))
    for cx, cz in centers:
        assert math.hypot(cx, cz) == pytest.approx(expected_radius, rel=1e-9)
    for i, (cx, cz) in enumerate(centers):
        angle = math.atan2(cz, cx) % (2.0 * math.pi)
        assert angle == pytest.approx(2.0 * math.pi * i / count, abs=1e-9)
    assert stats.skipped == {}


def test_nested_part_rides_with_parent() -> None:
    bolt = make_node("Bolt", translation=(0.5, 0.25, 0.0), mesh=make_mesh())
    receiver = make_node(
        "Receiver", translation=(2.0, 1.0, 0.0), mesh=make_mesh(), children=[bolt]
    )
    root = make_node("Weapon", children=[receiver])
    bolt_before = bolt.translation
    placements, stats = run_layout(root)
    assert [p.name for p in placements] == ["Receiver"]
    assert bolt.translation == bolt_before
    assert stats.skipped.get("rode_along") == 1
    parent_origin = transform_point(world_matrix(root, receiver), (0.0, 0.0, 0.0))
    bolt_origin = transform_point(world_matrix(root, bolt), (0.0, 0.0, 0.0))
    assert bolt_origin[0] - parent_origin[0] == pytest.approx(0.5)
    assert bolt_origin[1] - parent_origin[1] == pytest.approx(0.25)


def test_part_order_respected() -> None:
    alpha = make_node("Alpha", translation=(1.0, 0.0, 0.0), mesh=make_mesh())
    beta = make_node("Beta", translation=(2.0, 0.0, 0.0), mesh=make_mesh())
    gamma = make_node("Gamma", translation=(3.0, 0.0, 0.0), mesh=make_mesh())
    root = make_node("Weapon", children=[alpha, beta, gamma])
    placements, stats = run_layout(root, part_order=["GAMMA", "_alpha", "nonexistent"])
    # Only listed parts are displayed (built-in grid semantics); Beta
    # is absent from the list and stays put.
    assert [p.name for p in placements] == ["Gamma", "Alpha"]
    assert stats.skipped.get("showcase_unmatched") == 1
    assert beta.translation == (2.0, 0.0, 0.0)
    radius = math.hypot(2.0, 2.0)
    for i, part in enumerate([gamma, alpha]):
        cx, cz = center_xz(bounds_gl(root, part))
        assert math.hypot(cx, cz) == pytest.approx(radius, rel=1e-9)
        angle = math.atan2(cz, cx) % (2.0 * math.pi)
        assert angle == pytest.approx(2.0 * math.pi * i / 2.0, abs=1e-9)


def test_no_bounds_parts_skipped_with_counter() -> None:
    def selective_bounds(mesh: Any) -> SimpleNamespace | None:
        if getattr(mesh, "solid", False):
            return cube_bounds(mesh)
        return None

    good = make_node(
        "Good", translation=(3.0, 0.0, 0.0), mesh=SimpleNamespace(solid=True)
    )
    bad = make_node(
        "Bad", translation=(-3.0, 0.0, 0.0), mesh=SimpleNamespace(solid=False)
    )
    root = make_node("Weapon", children=[good, bad])
    placements, stats = run_layout(root, mesh_bounds_fn=selective_bounds)
    assert [p.name for p in placements] == ["Good"]
    assert stats.skipped.get("showcase_no_bounds") == 1
    assert bad.translation == (-3.0, 0.0, 0.0)


def test_all_parts_without_bounds_return_empty() -> None:
    parts = [
        make_node(f"Part{i}", translation=(float(i), 0.0, 0.0), mesh=make_mesh())
        for i in range(2)
    ]
    root = make_node("Weapon", children=parts)
    placements, stats = run_layout(root, mesh_bounds_fn=lambda mesh: None)
    assert placements == []
    assert stats.skipped.get("showcase_no_bounds") == 2
    assert parts[0].translation == (0.0, 0.0, 0.0)
    assert parts[1].translation == (1.0, 0.0, 0.0)


def test_empty_local_min_counts_as_no_bounds() -> None:
    part = make_node("Receiver", mesh=make_mesh())
    root = make_node("Weapon", children=[part])
    placements, stats = run_layout(
        root,
        mesh_bounds_fn=lambda mesh: SimpleNamespace(local_min=[], local_max=[]),
    )
    assert placements == []
    assert stats.skipped.get("showcase_no_bounds") == 1


def test_offsets_in_gltf_meters_with_mirrored_z() -> None:
    part = make_node("Receiver", translation=(4.0, 6.0, 8.0), mesh=make_mesh())
    root = make_node("Weapon", children=[part])
    placements, stats = run_layout(root, unit_scale=0.5)
    assert len(placements) == 1
    assert placements[0].offset == pytest.approx((-2.0, -2.5, 4.0))
    assert part.translation == pytest.approx((0.0, 1.0, 0.0))
    lo, hi = bounds_gl(root, part, unit_scale=0.5)
    assert lo[1] == pytest.approx(0.0, abs=1e-9)
    cx, cz = center_xz((lo, hi))
    assert cx == pytest.approx(0.0, abs=1e-9)
    assert cz == pytest.approx(0.0, abs=1e-9)


def test_transformed_subtree_moves_as_one_part() -> None:
    part = make_node("Barrel", translation=(1.0, 0.0, 0.0), mesh=make_mesh())
    group = make_node(
        "Group",
        translation=(1.0, 2.0, 3.0),
        rotation=(0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)),
        scale=(2.0, 2.0, 2.0),
        children=[part],
    )
    root = make_node("Weapon", children=[group])
    part_before = part.translation
    placements, stats = run_layout(root)
    assert [p.name for p in placements] == ["Group"]
    assert part.translation == part_before
    assert stats.skipped.get("rode_along") == 1
    lo, hi = bounds_gl(root, group)
    cx, cz = center_xz((lo, hi))
    assert cx == pytest.approx(0.0, abs=1e-9)
    assert cz == pytest.approx(0.0, abs=1e-9)
    assert lo[1] == pytest.approx(0.0, abs=1e-9)


def test_part_under_nonidentity_parent_world() -> None:
    part = make_node("Barrel", translation=(1.0, 0.0, 0.0), mesh=make_mesh())
    anchor = make_node(
        "group_out",
        translation=(1.0, 2.0, 3.0),
        rotation=(0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)),
        scale=(2.0, 2.0, 2.0),
        children=[part],
    )
    root = make_node("Weapon", children=[anchor])
    old_local = part.translation
    placements, stats = run_layout(root)
    assert [p.name for p in placements] == ["Barrel"]
    assert anchor.translation == (1.0, 2.0, 3.0)
    lo, hi = bounds_gl(root, part)
    cx, cz = center_xz((lo, hi))
    assert cx == pytest.approx(0.0, abs=1e-9)
    assert cz == pytest.approx(0.0, abs=1e-9)
    assert lo[1] == pytest.approx(0.0, abs=1e-9)
    delta = tuple(new - old for new, old in zip(part.translation, old_local))
    assert placements[0].offset == pytest.approx((delta[0], delta[1], -delta[2]))


def test_deterministic_output() -> None:
    def build() -> _GameObjectInfo:
        return make_node(
            "Weapon",
            children=[
                make_node(
                    f"Part{i}",
                    translation=(float(i), 2.0 * i, -float(i)),
                    mesh=make_mesh(),
                )
                for i in range(4)
            ],
        )

    first_root, second_root = build(), build()
    first, _ = run_layout(first_root, part_order=["part2"])
    second, _ = run_layout(second_root, part_order=["part2"])
    assert first == second
    for a, b in zip(first_root.children, second_root.children):
        assert a.translation == b.translation


def test_empty_and_anchor_only_trees() -> None:
    empty = make_node("Weapon")
    placements, stats = run_layout(empty)
    assert placements == []
    assert stats.skipped == {}

    anchors = make_node(
        "Weapon",
        children=[
            make_node("receiver_in", mesh=make_mesh()),
            make_node("receiver_out", translation=(1.0, 0.0, 0.0), mesh=make_mesh()),
        ],
    )
    placements, stats = run_layout(anchors)
    assert placements == []
    assert stats.skipped == {}


def test_parts_listed_children_displayed_when_parent_unlisted() -> None:
    # Regression: ak47 nests every part under "receiver"; when the game
    # parts list omits the grouping node, its listed children must get
    # their own circle positions (built-in grid semantics).
    child_a = make_node("bolt", translation=(1.0, 0.0, 0.0), mesh=make_mesh())
    child_b = make_node("barrel", translation=(2.0, 0.0, 0.0), mesh=make_mesh())
    receiver = make_node(
        "receiver",
        translation=(0.0, 0.0, 0.0),
        mesh=make_mesh(),
        children=[child_a, child_b],
    )
    root = make_node("root", children=[receiver])

    placements, _ = run_layout(root, part_order=["bolt", "barrel"])

    assert len(placements) == 2
    assert {p.name for p in placements} == {"bolt", "barrel"}
