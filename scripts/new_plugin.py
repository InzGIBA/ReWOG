#!/usr/bin/env python3
"""Scaffold a new WOG Dump plugin package.

Usage:
    python scripts/new_plugin.py my-format --type exporter
    python scripts/new_plugin.py my-state --type state --dir plugins

Creates a ready-to-install package (pyproject + module + tests) for one
of the four extension groups, then prints the install/test commands.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

GROUPS = {
    "extractor": "wog_dump.extractors",
    "exporter": "wog_dump.exporters",
    "command": "wog_dump.commands",
    "state": "wog_dump.scene_states",
    "viewer": "wog_dump.viewers",
}

PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{dist}"
version = "0.1.0"
description = "WOG Dump plugin: {name} ({kind})"
requires-python = ">=3.12"
dependencies = ["rewog>=3.3"{extra_deps}]

[project.entry-points."{group}"]
{entry}

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg}"]
"""

CONFTEST = """\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""

TEMPLATES = {
    "exporter": '''\
"""WOG Dump exporter plugin: {name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["{cls}"]


class {cls}:
    name = "{name}"
    extensions = (".{name}",)

    def convert(
        self, glb_path: Path, output_dir: Path, **options: Any
    ) -> list[Path]:
        # options carry ExportOptions fields: units ("m"/"cm"/"mm"), per_part
        raise NotImplementedError("convert {name} from the GLB at glb_path")
''',
    "command": '''\
"""WOG Dump command plugin: {name}."""

from __future__ import annotations

import click

__all__ = ["{snake}"]


@click.command("{name}")
def {snake}() -> None:
    """Describe what {name} does."""
    click.echo("Hello from the {name} plugin")
''',
    "state": '''\
"""WOG Dump scene-state plugin: {name}."""

from __future__ import annotations

from typing import Any

from wog_dump.core.scene_states import PartPlacement

__all__ = ["{cls}"]


class {cls}:
    name = "{name}"

    def layout(
        self, root: Any, unit_scale: float, stats: Any, **options: Any
    ) -> list[PartPlacement]:
        # Rewrite node.translation in Unity space (parents before children),
        # return offsets in glTF meters: (dx*s, dy*s, -dz*s).
        # options: explode_distance, part_order, mesh_bounds_fn
        return []
''',
    "extractor": '''\
"""WOG Dump extractor plugin: {name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wog_dump.core.unpack import AssetExtractor

__all__ = ["{cls}"]


class {cls}(AssetExtractor):
    obj_type = "{name}"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        raise NotImplementedError("extract {name} objects")
''',
    "viewer": '''\
"""WOG Dump viewer plugin: {name}."""

from __future__ import annotations

__all__ = ["{cls}"]


class {cls}:
    name = "{name}"

    def render_html(
        self,
        glb: bytes,
        manifest: dict,
        *,
        linked: bool = False,
        glb_url: str | None = None,
        vendor_base: str = "./vendor",
        font_url_base: str | None = None,
    ) -> str:
        # Return one HTML document; linked=False must work offline
        raise NotImplementedError("render the {name} viewer")
''',
}

TESTS = {
    "exporter": """\
from wog_dump.core.extensions import Exporter

from {pkg} import {cls}


def test_protocol() -> None:
    plugin = {cls}()
    assert isinstance(plugin, Exporter)
    assert plugin.name == "{name}"
    assert plugin.extensions == (".{name}",)
""",
    "command": """\
from click.testing import CliRunner

from {pkg} import {snake}


def test_command_runs() -> None:
    result = CliRunner().invoke({snake}, [])
    assert result.exit_code == 0
""",
    "state": """\
from wog_dump.core.extensions import StateProvider

from {pkg} import {cls}


def test_protocol() -> None:
    plugin = {cls}()
    assert isinstance(plugin, StateProvider)
    assert plugin.name == "{name}"
""",
    "extractor": """\
from {pkg} import {cls}


def test_obj_type() -> None:
    assert {cls}.obj_type == "{name}"
""",
    "viewer": """\
from wog_dump.core.extensions import ViewerProvider

from {pkg} import {cls}


def test_protocol() -> None:
    plugin = {cls}()
    assert isinstance(plugin, ViewerProvider)
    assert plugin.name == "{name}"
""",
}


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[-_]", name))


def scaffold(name: str, kind: str, base_dir: Path) -> Path:
    dist = f"wog-plugin-{name}"
    pkg = f"wog_plugin_{name.replace('-', '_')}"
    cls = (
        _class_name(name)
        + {
            "exporter": "Exporter",
            "command": "",
            "state": "State",
            "extractor": "Extractor",
            "viewer": "Viewer",
        }[kind]
    )
    snake = name.replace("-", "_")
    root = base_dir / dist
    if root.exists():
        raise SystemExit(f"Refusing to overwrite existing directory: {root}")

    entry = (
        f'{name} = "{pkg}:{snake}"' if kind == "command" else f'{name} = "{pkg}:{cls}"'
    )
    extra_deps = ', "click>=8.1"' if kind == "command" else ""
    values = {
        "dist": dist,
        "pkg": pkg,
        "name": name,
        "cls": cls,
        "snake": snake,
        "kind": kind,
        "group": GROUPS[kind],
        "entry": entry,
        "extra_deps": extra_deps,
    }

    (root / "src" / pkg).mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT.format(**values), encoding="utf-8")
    (root / "src" / pkg / "__init__.py").write_text(
        TEMPLATES[kind].format(**values), encoding="utf-8"
    )
    (root / "tests" / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "tests" / f"test_{snake}.py").write_text(
        TESTS[kind].format(**values), encoding="utf-8"
    )
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "name", help="plugin name, lowercase kebab-case (e.g. my-format)"
    )
    parser.add_argument("--type", choices=sorted(GROUPS), required=True, dest="kind")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path("plugins"),
        help="parent directory (default: plugins)",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", args.name):
        parser.error("name must be lowercase kebab-case, e.g. my-format")

    root = scaffold(args.name, args.kind, args.dir)
    print(f"Created {root}")
    print("Next steps:")
    print(f"  uv pip install -e {root}")
    print(f"  python -m pytest {root}/tests -p no:cacheprovider")
    print("  wog-dump plugins")


if __name__ == "__main__":
    sys.exit(main())
