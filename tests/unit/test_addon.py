"""Unit tests for the Blender addon client and packaging (task 21).

The addon package stays bpy-free at the client layer, so these tests
run outside Blender.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

ADDON_SRC = Path(__file__).resolve().parents[2] / "blender-addon" / "wog_dump_addon"
sys.path.insert(0, str(ADDON_SRC))

import wog_client  # noqa: E402
from wog_client import (  # noqa: E402
    CatalogEntry,
    WogClientError,
    load_catalog,
    parse_catalog,
    scene_glb_path,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "build_addon.py"


def catalog_payload() -> dict[str, Any]:
    return {
        "schema": "wog-dump.catalog/1",
        "count": 2,
        "weapons": [
            {"name": "ak47", "scene": True},
            {"name": "m4a1", "scene": False},
        ],
    }


class TestParseCatalog:
    def test_valid_payload(self) -> None:
        entries = parse_catalog(json.dumps(catalog_payload()))
        assert entries == [
            CatalogEntry(name="ak47", has_scene=True),
            CatalogEntry(name="m4a1", has_scene=False),
        ]

    def test_banner_before_json_is_ignored(self) -> None:
        payload = "🔫 WOG Dump v2.7.1\n" + json.dumps(catalog_payload())
        assert len(parse_catalog(payload)) == 2

    def test_no_json_raises(self) -> None:
        with pytest.raises(WogClientError, match="no JSON"):
            parse_catalog("total garbage")

    def test_wrong_schema_tells_user_to_update(self) -> None:
        payload = catalog_payload()
        payload["schema"] = "wog-dump.catalog/2"
        with pytest.raises(WogClientError, match="update the wog-dump toolkit"):
            parse_catalog(json.dumps(payload))


class TestLoadCatalog:
    def test_failure_surfaces_last_stderr_line(self) -> None:
        with patch.object(wog_client, "run_wog") as run_wog:
            run_wog.return_value = (1, "", "line one\nnot logged in\n")
            with pytest.raises(WogClientError, match="not logged in"):
                load_catalog("wog-dump")

    def test_success_path_parses(self) -> None:
        with patch.object(wog_client, "run_wog") as run_wog:
            run_wog.return_value = (0, json.dumps(catalog_payload()), "")
            entries = load_catalog("wog-dump", "/some/dir")
        assert entries[0].name == "ak47"
        args = run_wog.call_args[0]
        assert args[1] == ["catalog", "--json"]
        assert args[2] == "/some/dir"


class TestScenePath:
    def test_runtime_layout(self, tmp_path: Path) -> None:
        path = scene_glb_path(str(tmp_path), "ak47")
        assert path == tmp_path / "runtime" / "scenes" / "ak47" / "ak47.glb"


class TestAddonPackaging:
    def test_zip_builds_with_manifest(self, tmp_path: Path) -> None:
        sys.path.insert(0, str(SCRIPTS.parent))
        try:
            import build_addon  # type: ignore[import-not-found]
        finally:
            sys.path.remove(str(SCRIPTS.parent))
            sys.modules.pop("build_addon", None)

        zip_path = build_addon.build(tmp_path)
        import zipfile

        names = zipfile.ZipFile(zip_path).namelist()
        assert "wog_dump_addon/blender_manifest.toml" in names
        assert "wog_dump_addon/__init__.py" in names
        assert "wog_dump_addon/wog_client.py" in names

    def test_manifest_fields(self) -> None:
        manifest = (
            Path(__file__).resolve().parents[2]
            / "blender-addon"
            / "blender_manifest.toml"
        ).read_text(encoding="utf-8")
        for key in ("schema_version", "id", "version", "type", "license"):
            assert f"{key} =" in manifest
        assert 'blender_version_min = "4.2.0"' in manifest
