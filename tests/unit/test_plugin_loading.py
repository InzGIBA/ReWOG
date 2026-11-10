"""Unit tests for extractor plugin loading (task 14)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from wog_dump.cli.unpack import unpack_assets
from wog_dump.core import unpack as unpack_module
from wog_dump.core.unpack import AssetExtractor, get_extractors


@dataclass
class FakeEP:
    """Entry point stand-in."""

    name: str
    target: Any
    dist_name: str = "fake-plugin"

    def load(self) -> Any:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target

    @property
    def dist(self) -> Any:
        return Mock(metadata={"Name": self.dist_name})


class FakeExtractor(AssetExtractor):
    obj_type = "FakeType"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        return None


class SpriteOverride(AssetExtractor):
    obj_type = "Texture2D"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        return None


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: Any) -> None:
    monkeypatch.setattr(unpack_module, "_MERGED_EXTRACTORS", None)


def patch_eps(monkeypatch: Any, eps: list[FakeEP]) -> None:
    monkeypatch.setattr(
        "importlib.metadata.entry_points", lambda group=None: eps if group else {}
    )


class TestPluginLoading:
    def test_plugin_added_to_registry(self, monkeypatch: Any) -> None:
        patch_eps(monkeypatch, [FakeEP("fake", FakeExtractor)])

        registry = get_extractors()

        assert isinstance(registry["FakeType"], FakeExtractor)

    def test_plugin_overrides_builtin_with_warning(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(monkeypatch, [FakeEP("override", SpriteOverride)])

        registry = get_extractors(logger)

        assert isinstance(registry["Texture2D"], SpriteOverride)
        assert registry["Mesh"] is not None  # untouched built-ins stay
        assert any("overrides" in str(call) for call in logger.warning.call_args_list)

    def test_broken_plugin_warns_and_continues(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(
            monkeypatch,
            [
                FakeEP("broken", ImportError("no such module")),
                FakeEP("working", FakeExtractor),
            ],
        )

        registry = get_extractors(logger)

        assert "FakeType" in registry
        assert logger.warning.called

    def test_non_extractor_rejected(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(monkeypatch, [FakeEP("junk", dict)])

        registry = get_extractors(logger)

        assert "FakeType" not in registry
        assert any(
            "not an AssetExtractor" in str(c) for c in logger.warning.call_args_list
        )

    def test_instance_entry_point_accepted(self, monkeypatch: Any) -> None:
        instance = FakeExtractor()
        patch_eps(monkeypatch, [FakeEP("fake", instance)])

        registry = get_extractors()

        assert registry["FakeType"] is instance

    def test_empty_obj_type_rejected(self, monkeypatch: Any) -> None:
        class NoType(AssetExtractor):
            obj_type = ""

        logger = Mock()
        patch_eps(monkeypatch, [FakeEP("notype", NoType)])

        registry = get_extractors(logger)

        assert registry.get("") is None


class TestListExtractorsCli:
    def test_lists_builtin_and_source(self, tmp_path: Path) -> None:
        from wog_dump.cli.main import cli
        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["unpack-assets", "--list-extractors"])

        assert result.exit_code == 0, result.output
        assert "Texture2D" in result.output
        assert "built-in" in result.output

    def test_lists_plugin_package(self, tmp_path: Path, monkeypatch: Any) -> None:
        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        patch_eps(monkeypatch, [FakeEP("fake", FakeExtractor, "acme-plugin")])
        unpack_module._MERGED_EXTRACTORS = None

        from wog_dump.cli.main import cli
        from wog_dump.core.config import set_config as _sc

        _sc(base_dir=tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["unpack-assets", "--list-extractors"])

        assert "FakeType" in result.output
        assert "acme-plugin" in result.output
