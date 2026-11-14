"""Unit tests for the plugins listing command and the plugin scaffold."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from wog_dump.cli.plugins import plugins
from wog_dump.core import extensions
from wog_dump.core import unpack as unpack_module
from wog_dump.utils.logging import get_logger


class FakeEP:
    def __init__(self, name: str, target: Any) -> None:
        self.name = name
        self.target = target
        self.dist = Mock(metadata={"Name": "fake-plugin"})

    def load(self) -> Any:
        return self.target


class FakeExporter:
    name = "fakefmt"
    extensions = (".fake",)

    def convert(self, glb_path: Path, output_dir: Path, **options: Any) -> list[Path]:
        return []


class FakeState:
    name = "fakestate"

    def layout(self, root: Any, unit_scale: float, stats: Any, **options: Any) -> list:
        return []


@click.command("fakecmd")
def fakecmd() -> None:
    """Fake."""


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: Any) -> Generator[None, None, None]:
    monkeypatch.setattr(unpack_module, "_MERGED_EXTRACTORS", None)
    extensions.reset_extension_cache()
    yield
    extensions.reset_extension_cache()


def patch_eps(monkeypatch: Any, groups: dict[str, list[FakeEP]]) -> None:
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda group=None: groups.get(group, []) if group else {},
    )


def invoke(args: list[str]) -> Any:
    return CliRunner().invoke(plugins, args, obj={"logger": get_logger()})


class TestPluginsCommand:
    def test_empty_lists_no_plugins(self, monkeypatch: Any) -> None:
        patch_eps(monkeypatch, {})
        result = invoke([])
        assert result.exit_code == 0
        assert "No plugins installed" in result.output

    def test_lists_all_groups(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {
                "wog_dump.exporters": [FakeEP("fakefmt", FakeExporter)],
                "wog_dump.commands": [FakeEP("fakecmd", fakecmd)],
                "wog_dump.scene_states": [FakeEP("fakestate", FakeState)],
            },
        )
        result = invoke([])
        assert result.exit_code == 0
        for needle in ("fakefmt", "fakecmd", "fakestate", ".fake", "fake-plugin"):
            assert needle in result.output

    def test_json_output(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {"wog_dump.exporters": [FakeEP("fakefmt", FakeExporter)]},
        )
        result = invoke(["--json"])
        assert result.exit_code == 0
        start = result.stdout.index("{")
        payload = json.loads(result.stdout[start:])
        assert payload["exporters"]["fakefmt"]["extensions"] == [".fake"]
        assert set(payload) == {
            "extractors",
            "exporters",
            "commands",
            "scene_states",
            "viewers",
        }


def load_scaffold_module() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "new_plugin.py"
    spec = importlib.util.spec_from_file_location("new_plugin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["new_plugin"] = module
    spec.loader.exec_module(module)
    return module


class TestScaffold:
    @pytest.mark.parametrize(
        "kind", ["exporter", "command", "state", "extractor", "viewer"]
    )
    def test_scaffold_creates_installable_package(
        self, tmp_path: Path, kind: str
    ) -> None:
        module = load_scaffold_module()
        root = module.scaffold("demo-thing", kind, tmp_path)
        assert (root / "pyproject.toml").is_file()
        assert (root / "src" / "wog_plugin_demo_thing" / "__init__.py").is_file()
        assert (root / "tests" / "conftest.py").is_file()
        pyproject = (root / "pyproject.toml").read_text()
        assert module.GROUPS[kind] in pyproject
        assert 'name = "wog-plugin-demo-thing"' in pyproject

    def test_scaffold_refuses_overwrite(self, tmp_path: Path) -> None:
        module = load_scaffold_module()
        module.scaffold("demo", "exporter", tmp_path)
        with pytest.raises(SystemExit, match="Refusing"):
            module.scaffold("demo", "exporter", tmp_path)

    def test_generated_exporter_importable(self, tmp_path: Path) -> None:
        module = load_scaffold_module()
        root = module.scaffold("demo", "exporter", tmp_path)
        src = str(root / "src")
        sys.path.insert(0, src)
        try:
            import wog_plugin_demo

            plugin = wog_plugin_demo.DemoExporter()
            assert plugin.name == "demo"
            assert plugin.extensions == (".demo",)
        finally:
            sys.path.remove(src)
            sys.modules.pop("wog_plugin_demo", None)
