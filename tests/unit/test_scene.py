"""Unit tests for the scene exporter (classification and conversions)."""

from __future__ import annotations

import pytest

from wog_dump.cli.scene import _parse_extras
from wog_dump.core.scene import (
    SceneBuilder,
    _compose_matrix,
    _identity_matrix,
    _multiply_matrix,
    _shininess_to_roughness,
    classify_game_object,
    convert_position,
    convert_rotation,
)


class TestClassification:
    def test_arrow_materials_classified_by_material(self) -> None:
        assert classify_game_object("_barrel_in", ["arrow_in"]) == "arrows"
        assert classify_game_object("barrel_out", ["arrow_out"]) == "arrows"

    def test_cut_classified_by_name_and_material(self) -> None:
        assert classify_game_object("receiver_cut", ["ak47_3_hd"]) == "cut"
        assert classify_game_object("thing", ["lambert4"]) == "cut"

    def test_vfx_classified_by_material(self) -> None:
        assert classify_game_object("fire_muzzle", ["muzzle_big", "muzzle_1"]) == "vfx"
        assert classify_game_object("gas_anim1", ["gas"]) == "vfx"

    def test_regular_parts_are_gun(self) -> None:
        assert classify_game_object("receiver", ["ak47_3_hd", "frame_b"]) == "gun"
        assert classify_game_object("trigger", ["ak47_1_hd", "metal_dark"]) == "gun"


class TestCoordinateConversion:
    def test_position_mirrors_z_and_scales(self) -> None:
        assert convert_position((2.0, 3.0, 4.0), 0.01) == (0.02, 0.03, -0.04)

    def test_identity_quaternion_unchanged(self) -> None:
        assert convert_rotation((0.0, 0.0, 0.0, 1.0)) == (0.0, 0.0, 0.0, 1.0)

    def test_quaternion_mirrors_xy_components(self) -> None:
        # 90 degrees around Unity +Y maps glTF rotation onto -Y:
        # mirroring the frame negates the x/y quaternion components
        q = (math_sin_45(), math_sin_45(), 0.0, math_sin_45())
        converted = convert_rotation(q)
        assert converted[0] == pytest.approx(-q[0])
        assert converted[1] == pytest.approx(-q[1])
        assert converted[2] == pytest.approx(q[2])
        assert converted[3] == pytest.approx(q[3])


def math_sin_45() -> float:
    import math

    return math.sin(math.pi / 4)


class TestMatrixHelpers:
    def test_identity_composition(self) -> None:
        matrix = _compose_matrix((0, 0, 0), (0, 0, 0, 1), (1, 1, 1))
        assert matrix == _identity_matrix()

    def test_translation_matrix(self) -> None:
        matrix = _compose_matrix((1.0, 2.0, 3.0), (0, 0, 0, 1), (1, 1, 1))
        assert matrix[12] == 1.0
        assert matrix[13] == 2.0
        assert matrix[14] == 3.0

    def test_multiply_with_identity(self) -> None:
        matrix = _compose_matrix((5.0, 0.0, 0.0), (0, 0, 0, 1), (2, 2, 2))
        result = _multiply_matrix(_identity_matrix(), matrix)
        assert result == matrix

    def test_point_transform_chain(self) -> None:
        # Point at local (1, 0, 0) with parent translation (10, 0, 0)
        parent = _compose_matrix((10.0, 0.0, 0.0), (0, 0, 0, 1), (1, 1, 1))
        child = _compose_matrix((1.0, 0.0, 0.0), (0, 0, 0, 1), (1, 1, 1))
        world = _multiply_matrix(parent, child)
        point = (1.0, 0.0, 0.0, 1.0)
        # Column-major storage: element (row i, col k) lives at k*4 + i
        transformed = [
            sum(world[k * 4 + i] * point[k] for k in range(4)) for i in range(4)
        ]
        assert transformed[:3] == pytest.approx([12.0, 0.0, 0.0])


class TestShininessMapping:
    def test_matte(self) -> None:
        assert _shininess_to_roughness(0.0) == 1.0

    def test_glossy_clamped(self) -> None:
        assert _shininess_to_roughness(0.6) == 0.05

    def test_typical_metal(self) -> None:
        roughness = _shininess_to_roughness(0.35)
        assert 0.2 < roughness < 0.4


class TestExtrasParsing:
    def test_empty(self) -> None:
        assert _parse_extras(()) == frozenset()

    def test_single(self) -> None:
        assert _parse_extras(("arrows",)) == frozenset({"arrows"})

    def test_all_expands(self) -> None:
        assert _parse_extras(("all",)) == frozenset({"arrows", "cut", "vfx"})

    def test_repeats_deduplicated(self) -> None:
        assert _parse_extras(("vfx", "all", "vfx")) == frozenset(
            {"arrows", "cut", "vfx"}
        )


class TestSceneBuilderInit:
    def test_builder_uses_global_config(self) -> None:
        builder = SceneBuilder()
        assert builder.config is not None
