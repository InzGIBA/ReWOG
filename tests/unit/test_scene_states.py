"""Unit tests for the disassembly scene states (task 01)."""

from __future__ import annotations

from typing import Any

import pytest

from wog_dump.core.scene import SceneBuilder, SceneExportError, SceneStats
from wog_dump.core.scene_states import (
    SCENE_STATES,
    PartPlacement,
    apply_exploded,
    apply_field_strip,
    apply_grid_layout,
    normalize_part_name,
)
from wog_dump.core.transforms import (
    compose_matrix,
    invert_matrix,
    multiply_matrix,
    transform_point,
)

UNIT_SCALE = 0.01


class _FakeMesh:
    """Mesh stand-in with an axis-aligned local bound box."""

    def __init__(self, size: float = 0.1) -> None:
        self.local_min = [-size] * 3
        self.local_max = [size] * 3


class _FakeMeshData:
    """Mesh-extraction stand-in returned by the bounds callback."""

    def __init__(self, size: float = 0.1) -> None:
        self.local_min = [-size] * 3
        self.local_max = [size] * 3


def make_node(
    name: str,
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    mesh: Any | None = None,
    children: list[Any] | None = None,
) -> Any:
    from wog_dump.core.scene import _GameObjectInfo

    return _GameObjectInfo(
        name=name,
        mesh=mesh,
        translation=translation,
        children=list(children or []),
    )


def add_anchor(root: Any, base: str, pos: tuple[float, float, float]) -> None:
    """Add a ``<base>_out`` removal anchor like the game bundles do."""
    root.children.append(make_node(f"{base}_out", translation=pos))


def world_position(node: Any, ancestors: list[Any]) -> tuple[float, float, float]:
    matrix = identity_matrix_list()
    for a in ancestors:
        matrix = multiply_matrix(
            matrix, compose_matrix(a.translation, a.rotation, a.scale)
        )
    matrix = multiply_matrix(
        matrix, compose_matrix(node.translation, node.rotation, node.scale)
    )
    return transform_point(matrix, (0.0, 0.0, 0.0))


def identity_matrix_list() -> list[float]:
    from wog_dump.core.transforms import identity_matrix

    return identity_matrix()


class TestNormalize:
    def test_strips_underscores_and_case(self) -> None:
        assert normalize_part_name("_Trigger_Group") == "trigger_group"
        assert normalize_part_name("barrel") == "barrel"

    def test_anchor_base_normalized(self) -> None:
        assert normalize_part_name("_magazine_assembly_out"[:-4]) == "magazine_assembly"


class TestTransforms:
    def test_invert_compose_roundtrip(self) -> None:
        m = compose_matrix((3.0, -2.0, 5.0), (0.1, 0.2, 0.3, 0.9), (1.2, 0.8, 1.5))
        inv = invert_matrix(m)
        product = multiply_matrix(m, inv)
        identity = identity_matrix_list()
        for i, value in enumerate(product):
            assert value == pytest.approx(identity[i], abs=1e-6)

    def test_invert_singular_raises(self) -> None:
        zero_scale = compose_matrix((0, 0, 0), (0, 0, 0, 1), (0.0, 0.0, 0.0))
        with pytest.raises(ValueError):
            invert_matrix(zero_scale)

    def test_transform_point_translation(self) -> None:
        m = compose_matrix((10.0, 0.0, 4.0), (0, 0, 0, 1), (2.0, 2.0, 2.0))
        assert transform_point(m, (1.0, 1.0, 1.0)) == pytest.approx((12.0, 2.0, 6.0))


class TestFieldStrip:
    def test_part_moves_to_anchor_position(self) -> None:
        root = make_node(
            "ak47_hd_root",
            children=[
                make_node("receiver", mesh=object()),
                make_node(
                    "barrel",
                    translation=(-15.0, 21.0, 0.0),
                    mesh=object(),
                ),
            ],
        )
        add_anchor(root, "barrel", (-53.0, 21.5, 0.01))
        stats = SceneStats(weapon="ak47")

        placements = apply_field_strip(root, UNIT_SCALE, stats)

        barrel = root.children[1]
        # Anchor is a root child, root sits at identity: the barrel world
        # position must land exactly on the anchor position
        assert world_position(barrel, [root]) == pytest.approx((-53.0, 21.5, 0.01))
        assert len(placements) == 1
        assert placements[0].name == "barrel"
        assert isinstance(placements[0], PartPlacement)

    def test_anchor_on_nested_part(self) -> None:
        receiver = make_node("receiver", mesh=object())
        trigger = make_node("trigger", translation=(5.0, 24.0, -1.5), mesh=object())
        receiver.children.append(trigger)
        root = make_node("root", children=[receiver])
        add_anchor(root, "trigger_group", (4.7, 21.3, -1.7))
        stats = SceneStats(weapon="ak47")

        placements = apply_field_strip(root, UNIT_SCALE, stats)

        assert world_position(trigger, [root, receiver]) == pytest.approx(
            (4.7, 21.3, -1.7)
        )
        assert len(placements) == 1

    def test_unmatched_anchor_counted(self) -> None:
        root = make_node("root", children=[make_node("receiver", mesh=object())])
        add_anchor(root, "ghost_part", (1.0, 2.0, 3.0))
        stats = SceneStats(weapon="ak47")

        placements = apply_field_strip(root, UNIT_SCALE, stats)

        assert placements == []
        assert stats.skipped["unmatched_anchor"] == 1

    def test_nested_part_with_own_anchor_moves_independently(self) -> None:
        # trigger_group carries trigger; both have anchors, so each takes
        # its own game removal position
        trigger = make_node("trigger", translation=(5.0, 24.0, -1.5), mesh=object())
        trigger_group = make_node(
            "_trigger_group",
            translation=(4.9, 24.6, -1.5),
            mesh=object(),
            children=[trigger],
        )
        root = make_node("root", children=[trigger_group])
        add_anchor(root, "trigger_group", (4.7, 21.3, -1.7))
        add_anchor(root, "trigger", (4.0, 20.0, -1.0))
        stats = SceneStats(weapon="ak47")

        placements = apply_field_strip(root, UNIT_SCALE, stats)

        assert len(placements) == 2
        assert world_position(trigger_group, [root]) == pytest.approx((4.7, 21.3, -1.7))
        assert world_position(trigger, [root, trigger_group]) == pytest.approx(
            (4.0, 20.0, -1.0)
        )
        assert stats.skipped["nested_independent"] == 1

    def test_nested_part_without_anchor_rides_with_moved_parent(self) -> None:
        trigger = make_node("trigger", translation=(5.0, 24.0, -1.5), mesh=object())
        trigger_group = make_node(
            "_trigger_group",
            translation=(4.9, 24.6, -1.5),
            mesh=object(),
            children=[trigger],
        )
        root = make_node("root", children=[trigger_group])
        add_anchor(root, "trigger_group", (4.7, 21.3, -1.7))
        stats = SceneStats(weapon="ak47")

        placements = apply_field_strip(root, UNIT_SCALE, stats)

        assert len(placements) == 1
        assert placements[0].name == "_trigger_group"
        assert stats.skipped["rode_along"] == 1
        # Relative position inside the group is preserved
        assert trigger.translation == (5.0, 24.0, -1.5)


class TestExploded:
    def test_displacement_magnitude_and_direction(self) -> None:
        barrel = make_node("barrel", translation=(-15.0, 21.0, 0.0), mesh=object())
        root = make_node("root", children=[barrel])
        add_anchor(root, "barrel", (-35.0, 21.0, 0.0))
        stats = SceneStats(weapon="ak47")

        placements = apply_exploded(root, UNIT_SCALE, 0.35, stats)

        # Removal direction is -X, magnitude 0.35 m -> 35 Unity units
        assert world_position(barrel, [root]) == pytest.approx(
            (-15.0 - 35.0, 21.0, 0.0)
        )
        assert placements[0].offset == pytest.approx((-0.35, 0.0, 0.0))

    def test_no_anchor_gets_radial_direction(self) -> None:
        anchored = make_node("barrel", translation=(10.0, 0.0, 0.0), mesh=object())
        free = make_node("receiver", translation=(-10.0, 0.0, 0.0), mesh=object())
        root = make_node("root", children=[anchored, free])
        add_anchor(root, "barrel", (20.0, 0.0, 0.0))
        stats = SceneStats(weapon="ak47")

        placements = apply_exploded(root, UNIT_SCALE, 0.1, stats)

        # receiver gets pushed away from the centroid (origin here)
        assert world_position(free, [root])[0] == pytest.approx(-20.0)
        assert len(placements) == 2

    def test_zero_distance_leaves_parts_untouched(self) -> None:
        barrel = make_node("barrel", translation=(1.0, 2.0, 3.0), mesh=object())
        root = make_node("root", children=[barrel])
        add_anchor(root, "barrel", (50.0, 2.0, 3.0))
        stats = SceneStats(weapon="ak47")

        placements = apply_exploded(root, UNIT_SCALE, 0.0, stats)

        assert world_position(barrel, [root]) == pytest.approx((1.0, 2.0, 3.0))
        assert placements[0].offset == pytest.approx((0.0, 0.0, 0.0))


class TestGridLayout:
    def _fake_bounds(self, mesh: Any) -> Any:
        return _FakeMeshData()

    def test_parts_laid_out_in_distinct_cells(self) -> None:
        part_a = make_node("barrel", translation=(-15.0, 21.0, 0.0), mesh=object())
        part_b = make_node("buttstock", translation=(10.0, 21.0, 0.0), mesh=object())
        root = make_node("root", children=[part_a, part_b])
        stats = SceneStats(weapon="ak47", parts=["barrel", "buttstock"])

        placements = apply_grid_layout(
            root, UNIT_SCALE, stats.parts, self._fake_bounds, stats
        )

        assert len(placements) == 2
        # Distinct world positions
        pa = world_position(part_a, [root])
        pb = world_position(part_b, [root])
        assert pa != pb

    def test_bottoms_on_ground_plane(self) -> None:
        # Part bound box spans -0.1..0.1 around its world position, so
        # grounding shifts the part up by 0.1 minus the old min
        part = make_node("barrel", translation=(0.0, 0.0, 0.0), mesh=object())
        root = make_node("root", children=[part])
        stats = SceneStats(weapon="ak47", parts=["barrel"])

        apply_grid_layout(root, UNIT_SCALE, stats.parts, self._fake_bounds, stats)

        bounds = [[float("inf")] * 3, [float("-inf")] * 3]
        matrix = multiply_matrix(
            identity_matrix_list(),
            compose_matrix(part.translation, part.rotation, part.scale),
        )
        mesh = _FakeMeshData()
        from wog_dump.core.scene_states import _accumulate

        _accumulate(bounds, matrix, mesh.local_min, mesh.local_max, UNIT_SCALE)
        assert bounds[0][1] == pytest.approx(0.0, abs=1e-6)

    def test_nested_part_rides_in_parent_cell(self) -> None:
        screw = make_node("buttstock_screw", mesh=object())
        buttstock = make_node(
            "buttstock", translation=(10.0, 21.0, 0.0), mesh=object(), children=[screw]
        )
        receiver = make_node("receiver", mesh=object(), children=[])
        root = make_node("root", children=[receiver, buttstock])
        stats = SceneStats(
            weapon="ak47", parts=["receiver", "buttstock", "buttstock_screw"]
        )

        placements = apply_grid_layout(
            root, UNIT_SCALE, stats.parts, self._fake_bounds, stats
        )

        names = [p.name for p in placements]
        assert names == ["receiver", "buttstock"]
        assert stats.skipped["rode_along"] == 1

    def test_duplicate_entries_laid_out_once(self) -> None:
        part = make_node("barrel", mesh=object())
        root = make_node("root", children=[part])
        stats = SceneStats(weapon="ak47", parts=["barrel", "barrel"])

        placements = apply_grid_layout(
            root, UNIT_SCALE, stats.parts, self._fake_bounds, stats
        )
        assert len(placements) == 1

    def test_empty_part_list(self) -> None:
        root = make_node("root", children=[make_node("barrel", mesh=object())])
        stats = SceneStats(weapon="ak47")

        placements = apply_grid_layout(root, UNIT_SCALE, [], self._fake_bounds, stats)
        assert placements == []


class TestSceneStatesContract:
    def test_state_set(self) -> None:
        assert SCENE_STATES == frozenset(
            {"assembled", "field-strip", "exploded", "parts"}
        )

    def test_builder_rejects_unknown_state(self) -> None:
        builder = SceneBuilder()
        with pytest.raises(SceneExportError, match="Invalid state"):
            builder.build_scene("ak47", state="bogus")

    def test_builder_rejects_negative_explode_distance(self) -> None:
        builder = SceneBuilder()
        with pytest.raises(SceneExportError, match="non-negative"):
            builder.build_scene("ak47", state="exploded", explode_distance=-1.0)


class TestInAnchors:
    def _add_in(self, root: Any, base: str, pos: tuple[float, float, float]) -> None:
        root.children.append(make_node(f"{base}_in", translation=pos))

    def test_reused_name_disambiguated_by_in_position(self) -> None:
        # Two nodes named "pin": the _in anchor sits next to the second
        far_pin = make_node("pin", translation=(100.0, 0.0, 0.0), mesh=object())
        near_pin = make_node("pin", translation=(10.0, 0.0, 0.0), mesh=object())
        root = make_node("root", children=[far_pin, near_pin])
        self._add_in(root, "pin", (10.5, 0.0, 0.0))
        add_anchor(root, "pin", (10.0, -20.0, 0.0))
        stats = SceneStats(weapon="test")

        placements = apply_field_strip(root, UNIT_SCALE, stats)

        assert len(placements) == 1
        assert near_pin.translation == pytest.approx((10.0, -20.0, 0.0))
        assert far_pin.translation == (100.0, 0.0, 0.0)

    def test_without_in_anchor_first_candidate_wins(self) -> None:
        first = make_node("pin", translation=(100.0, 0.0, 0.0), mesh=object())
        second = make_node("pin", translation=(10.0, 0.0, 0.0), mesh=object())
        root = make_node("root", children=[first, second])
        add_anchor(root, "pin", (0.0, -20.0, 0.0))
        stats = SceneStats(weapon="test")

        apply_field_strip(root, UNIT_SCALE, stats)

        assert first.translation == pytest.approx((0.0, -20.0, 0.0))
        assert second.translation == (10.0, 0.0, 0.0)

    def test_anchor_verification_counts_near_and_far(self) -> None:
        close = make_node("barrel", translation=(-15.0, 21.0, 0.0), mesh=object())
        distant = make_node("grip", translation=(50.0, 50.0, 0.0), mesh=object())
        root = make_node("root", children=[close, distant])
        self._add_in(root, "barrel", (-15.5, 21.4, 0.0))
        self._add_in(root, "grip", (10.0, 12.0, 0.0))
        add_anchor(root, "barrel", (-53.0, 21.5, 0.0))
        add_anchor(root, "grip", (13.0, 5.0, 0.0))
        stats = SceneStats(weapon="test")

        apply_field_strip(root, UNIT_SCALE, stats)

        assert stats.anchor_checks == {"near": 1, "far": 1}

    def test_zero_in_anchor_ignored(self) -> None:
        barrel = make_node("barrel", translation=(-15.0, 21.0, 0.0), mesh=object())
        root = make_node("root", children=[barrel])
        self._add_in(root, "barrel", (0.0, 0.0, 0.0))
        add_anchor(root, "barrel", (-53.0, 21.5, 0.0))
        stats = SceneStats(weapon="test")

        apply_field_strip(root, UNIT_SCALE, stats)

        assert stats.anchor_checks == {}


class TestNestedExploded:
    def test_anchored_nested_part_explodes_independently(self) -> None:
        trigger = make_node("trigger", translation=(1.0, 0.0, 0.0), mesh=object())
        group = make_node(
            "trigger_group",
            translation=(0.0, 0.0, 0.0),
            mesh=object(),
            children=[trigger],
        )
        root = make_node("root", children=[group])
        add_anchor(root, "trigger_group", (0.0, -10.0, 0.0))
        add_anchor(root, "trigger", (10.0, 0.0, 0.0))
        stats = SceneStats(weapon="test")

        placements = apply_exploded(root, UNIT_SCALE, 0.2, stats)

        assert len(placements) == 2
        # Group slides 20 Unity units down its removal direction
        assert world_position(group, [root]) == pytest.approx((0.0, -20.0, 0.0))
        # Trigger slides 20 units along +X in world space, despite the
        # moved parent
        assert world_position(trigger, [root, group]) == pytest.approx((21.0, 0.0, 0.0))
        assert stats.skipped["nested_independent"] == 1

    def test_unanchored_nested_part_rides_in_exploded(self) -> None:
        child = make_node("screw", translation=(1.0, 0.0, 0.0), mesh=object())
        parent = make_node(
            "barrel", translation=(10.0, 0.0, 0.0), mesh=object(), children=[child]
        )
        root = make_node("root", children=[parent])
        add_anchor(root, "barrel", (30.0, 0.0, 0.0))
        stats = SceneStats(weapon="test")

        placements = apply_exploded(root, UNIT_SCALE, 0.2, stats)

        assert len(placements) == 1
        assert stats.skipped["rode_along"] == 1
        assert child.translation == (1.0, 0.0, 0.0)
