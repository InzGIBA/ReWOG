"""Unit tests for the format exporters (STL, USD, FBX)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from wog_dump.core.export import (
    ExportError,
    ExportOptions,
    GlbMesh,
    GlbNode,
    GlbPrimitive,
    GlbScene,
    STLExporter,
    _blender_fbx_script,
    _prim_name,
    _stl_bytes,
    convert_format,
    find_blender,
    parse_glb,
    write_stl,
)
from wog_dump.core.glb import GLBWriter, MeshPrimitive
from wog_dump.core.transforms import compose_matrix, identity_matrix

# A right triangle in the XY plane
TRIANGLE = (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),)


class TestStlBytes:
    def test_layout_header_count_and_size(self) -> None:
        data = _stl_bytes(list(TRIANGLE))
        assert len(data) == 84 + 50
        assert data[:11] == b"WOG Dump so"
        (count,) = struct.unpack_from("<I", data, 80)
        assert count == 1

    def test_vertices_written_verbatim(self) -> None:
        data = _stl_bytes(list(TRIANGLE))
        normal = struct.unpack_from("<3f", data, 84)
        assert normal == pytest.approx((0.0, 0.0, 1.0))
        a = struct.unpack_from("<3f", data, 96)
        b = struct.unpack_from("<3f", data, 108)
        c = struct.unpack_from("<3f", data, 120)
        assert a == pytest.approx((0.0, 0.0, 0.0))
        assert b == pytest.approx((1.0, 0.0, 0.0))
        assert c == pytest.approx((0.0, 1.0, 0.0))

    def test_degenerate_face_zero_normal(self) -> None:
        flat = (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),)
        data = _stl_bytes(list(flat))
        assert struct.unpack_from("<3f", data, 84) == (0.0, 0.0, 0.0)

    def test_write_stl_roundtrip(self, tmp_path: Path) -> None:
        path = write_stl(list(TRIANGLE), tmp_path / "part.stl")
        data = path.read_bytes()
        (count,) = struct.unpack_from("<I", data, 80)
        assert count == 1
        assert len(data) == 84 + count * 50


class TestPrimName:
    def test_plain_name_kept(self) -> None:
        assert _prim_name("receiver") == "receiver"

    def test_spaces_and_specials_replaced(self) -> None:
        assert _prim_name("main_spring_2 1") == "main_spring_2_1"
        assert _prim_name("bolt/spring") == "bolt_spring"

    def test_leading_digit_prefixed(self) -> None:
        assert _prim_name("1911_grip") == "n_1911_grip"

    def test_empty_falls_back(self) -> None:
        assert _prim_name("") == "node"
        assert _prim_name("///") == "___"  # underscores are valid prim names


def make_scene() -> GlbScene:
    """Two meshed nodes under one root plus a camera-like empty node."""
    identity = identity_matrix()
    triangle = GlbPrimitive(
        positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        indices=[0, 1, 2],
        material=0,
    )
    scene = GlbScene()
    scene.meshes = [GlbMesh(name="mesh_a", primitives=[triangle])]
    scene.nodes = [
        GlbNode(name="weapon", mesh=None, world=identity, children=[1, 2]),
        GlbNode(name="part_a", mesh=0, world=identity, children=[]),
        GlbNode(name="camera", mesh=None, world=identity, children=[]),
    ]
    return scene


class TestStlExporter:
    def test_merged_export_units(self, tmp_path: Path) -> None:
        scene = make_scene()
        paths = STLExporter().export(
            scene, Path("/tmp/weapon.glb"), tmp_path, ExportOptions(units="mm")
        )

        assert len(paths) == 1
        data = paths[0].read_bytes()
        (count,) = struct.unpack_from("<I", data, 80)
        assert count == 1
        y = struct.unpack_from("<3f", data, 120)[1]
        assert y == pytest.approx(1000.0)  # 1 m in mm

    def test_per_part_export_skips_empty_nodes(self, tmp_path: Path) -> None:
        scene = make_scene()
        paths = STLExporter().export(
            scene, Path("/tmp/weapon.glb"), tmp_path, ExportOptions(per_part=True)
        )

        names = {p.name for p in paths}
        assert names == {"weapon__part_a.stl"}

    def test_unknown_unit_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="Unknown STL unit"):
            STLExporter().export(
                make_scene(),
                Path("/tmp/weapon.glb"),
                tmp_path,
                ExportOptions(units="yards"),
            )


class TestConvertFormat:
    def test_glb_passthrough(self, tmp_path: Path) -> None:
        glb = tmp_path / "scene.glb"
        glb.write_bytes(b"stub")
        assert convert_format(glb, "glb", tmp_path) == [glb]

    def test_unknown_format_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="Unknown export format"):
            convert_format(tmp_path / "x.glb", "obj", tmp_path)


class TestFbxScript:
    def test_script_references_paths(self, tmp_path: Path) -> None:
        glb = tmp_path / "ak47.glb"
        fbx = tmp_path / "ak47.fbx"
        script = _blender_fbx_script(glb, fbx)
        assert str(glb) in script
        assert str(fbx) in script
        assert "import_scene.gltf" in script
        assert "export_scene.fbx" in script

    def test_fbx_without_blender(self, tmp_path: Path, monkeypatch: Any) -> None:
        from wog_dump.core.export import FBXExporter

        monkeypatch.delenv("WOG_BLENDER_PATH", raising=False)
        monkeypatch.setattr("wog_dump.core.export.find_blender", lambda: None)
        with pytest.raises(ExportError, match="Blender not found"):
            FBXExporter().export(
                make_scene(), Path("/tmp/w.glb"), tmp_path, ExportOptions()
            )

    def test_find_blender_prefers_env(self, tmp_path: Path, monkeypatch: Any) -> None:
        fake = tmp_path / "blender-foo"
        fake.write_text("")
        monkeypatch.setenv("WOG_BLENDER_PATH", str(fake))
        assert find_blender() == fake


class TestUsdExport:
    usd_core: Any = pytest.importorskip("pxr", reason="usd-core not installed")

    def test_usd_stage_structure(self, tmp_path: Path) -> None:
        from pxr import Usd, UsdGeom

        glb = _glb_from(make_scene(), tmp_path)
        paths = convert_format(glb, "usd", tmp_path, ExportOptions())

        stage = Usd.Stage.Open(str(paths[0]))
        mesh_prims = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
        assert len(mesh_prims) == 1
        mesh = UsdGeom.Mesh(mesh_prims[0])
        points = mesh.GetPointsAttr().Get()
        assert len(points) == 3
        assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0

    def test_usdz_package(self, tmp_path: Path) -> None:
        from pxr import Usd, UsdGeom

        glb = _glb_from(make_scene(), tmp_path)
        paths = convert_format(glb, "usdz", tmp_path, ExportOptions())
        assert paths[0].suffix == ".usdz"
        stage = Usd.Stage.Open(str(paths[0]))
        meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
        assert len(meshes) == 1


def _glb_from(scene: GlbScene, tmp_path: Path) -> Path:
    """Build a real GLB through the writer so parse_glb has input."""
    writer = GLBWriter(generator="test")
    material = writer.add_material(name="steel")
    mesh_index = writer.add_mesh(
        "tri",
        [
            MeshPrimitive(
                positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                material=material,
            )
        ],
    )
    root = writer.add_node("weapon", translation=(0.0, 0.0, 0.0))
    writer.add_node("part_a", mesh=mesh_index, translation=(1.0, 2.0, 3.0), parent=root)
    path = tmp_path / "weapon.glb"
    writer.write(path)
    return path


class TestParseGlb:
    def test_world_transform_composition(self, tmp_path: Path) -> None:
        glb = _glb_from(make_scene(), tmp_path)
        scene = parse_glb(glb)

        assert len(scene.nodes) == 2
        root, part = scene.nodes
        assert root.name == "weapon"
        assert part.name == "part_a"
        assert root.children == [1]
        # child world = parent identity * local translation
        assert part.world[12] == pytest.approx(1.0)
        assert part.world[13] == pytest.approx(2.0)
        assert part.world[14] == pytest.approx(3.0)

    def test_mesh_and_material_data(self, tmp_path: Path) -> None:
        glb = _glb_from(make_scene(), tmp_path)
        scene = parse_glb(glb)

        assert len(scene.meshes) == 1
        primitive = scene.meshes[0].primitives[0]
        assert primitive.indices == [0, 1, 2]
        assert primitive.material == 0
        assert scene.materials[0].name == "steel"

    def test_invalid_file_rejected(self, tmp_path: Path) -> None:
        fake = tmp_path / "bad.glb"
        fake.write_bytes(b"not a glb")
        with pytest.raises(ExportError, match="Not a valid GLB"):
            parse_glb(fake)
