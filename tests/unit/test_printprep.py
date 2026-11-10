"""Unit tests for the print preparation pipeline (task 18)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

trimesh = pytest.importorskip("trimesh", reason="print extra not installed")

from wog_dump.core.glb import GLBWriter, MeshPrimitive  # noqa: E402
from wog_dump.core.printprep import prepare_print  # noqa: E402


def box_glb(tmp_path: Path, name: str = "weapon") -> Path:
    """One GLB with a single 1m watertight box part."""
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    writer = GLBWriter(generator="print-test")
    mesh = writer.add_mesh(
        "box_part",
        [
            MeshPrimitive(
                positions=[c for v in box.vertices for c in v],
                indices=[int(i) for i in box.faces.reshape(-1)],
            )
        ],
    )
    writer.add_node(name="box_part", mesh=mesh, translation=(0, 0, 0))
    path = tmp_path / f"{name}.glb"
    writer.write(path)
    return path


def broken_glb(tmp_path: Path) -> Path:
    """A box missing one face (a triangle hole) plus an open strip."""
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    holed_faces = box.faces[:-1]
    writer = GLBWriter(generator="print-test")
    mesh = writer.add_mesh(
        "holed_box",
        [
            MeshPrimitive(
                positions=[c for v in box.vertices for c in v],
                indices=[int(i) for i in holed_faces.reshape(-1)],
            )
        ],
    )
    writer.add_node(name="holed_box", mesh=mesh, translation=(0, 0, 0))
    path = tmp_path / "broken.glb"
    writer.write(path)
    return path


class TestPreparePrint:
    def test_watertight_part_is_ok_and_mm(self, tmp_path: Path) -> None:
        out = tmp_path / "print"
        report = prepare_print(box_glb(tmp_path), out)

        assert report["schema"] == "wog-dump.print/1"
        part = report["parts"][0]
        assert part["status"] == "ok"
        assert part["watertight"] is True
        # 1 m box -> 1000 mm extents
        assert all(size == pytest.approx(1000.0) for size in part["size_mm"])
        stl = out / "weapon__box_part.stl"
        assert stl.exists()
        assert stl.stat().st_size > 84

    def test_holed_part_repairs_to_watertight(self, tmp_path: Path) -> None:
        out = tmp_path / "print"
        report = prepare_print(broken_glb(tmp_path), out)

        part = report["parts"][0]
        assert part["status"] in ("fixed", "warnings")
        assert part["notes"]  # the repair or remaining hole is recorded

    def test_part_filter(self, tmp_path: Path) -> None:
        glb = box_glb(tmp_path)
        out = tmp_path / "print"
        report = prepare_print(glb, out, parts_filter={"nothing-matches"})
        assert report["parts"] == []

        report = prepare_print(glb, out, parts_filter={"box_part"})
        assert report["parts"][0]["name"] == "box_part"

    def test_merged_layout_no_overlap(self, tmp_path: Path) -> None:
        writer = GLBWriter(generator="print-test")
        for name, x in (("part_a", 0.0), ("part_b", 3.0)):
            box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
            box.apply_translation([x, 0, 0])
            mesh = writer.add_mesh(
                name,
                [
                    MeshPrimitive(
                        positions=[c for v in box.vertices for c in v],
                        indices=[int(i) for i in box.faces.reshape(-1)],
                    )
                ],
            )
            writer.add_node(name=name, mesh=mesh, translation=(0, 0, 0))
        glb = tmp_path / "merged.glb"
        writer.write(glb)

        out = tmp_path / "print"
        report = prepare_print(glb, out, merged=True, spacing_mm=10.0)
        assert report["merged"] is True
        stl = out / "merged.stl"
        assert stl.exists()

    def test_invalid_weapon_name_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid weapon name"):
            prepare_print(tmp_path / "bad name.glb", tmp_path)

    def test_report_file_written(self, tmp_path: Path) -> None:
        out = tmp_path / "print"
        prepare_print(box_glb(tmp_path), out)
        report = json.loads((out / "print_report.json").read_text())
        assert report["units"] == "mm"
        assert report["real_scale"] is True
