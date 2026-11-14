"""Import the plugin from source (no installation) and patch config."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

try:
    import wog_dump  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from wog_dump.core.config import WOGConfig  # noqa: E402


@pytest.fixture
def scene_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WOGConfig:
    config = WOGConfig(base_dir=tmp_path)
    monkeypatch.setattr("wog_dump.core.config.get_config", lambda: config)
    monkeypatch.setattr("wog_plugin_report.command.get_config", lambda: config)
    return config


@pytest.fixture
def make_scene(
    scene_config: WOGConfig,
) -> Callable[[str, dict[str, Any], dict[str, Any] | None], Path]:
    def _make(
        weapon: str,
        manifest: dict[str, Any],
        print_report: dict[str, Any] | None = None,
    ) -> Path:
        scene_dir = scene_config.base_dir / "runtime" / "scenes" / weapon
        scene_dir.mkdir(parents=True, exist_ok=True)
        (scene_dir / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
        if print_report is not None:
            print_dir = scene_dir / "print"
            print_dir.mkdir(exist_ok=True)
            (print_dir / "print_report.json").write_text(
                json.dumps(print_report), encoding="utf-8"
            )
        return scene_dir

    return _make


@pytest.fixture
def manifest() -> dict[str, Any]:
    return {
        "weapon": "ak47",
        "state": "field-strip",
        "units": "meters",
        "meshes": 173,
        "materials": 22,
        "textures": 8,
        "nodes": 174,
        "parts": ["barrel", "magazine"],
        "field_strip": ["magazine", "bolt"],
        "annotations": [
            {"name": "barrel", "group": "parts", "bbox_size": [0.02, 0.03, 0.4]},
            {
                "name": "magazine",
                "group": "field_strip",
                "bbox_size": [0.05, 0.15, 0.08],
            },
        ],
        "placements": [
            {"name": "magazine", "offset": [0.0, -0.1, 0.0]},
            {"name": "bolt", "offset": [0.01, 0.02, 0.03]},
        ],
        "anchor_checks": {"ok": 2, "missing": 1},
        "skipped": {"arrows": 102, "vfx": 2},
        "viewer_states": {"assembled": {}, "field-strip": {}},
    }


@pytest.fixture
def print_report() -> dict[str, Any]:
    return {
        "schema": "wog.print/1",
        "weapon": "ak47",
        "units": "mm",
        "parts": [
            {
                "name": "barrel",
                "status": "ok",
                "watertight": True,
                "triangles": 100,
                "size_mm": [20.0, 30.0, 400.0],
            },
            {
                "name": "magazine",
                "status": "warnings",
                "watertight": False,
                "triangles": 50,
                "size_mm": [50.0, 150.0, 80.0],
            },
        ],
        "estimate": {
            "totals": {
                "volume_cm3": 12.5,
                "mass_g": 15.0,
                "time_s": 3600.0,
                "cost": 0.5,
            }
        },
    }
