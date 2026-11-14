"""Behavior tests for the scene-report command (no installation)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from click.testing import CliRunner
from wog_plugin_report import scene_report

from wog_dump.core.config import WOGConfig

MakeScene = Callable[[str, dict[str, Any], dict[str, Any] | None], Path]


def test_command_contract() -> None:
    assert isinstance(scene_report, click.Command)
    assert scene_report.name == "scene-report"


def test_markdown_report(make_scene: MakeScene, manifest: dict[str, Any]) -> None:
    make_scene("ak47", manifest)
    result = CliRunner().invoke(scene_report, ["ak47"])
    assert result.exit_code == 0, result.output
    text = result.stdout
    assert "# Scene report: ak47" in text
    assert "field-strip" in text
    assert "barrel" in text
    assert "magazine" in text
    assert "## Parts" in text
    assert "## Placements" in text
    assert "Count: 2" in text
    assert "100.00" in text
    assert "## Anchor checks" in text
    assert "## Skipped" in text
    assert "assembled, field-strip" in text


def test_json_report(make_scene: MakeScene, manifest: dict[str, Any]) -> None:
    make_scene("ak47", manifest)
    result = CliRunner().invoke(scene_report, ["ak47", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["weapon"] == "ak47"
    assert data["state"] == "field-strip"
    assert data["units"] == "meters"
    assert data["counts"] == {
        "meshes": 173,
        "materials": 22,
        "textures": 8,
        "nodes": 174,
    }
    assert data["parts"][0] == {
        "name": "barrel",
        "group": "parts",
        "bbox_size_mm": [20.0, 30.0, 400.0],
    }
    assert data["placements"]["count"] == 2
    top = data["placements"]["top"][0]
    assert top["name"] == "magazine"
    assert top["offset_mm"] == [0.0, -100.0, 0.0]
    assert top["magnitude_mm"] == 100.0
    assert data["anchor_checks"] == {"ok": 2, "missing": 1}
    assert data["skipped"] == {"arrows": 102, "vfx": 2}
    assert data["viewer_states"] == ["assembled", "field-strip"]
    assert data["print"] is None


def test_missing_scene_fails_with_hint(scene_config: WOGConfig) -> None:
    result = CliRunner().invoke(scene_report, ["ghost"])
    assert result.exit_code == 1
    assert "ghost" in result.stderr
    assert "build-scene" in result.stderr


def test_unsafe_name_rejected() -> None:
    result = CliRunner().invoke(scene_report, ["../evil"])
    assert result.exit_code == 2
    assert "Unsafe weapon name" in result.output


def test_output_file(
    tmp_path: Path, make_scene: MakeScene, manifest: dict[str, Any]
) -> None:
    make_scene("ak47", manifest)
    out = tmp_path / "reports" / "ak47.md"
    result = CliRunner().invoke(scene_report, ["ak47", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "# Scene report: ak47" in out.read_text(encoding="utf-8")


def test_output_dir_multiple_weapons(
    tmp_path: Path, make_scene: MakeScene, manifest: dict[str, Any]
) -> None:
    make_scene("ak47", manifest)
    make_scene("m4a1", {**manifest, "weapon": "m4a1"})
    out_dir = tmp_path / "reports"
    result = CliRunner().invoke(
        scene_report, ["ak47", "m4a1", "--output", str(out_dir)]
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "ak47-report.md").is_file()
    assert (out_dir / "m4a1-report.md").is_file()
    assert "# Scene report: m4a1" in (out_dir / "m4a1-report.md").read_text(
        encoding="utf-8"
    )


def test_multi_weapon_partial_failure(
    make_scene: MakeScene, manifest: dict[str, Any]
) -> None:
    make_scene("ak47", manifest)
    result = CliRunner().invoke(scene_report, ["ak47", "ghost"])
    assert result.exit_code == 1
    assert "# Scene report: ak47" in result.stdout
    assert "ghost" in result.stderr
    assert "build-scene" in result.stderr


def test_json_multi_weapon_partial_failure(
    make_scene: MakeScene, manifest: dict[str, Any]
) -> None:
    make_scene("ak47", manifest)
    result = CliRunner().invoke(scene_report, ["ak47", "ghost", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert list(data) == ["ak47"]
    assert data["ak47"]["weapon"] == "ak47"


def test_print_diagnostics_markdown(
    make_scene: MakeScene,
    manifest: dict[str, Any],
    print_report: dict[str, Any],
) -> None:
    make_scene("ak47", manifest, print_report)
    result = CliRunner().invoke(scene_report, ["ak47"])
    assert result.exit_code == 0, result.output
    text = result.stdout
    assert "## Print diagnostics" in text
    assert "| barrel | yes | ok |" in text
    assert "| magazine | no | warnings |" in text
    assert "Estimate totals:" in text
    assert "mass_g: 15.0" in text


def test_print_diagnostics_json(
    make_scene: MakeScene,
    manifest: dict[str, Any],
    print_report: dict[str, Any],
) -> None:
    make_scene("ak47", manifest, print_report)
    result = CliRunner().invoke(scene_report, ["ak47", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["print"]["parts"][0]["watertight"] is True
    assert data["print"]["parts"][1]["watertight"] is False
    assert data["print"]["estimate_totals"]["mass_g"] == 15.0


def test_json_output_file(
    tmp_path: Path, make_scene: MakeScene, manifest: dict[str, Any]
) -> None:
    make_scene("ak47", manifest)
    out = tmp_path / "ak47.json"
    result = CliRunner().invoke(scene_report, ["ak47", "--json", "--output", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["weapon"] == "ak47"


def test_empty_placements(make_scene: MakeScene, manifest: dict[str, Any]) -> None:
    make_scene("ak47", {**manifest, "placements": []})
    result = CliRunner().invoke(scene_report, ["ak47"])
    assert result.exit_code == 0, result.output
    assert "No placements recorded." in result.stdout


def test_corrupt_manifest_fails(
    scene_config: WOGConfig, make_scene: MakeScene, manifest: dict[str, Any]
) -> None:
    scene_dir = make_scene("ak47", manifest)
    (scene_dir / "scene.json").write_text("{not json", encoding="utf-8")
    result = CliRunner().invoke(scene_report, ["ak47"])
    assert result.exit_code == 1
    assert "ak47" in result.stderr
