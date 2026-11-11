"""Unit tests for the catalog command (task 07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from wog_dump.cli.catalog import (
    _apply_filters,
    _entries_to_json,
    _human_size,
    _sort_entries,
)
from wog_dump.core.catalog import CATALOG_SCHEMA, WeaponEntry, build_catalog


def make_runtime(base: Path, weapons: dict[str, str]) -> None:
    """Create a runtime tree; value marks the furthest stage per weapon."""
    encrypted = base / "runtime" / "encrypted"
    decrypted = base / "runtime" / "decrypted"
    unpacked = base / "runtime" / "unpacked"
    scenes = base / "runtime" / "scenes"
    encrypted.mkdir(parents=True)
    decrypted.mkdir(parents=True)

    stages = ("downloaded", "decrypted", "unpacked", "scene")
    for name, furthest in weapons.items():
        (encrypted / f"{name}.bytes").write_bytes(b"x" * 1000)
        index = stages.index(furthest)
        (decrypted / f"{name}.unity3d").write_bytes(b"x" * 4000)
        if index >= 2:
            (unpacked / name).mkdir(parents=True)
        if index >= 3:
            (scenes / name).mkdir(parents=True)
            (scenes / name / f"{name}.glb").write_bytes(b"glb")


class TestBuildCatalog:
    def test_stage_flags(self, tmp_path: Path) -> None:
        make_runtime(
            tmp_path,
            {"ak47": "scene", "ak74n": "unpacked", "m4a1": "decrypted"},
        )

        from wog_dump.core.config import WOGConfig

        entries = {e.name: e for e in build_catalog(WOGConfig(base_dir=tmp_path))}

        ak47 = entries["ak47"]
        assert ak47.downloaded and ak47.decrypted and ak47.unpacked and ak47.scene
        assert ak47.size_bytes == 5000
        assert not ak47.has_key  # empty database
        assert not ak47.previews

        ak74n = entries["ak74n"]
        assert ak74n.unpacked and not ak74n.scene

    def test_scene_state_dirs_not_counted_as_scene(self, tmp_path: Path) -> None:
        make_runtime(tmp_path, {"ak47": "decrypted"})
        state_dir = tmp_path / "runtime" / "scenes" / "ak47-field-strip"
        state_dir.mkdir(parents=True)
        (state_dir / "ak47.glb").write_bytes(b"glb")

        from wog_dump.core.config import WOGConfig

        entries = {e.name: e for e in build_catalog(WOGConfig(base_dir=tmp_path))}
        assert "ak47-field-strip" not in entries
        assert not entries["ak47"].scene

    def test_previews_flag(self, tmp_path: Path) -> None:
        make_runtime(tmp_path, {"ak47": "scene"})
        previews = tmp_path / "runtime" / "scenes" / "ak47" / "previews"
        previews.mkdir()

        from wog_dump.core.config import WOGConfig

        entries = {e.name: e for e in build_catalog(WOGConfig(base_dir=tmp_path))}
        assert entries["ak47"].previews

    def test_missing_runtime_dirs(self, tmp_path: Path) -> None:
        from wog_dump.core.config import WOGConfig

        assert build_catalog(WOGConfig(base_dir=tmp_path)) == []


class TestFiltersAndSorting:
    def entries(self) -> list[WeaponEntry]:
        return [
            WeaponEntry(name="ak47", downloaded=True, decrypted=True, size_bytes=30),
            WeaponEntry(name="ak74n", downloaded=True, size_bytes=50),
            WeaponEntry(
                name="m4a1",
                downloaded=True,
                decrypted=True,
                unpacked=True,
                scene=True,
                size_bytes=10,
            ),
        ]

    def test_filter_substring(self) -> None:
        names = [e.name for e in _apply_filters(self.entries(), "ak", None)]
        assert names == ["ak47", "ak74n"]

    def test_status_filter(self) -> None:
        names = [e.name for e in _apply_filters(self.entries(), None, "scene")]
        assert names == ["m4a1"]

    def test_sort_size_and_status(self) -> None:
        by_size = [e.name for e in _sort_entries(self.entries(), "size", False)]
        assert by_size == ["m4a1", "ak47", "ak74n"]
        by_status = [e.name for e in _sort_entries(self.entries(), "status", False)]
        assert by_status[0] == "m4a1"  # furthest along first

    def test_human_size(self) -> None:
        assert _human_size(0) == "-"
        assert _human_size(999) == "999 B"
        assert _human_size(5000) == "4.9 KB"
        assert _human_size(5 * 1024 * 1024) == "5.0 MB"


class TestJsonSchema:
    def test_schema_shape(self) -> None:
        payload = _entries_to_json(
            [WeaponEntry(name="ak47", downloaded=True, size_bytes=123)]
        )
        assert payload["schema"] == CATALOG_SCHEMA
        assert payload["schema"] == "wog-dump.catalog/3"
        assert payload["count"] == 1
        weapon = payload["weapons"][0]
        assert set(weapon) == {
            "name",
            "has_key",
            "downloaded",
            "decrypted",
            "unpacked",
            "scene",
            "previews",
            "size_bytes",
            "display_name",
            "categories",
            "num_parts",
            "since",
            "icon",
            "test",
        }


class TestCatalogCli:
    def test_cli_json_output(self, tmp_path: Path) -> None:
        from wog_dump.cli.main import cli
        from wog_dump.core.config import set_config

        make_runtime(tmp_path, {"ak47": "scene", "ak74n": "decrypted"})
        set_config(base_dir=tmp_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["catalog", "--json"])
        assert result.exit_code == 0, result.output
        # The banner precedes the JSON in standalone invocation output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["schema"] == "wog-dump.catalog/3"
        assert {w["name"] for w in payload["weapons"]} == {"ak47", "ak74n"}
        assert payload["weapons"][0]["size_bytes"] == 5000

    def test_cli_empty_catalog_message(self, tmp_path: Path) -> None:
        from wog_dump.cli.main import cli
        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["catalog"])
        assert result.exit_code == 0
        assert "download-weapons" in result.output
