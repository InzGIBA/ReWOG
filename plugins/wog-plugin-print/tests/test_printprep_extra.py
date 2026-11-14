"""print-export 2.0: auto-orientation, bed splitting and estimation.

All meshes are synthetic (boxes, an icosphere and a hand-built L
prism); no real weapon data is involved.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

trimesh = pytest.importorskip("trimesh", reason="print extra not installed")

from wog_plugin_print.cli import _parse_bed_size  # noqa: E402
from wog_plugin_print.printprep import prepare_print  # noqa: E402

from wog_dump.core.glb import GLBWriter, MeshPrimitive  # noqa: E402

BED = (220.0, 220.0, 250.0)


def write_glb(tmp_path: Path, weapon: str, parts: list[tuple[str, Any]]) -> Path:
    """GLB scene from named trimesh meshes (coordinates in meters)."""
    writer = GLBWriter(generator="print-test-extra")
    for name, mesh in parts:
        mesh_id = writer.add_mesh(
            name,
            [
                MeshPrimitive(
                    positions=[float(c) for v in mesh.vertices for c in v],
                    indices=[int(i) for i in mesh.faces.reshape(-1)],
                )
            ],
        )
        writer.add_node(name=name, mesh=mesh_id, translation=(0, 0, 0))
    path = tmp_path / f"{weapon}.glb"
    writer.write(path)
    return path


def box(extents_mm: tuple[float, float, float]) -> Any:
    return trimesh.creation.box(extents=[v / 1000.0 for v in extents_mm])


def l_prism() -> Any:
    """Watertight L prism: 100x100 mm arms, 20 mm wide, 10 mm thick.

    Built by hand (shapely/manifold are not part of the print extra):
    the L hexagon fans from its concave-safe corner vertex.
    """
    import numpy as np

    poly = [
        (0.0, 0.0),
        (0.1, 0.0),
        (0.1, 0.02),
        (0.02, 0.02),
        (0.02, 0.1),
        (0.0, 0.1),
    ]
    height = 0.01
    bottom = [(x, y, 0.0) for x, y in poly]
    top = [(x, y, height) for x, y in poly]
    vertices = np.asarray(bottom + top, dtype=np.float64)
    faces: list[tuple[int, int, int]] = []
    for k in range(1, 5):  # top (+Z) and bottom (-Z) fans
        faces.append((6, 6 + k, 6 + k + 1))
        faces.append((0, k + 1, k))
    for i in range(6):  # side walls
        j = (i + 1) % 6
        faces.append((i, j, 6 + j))
        faces.append((i, 6 + j, 6 + i))
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    assert mesh.is_watertight
    assert mesh.volume > 0
    return mesh


def load_stl(path: Path) -> Any:
    return trimesh.load(path, force="mesh")


class TestOrientation:
    def test_tall_bar_lays_flat(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((20, 20, 300)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out, orient=True)

        part = report["parts"][0]
        info = part["orientation"]
        assert info["overhang_area_mm2"] == pytest.approx(0.0, abs=1e-6)
        assert info["height_mm"] == pytest.approx(20.0, abs=1e-3)
        assert part["size_mm"][2] == pytest.approx(20.0, abs=1e-3)
        assert info["candidates"] >= 2
        assert len(info["rotation"]) == 4

        mesh = load_stl(out / "weapon__bar.stl")
        assert mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-4)
        assert mesh.is_watertight

    def test_largest_face_down(self, tmp_path: Path) -> None:
        # 300x200 face starts vertical; orientation must lay it flat.
        tilted = box((300, 200, 50))
        tilted.apply_transform(
            trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])
        )
        glb = write_glb(tmp_path, "weapon", [("plate", tilted)])
        report = prepare_print(glb, tmp_path / "print", orient=True)

        part = report["parts"][0]
        assert part["size_mm"][2] == pytest.approx(50.0, abs=1e-3)
        assert part["orientation"]["overhang_area_mm2"] == pytest.approx(0.0, abs=1e-6)

    def test_l_shape_overhang_removed(self, tmp_path: Path) -> None:
        standing = l_prism()
        standing.apply_transform(
            trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])
        )
        glb = write_glb(tmp_path, "weapon", [("l_part", standing)])
        report = prepare_print(glb, tmp_path / "print", orient=True)

        part = report["parts"][0]
        # Flat L: 10 mm thick, no unsupported downward faces.
        assert part["size_mm"][2] == pytest.approx(10.0, abs=1e-3)
        assert part["orientation"]["overhang_area_mm2"] == pytest.approx(0.0, abs=1e-6)

    def test_sphere_orientation_recorded(self, tmp_path: Path) -> None:
        sphere = trimesh.creation.icosphere(subdivisions=2, radius=0.05)
        glb = write_glb(tmp_path, "weapon", [("ball", sphere)])
        out = tmp_path / "print"
        report = prepare_print(glb, out, orient=True)

        info = report["parts"][0]["orientation"]
        assert info["candidates"] >= 1
        assert info["overhang_area_mm2"] > 0
        mesh = load_stl(out / "weapon__ball.stl")
        assert mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-4)
        assert mesh.is_watertight

    def test_deterministic_choice(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((20, 30, 300)))])
        first = prepare_print(glb, tmp_path / "a", orient=True)
        second = prepare_print(glb, tmp_path / "b", orient=True)
        assert first["parts"][0]["orientation"] == second["parts"][0]["orientation"]

    def test_report_documents_orientation_constants(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((20, 20, 300)))])
        report = prepare_print(glb, tmp_path / "print", orient=True)
        assert report["orientation"] == {
            "overhang_angle_deg": 45.0,
            "max_candidates": 24,
        }


class TestBedSplit:
    def test_long_bar_chunks_fit_and_names(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((500, 20, 20)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out, bed_size_mm=BED)

        part = report["parts"][0]
        split = part["split"]
        assert split["was_split"] is True
        assert split["z_overflow"] is False
        assert len(split["chunks"]) == 3
        widths = [chunk["size_mm"][0] for chunk in split["chunks"]]
        assert widths == pytest.approx([220.0, 220.0, 60.0], abs=1e-3)
        for index, chunk in enumerate(split["chunks"], start=1):
            assert chunk["size_mm"][0] <= 220.0 + 1e-6
            name = f"weapon__bar_chunk{index:02d}.stl"
            assert chunk["file"] == name
            mesh = load_stl(out / name)
            assert mesh.is_watertight
            assert mesh.extents[0] <= 220.0 + 1e-6
        assert not (out / "weapon__bar.stl").exists()
        total_volume = sum(
            abs(float(load_stl(out / c["file"]).volume)) for c in split["chunks"]
        )
        assert total_volume == pytest.approx(500 * 20 * 20, rel=1e-6)

    def test_plate_splits_both_axes(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("plate", box((500, 500, 10)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out, bed_size_mm=BED)

        chunks = report["parts"][0]["split"]["chunks"]
        assert len(chunks) == 9  # ceil(500/220) ** 2
        for chunk in chunks:
            assert chunk["size_mm"][0] <= 220.0 + 1e-6
            assert chunk["size_mm"][1] <= 220.0 + 1e-6
        assert len(list(out.glob("weapon__plate_chunk*.stl"))) == 9

    def test_l_shape_concave_caps(self, tmp_path: Path) -> None:
        # Concave cross-section: the cap needs real ear clipping, and
        # chunk volumes must still sum to the whole part.
        glb = write_glb(tmp_path, "weapon", [("l_part", l_prism())])
        out = tmp_path / "print"
        report = prepare_print(glb, out, bed_size_mm=(60.0, 60.0, 250.0))

        chunks = report["parts"][0]["split"]["chunks"]
        assert len(chunks) == 3
        total = 0.0
        for chunk in chunks:
            mesh = load_stl(out / chunk["file"])
            assert mesh.is_watertight
            total += abs(float(mesh.volume))
        assert total == pytest.approx(36000.0, rel=1e-6)

    def test_chunk_hard_cap(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((20000, 20, 20)))])
        with pytest.raises(ValueError, match="64"):
            prepare_print(glb, tmp_path / "print", bed_size_mm=BED)

    def test_z_overflow_warns_without_split(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("tower", box((10, 10, 300)))])
        report = prepare_print(glb, tmp_path / "print", bed_size_mm=BED)

        part = report["parts"][0]
        assert part["split"]["was_split"] is False
        assert part["split"]["z_overflow"] is True
        assert any("exceeds bed Z" in note for note in part["notes"])

    def test_fitting_part_not_split(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("small", box((100, 100, 100)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out, bed_size_mm=BED)

        split = report["parts"][0]["split"]
        assert split["was_split"] is False
        assert len(split["chunks"]) == 1
        assert split["chunks"][0]["file"] == "weapon__small.stl"
        assert (out / "weapon__small.stl").exists()

    def test_merged_layout_with_chunks(self, tmp_path: Path) -> None:
        glb = write_glb(
            tmp_path,
            "weapon",
            [("bar", box((500, 20, 20))), ("cube", box((100, 100, 100)))],
        )
        out = tmp_path / "print"
        report = prepare_print(glb, out, merged=True, bed_size_mm=BED)

        assert report["merged"] is True
        merged = load_stl(out / "weapon.stl")
        assert abs(float(merged.volume)) == pytest.approx(
            500 * 20 * 20 + 100**3, rel=1e-6
        )
        bar_split = report["parts"][0]["split"]
        assert len(bar_split["chunks"]) == 3
        assert all("file" not in chunk for chunk in bar_split["chunks"])

    def test_split_without_orient(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((500, 20, 20)))])
        report = prepare_print(glb, tmp_path / "print", bed_size_mm=BED)
        assert "orientation" not in report["parts"][0]
        assert report["bed_size_mm"] == [220.0, 220.0, 250.0]

    def test_invalid_bed_rejected(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((500, 20, 20)))])
        with pytest.raises(ValueError, match="positive"):
            prepare_print(glb, tmp_path / "print", bed_size_mm=(0.0, 220.0, 250.0))


class TestEstimate:
    def test_box_math(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("cube", box((100, 100, 100)))])
        report = prepare_print(glb, tmp_path / "print", estimate=True)

        values = report["parts"][0]["estimate"]
        # 100 mm cube: 1000 cm3, PLA 1.24 g/cm3 -> 1240 g -> 31.00 at 25/kg.
        assert values["volume_cm3"] == pytest.approx(1000.0, abs=1e-3)
        assert values["mass_g"] == pytest.approx(1240.0, abs=1e-2)
        # 1e6 mm3 / 5 mm3/s * 1.5 + (100 / 0.2) * 2 s = 301000 s
        assert values["time_s"] == pytest.approx(301000.0, abs=0.1)
        assert values["cost"] == pytest.approx(31.0, abs=1e-2)

        section = report["estimate"]
        assert section["totals"] == values
        assert section["parameters"] == {
            "density_g_cm3": 1.24,
            "flow_rate_mm3_s": 5.0,
            "filament_price_per_kg": 25.0,
        }
        heuristic = section["heuristic"]
        assert heuristic["overhead_factor"] == 1.5
        assert heuristic["layer_height_mm"] == 0.2
        assert heuristic["perimeter_overhead_s_per_layer"] == 2.0
        assert "formula" in heuristic

    def test_custom_parameters(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("cube", box((100, 100, 100)))])
        report = prepare_print(
            glb,
            tmp_path / "print",
            estimate=True,
            density_g_cm3=2.0,
            flow_rate_mm3_s=10.0,
            filament_price_per_kg=50.0,
        )
        values = report["parts"][0]["estimate"]
        assert values["mass_g"] == pytest.approx(2000.0, abs=1e-2)
        # 1e6 / 10 * 1.5 + 1000 = 151000 s
        assert values["time_s"] == pytest.approx(151000.0, abs=0.1)
        assert values["cost"] == pytest.approx(100.0, abs=1e-2)

    def test_totals_sum_parts(self, tmp_path: Path) -> None:
        glb = write_glb(
            tmp_path,
            "weapon",
            [("small", box((100, 100, 100))), ("big", box((200, 200, 200)))],
        )
        report = prepare_print(glb, tmp_path / "print", estimate=True)
        totals = report["estimate"]["totals"]
        assert totals["volume_cm3"] == pytest.approx(9000.0, abs=1e-2)
        assert totals["mass_g"] == pytest.approx(11160.0, abs=0.1)
        assert totals["cost"] == pytest.approx(279.0, abs=1e-2)
        assert totals["time_s"] == pytest.approx(301000.0 + 2402000.0, abs=0.2)

    def test_invalid_parameters_rejected(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("cube", box((100, 100, 100)))])
        with pytest.raises(ValueError, match="positive"):
            prepare_print(glb, tmp_path / "print", estimate=True, flow_rate_mm3_s=0.0)


class TestCombined:
    def test_orient_bed_estimate_together(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((20, 20, 500)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out, orient=True, bed_size_mm=BED, estimate=True)

        part = report["parts"][0]
        assert part["size_mm"][2] == pytest.approx(20.0, abs=1e-3)
        assert part["orientation"]["overhang_area_mm2"] == pytest.approx(0.0, abs=1e-6)
        assert len(part["split"]["chunks"]) == 3
        values = part["estimate"]
        assert values["volume_cm3"] == pytest.approx(200.0, abs=1e-3)
        assert values["mass_g"] == pytest.approx(248.0, abs=1e-2)
        # Oriented height 20 mm: 200000/5*1.5 + (20/0.2)*2 = 60200 s
        assert values["time_s"] == pytest.approx(60200.0, abs=0.1)
        assert values["cost"] == pytest.approx(6.2, abs=1e-2)
        assert report["estimate"]["totals"]["volume_cm3"] == pytest.approx(200.0)
        for index in range(1, 4):
            assert (out / f"weapon__bar_chunk{index:02d}.stl").exists()

    def test_orient_then_split_order(self, tmp_path: Path) -> None:
        # Standing 20x500x20 plate: orientation lays it flat (500 along
        # X or Y), then the bed split cuts the long horizontal axis.
        glb = write_glb(tmp_path, "weapon", [("plate", box((20, 500, 20)))])
        report = prepare_print(glb, tmp_path / "print", orient=True, bed_size_mm=BED)
        part = report["parts"][0]
        assert part["size_mm"][2] == pytest.approx(20.0, abs=1e-3)
        assert part["split"]["was_split"] is True
        assert all(
            chunk["size_mm"][0] <= 220.0 + 1e-6 and chunk["size_mm"][1] <= 220.0 + 1e-6
            for chunk in part["split"]["chunks"]
        )


class TestBackwardCompat:
    def test_no_flags_keeps_old_report_shape(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("cube", box((100, 100, 100)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out)

        assert set(report) == {
            "schema",
            "weapon",
            "units",
            "real_scale",
            "merged",
            "parts",
        }
        assert set(report["parts"][0]) == {
            "name",
            "status",
            "triangles",
            "watertight",
            "size_mm",
            "notes",
        }
        assert (out / "weapon__cube.stl").exists()
        on_disk = json.loads((out / "print_report.json").read_text())
        assert on_disk == report

    def test_no_flags_keeps_old_orientation(self, tmp_path: Path) -> None:
        glb = write_glb(tmp_path, "weapon", [("bar", box((20, 20, 300)))])
        out = tmp_path / "print"
        report = prepare_print(glb, out)
        assert report["parts"][0]["size_mm"] == [20.0, 20.0, 300.0]
        mesh = load_stl(out / "weapon__bar.stl")
        assert mesh.extents[2] == pytest.approx(300.0, abs=1e-3)


class TestBedSizeParser:
    def test_valid(self) -> None:
        assert _parse_bed_size("220x220x250") == (220.0, 220.0, 250.0)
        assert _parse_bed_size("1.5X2x3") == (1.5, 2.0, 3.0)

    @pytest.mark.parametrize("value", ["abc", "220x220", "1x2x3x4", "-1x2x3", "0x2x3"])
    def test_invalid(self, value: str) -> None:
        import click

        with pytest.raises(click.BadParameter):
            _parse_bed_size(value)


class TestPrintExportCli:
    def _scene(self, base: Path, weapon: str = "wpn") -> Path:
        scene_dir = base / "runtime" / "scenes" / weapon
        scene_dir.mkdir(parents=True, exist_ok=True)
        glb = write_glb(scene_dir, weapon, [("bar", box((500, 20, 20)))])
        return glb

    def test_full_flags(self, tmp_path: Path) -> None:
        from wog_plugin_print.cli import print_export

        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        self._scene(tmp_path)
        result = CliRunner().invoke(
            print_export,
            [
                "wpn",
                "--orient",
                "--bed-size",
                "220x220x250",
                "--estimate",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Print estimate" in result.output
        assert "TOTAL" in result.output
        assert "split into 3 chunks" in result.output
        report = json.loads(
            (
                tmp_path / "runtime" / "scenes" / "wpn" / "print" / "print_report.json"
            ).read_text()
        )
        assert report["bed_size_mm"] == [220.0, 220.0, 250.0]
        assert "estimate" in report
        assert "orientation" in report["parts"][0]

    def test_bad_bed_size_fails_fast(self, tmp_path: Path) -> None:
        from wog_plugin_print.cli import print_export

        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        self._scene(tmp_path)
        result = CliRunner().invoke(print_export, ["wpn", "--bed-size", "220x220"])
        assert result.exit_code != 0

    def test_no_flags_cli_unchanged(self, tmp_path: Path) -> None:
        from wog_plugin_print.cli import print_export

        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        self._scene(tmp_path)
        result = CliRunner().invoke(print_export, ["wpn"])
        assert result.exit_code == 0, result.output
        assert "Print estimate" not in result.output
        report = json.loads(
            (
                tmp_path / "runtime" / "scenes" / "wpn" / "print" / "print_report.json"
            ).read_text()
        )
        assert "estimate" not in report
        assert "bed_size_mm" not in report
