"""Tests for the 3MF exporter plugin.

Runnable without installing the package: ``conftest.py`` puts the
plugin's ``src`` dir on ``sys.path``. Tiny GLBs are built with the
editable-installed ``wog_dump.core.glb.GLBWriter``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from wog_plugin_3mf import ThreeMFExporter

from wog_dump.core.extensions import Exporter
from wog_dump.core.glb import GLBWriter, MeshPrimitive

NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REQUIRED_MEMBERS = {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}


def _triangle(scale: float = 1.0) -> MeshPrimitive:
    return MeshPrimitive(
        positions=[0.0, 0.0, 0.0, scale, 0.0, 0.0, 0.0, scale, 0.0],
        indices=[0, 1, 2],
    )


def _make_glb(path: Path, names: list[str]) -> Path:
    """One top-level named node per entry, each a right triangle."""
    writer = GLBWriter(generator="threemf-test")
    for i, name in enumerate(names):
        mesh = writer.add_mesh(f"{name}_mesh", [_triangle(float(i + 1))])
        writer.add_node(name, mesh=mesh, translation=(float(i * 10), 0.0, 0.0))
    writer.write(path)
    return path


def _read_model(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read("3D/3dmodel.model"))


def _objects(model: ET.Element) -> list[ET.Element]:
    return model.findall(f"{{{NS}}}resources/{{{NS}}}object")


def _vertices(obj: ET.Element) -> list[ET.Element]:
    return obj.findall(f"{{{NS}}}mesh/{{{NS}}}vertices/{{{NS}}}vertex")


def _max_coord(model: ET.Element) -> float:
    return max(
        abs(float(vertex.get(axis)))
        for vertex in model.iter(f"{{{NS}}}vertex")
        for axis in ("x", "y", "z")
    )


class TestProtocol:
    def test_conforms_to_exporter_protocol(self) -> None:
        exporter = ThreeMFExporter()
        assert isinstance(exporter, Exporter)
        assert exporter.name == "3mf"
        assert exporter.extensions == (".3mf",)

    def test_instantiable_with_no_args(self) -> None:
        assert ThreeMFExporter().name == "3mf"


class TestMergedExport:
    def test_valid_zip_with_required_members(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver", "barrel"])
        out = tmp_path / "out"
        written = ThreeMFExporter().convert(glb, out)

        assert written == [out / "ak47.3mf"]
        assert zipfile.is_zipfile(written[0])
        with zipfile.ZipFile(written[0]) as archive:
            assert REQUIRED_MEMBERS <= set(archive.namelist())
            assert archive.testzip() is None

    def test_model_xml_parseable_and_single_object(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver", "barrel"])
        written = ThreeMFExporter().convert(glb, tmp_path / "out")
        model = _read_model(written[0])

        assert model.tag == f"{{{NS}}}model"
        assert model.get("unit") == "millimeter"
        objects = _objects(model)
        assert len(objects) == 1
        # Two 3-vertex triangles merged into one object.
        assert len(_vertices(objects[0])) == 6
        triangles = objects[0].findall(
            f"{{{NS}}}mesh/{{{NS}}}triangles/{{{NS}}}triangle"
        )
        assert len(triangles) == 2

    def test_build_items_reference_objects(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver", "barrel"])
        written = ThreeMFExporter().convert(glb, tmp_path / "out")
        model = _read_model(written[0])

        object_ids = {obj.get("id") for obj in _objects(model)}
        items = model.findall(f"{{{NS}}}build/{{{NS}}}item")
        assert len(items) == 1
        assert {item.get("objectid") for item in items} == object_ids

    def test_indices_are_ints_within_range(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver"])
        written = ThreeMFExporter().convert(glb, tmp_path / "out")
        obj = _objects(_read_model(written[0]))[0]
        vertex_count = len(_vertices(obj))
        for triangle in obj.findall(f"{{{NS}}}mesh/{{{NS}}}triangles/{{{NS}}}triangle"):
            for key in ("v1", "v2", "v3"):
                value = triangle.get(key)
                assert value is not None and value.isdigit()
                assert 0 <= int(value) < vertex_count

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver"])
        out = tmp_path / "out"
        written = ThreeMFExporter().convert(glb, out)
        assert set(out.iterdir()) == set(written)


class TestPerPart:
    def test_one_object_per_named_node(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver", "barrel", "stock"])
        written = ThreeMFExporter().convert(glb, tmp_path / "out", per_part=True)
        model = _read_model(written[0])
        objects = _objects(model)

        assert len(objects) == 3
        assert {obj.get("name") for obj in objects} == {"receiver", "barrel", "stock"}
        for obj in objects:
            assert len(_vertices(obj)) == 3

        object_ids = {obj.get("id") for obj in objects}
        items = model.findall(f"{{{NS}}}build/{{{NS}}}item")
        assert {item.get("objectid") for item in items} == object_ids

    def test_default_is_merged(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver", "barrel"])
        written = ThreeMFExporter().convert(glb, tmp_path / "out")
        assert len(_objects(_read_model(written[0]))) == 1


class TestUnits:
    @pytest.mark.parametrize(
        ("units", "factor"), [("m", 1000.0), ("cm", 10.0), ("mm", 1.0)]
    )
    def test_scaling_to_millimeters(
        self, tmp_path: Path, units: str, factor: float
    ) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver"])
        written = ThreeMFExporter().convert(glb, tmp_path / "out", units=units)
        # Source triangle spans 1 glTF meter; expect `factor` mm.
        assert _max_coord(_read_model(written[0])) == pytest.approx(factor)

    def test_m_vs_mm_magnitude_ratio(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver"])
        meters = ThreeMFExporter().convert(glb, tmp_path / "m", units="m")
        millimeters = ThreeMFExporter().convert(glb, tmp_path / "mm", units="mm")
        m_max = _max_coord(_read_model(meters[0]))
        mm_max = _max_coord(_read_model(millimeters[0]))
        assert m_max == pytest.approx(mm_max * 1000.0)

    @pytest.mark.parametrize("units", ["in", "ft", "M", "", "meters"])
    def test_bad_units_rejected(self, tmp_path: Path, units: str) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ["receiver"])
        with pytest.raises(ValueError, match="units"):
            ThreeMFExporter().convert(glb, tmp_path / "out", units=units)


class TestRobustness:
    def test_empty_scene_raises(self, tmp_path: Path) -> None:
        glb = tmp_path / "empty.glb"
        GLBWriter(generator="threemf-test").write(glb)
        with pytest.raises(ValueError, match="no geometry"):
            ThreeMFExporter().convert(glb, tmp_path / "out")

    def test_special_characters_in_name_are_escaped(self, tmp_path: Path) -> None:
        glb = _make_glb(tmp_path / "ak47.glb", ['part<&>"name'])
        written = ThreeMFExporter().convert(glb, tmp_path / "out", per_part=True)
        # Must parse cleanly despite markup characters in the name.
        model = _read_model(written[0])
        assert _objects(model)[0].get("name") == 'part<&>"name'
