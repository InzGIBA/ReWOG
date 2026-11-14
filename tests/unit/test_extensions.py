"""Unit tests for the extension-group loaders (exporters, commands, states)."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import click
import pytest

from wog_dump.core import extensions
from wog_dump.core import unpack as unpack_module
from wog_dump.core.export import ExportError, ExportOptions, convert_format
from wog_dump.core.extensions import (
    Exporter,
    StateProvider,
    describe_extensions,
    load_exporters,
    load_plugin_commands,
    load_state_providers,
    reset_extension_cache,
)


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


def patch_eps(monkeypatch: Any, groups: dict[str, list[FakeEP]]) -> None:
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda group=None: groups.get(group, []) if group else {},
    )


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: Any) -> Generator[None, None, None]:
    monkeypatch.setattr(unpack_module, "_MERGED_EXTRACTORS", None)
    reset_extension_cache()
    yield
    reset_extension_cache()


class FakeObjExporter:
    name = "obj"
    extensions = (".obj",)

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, dict[str, Any]]] = []

    def convert(self, glb_path: Path, output_dir: Path, **options: Any) -> list[Path]:
        self.calls.append((glb_path, output_dir, options))
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{glb_path.stem}.obj"
        out.write_text("# fake obj\n", encoding="utf-8")
        return [out]


class StlOverride(FakeObjExporter):
    name = "stl"
    extensions = (".stl",)


class FakeStateProvider:
    name = "spin"

    def layout(
        self, root: Any, unit_scale: float, stats: Any, **options: Any
    ) -> list[Any]:
        return []


class FakeViewerProvider:
    name = "fancy"

    def render_html(self, glb: bytes, manifest: dict, **kwargs: Any) -> str:
        return "<html></html>"


@click.command("hello")
def hello_cmd() -> None:
    click.echo("hello")


class TestLoadExporters:
    def test_class_entry_point_loaded(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {extensions.EXPORTERS_GROUP: [FakeEP("obj", FakeObjExporter)]},
        )

        registry = load_exporters()

        assert isinstance(registry["obj"], FakeObjExporter)
        assert isinstance(registry["obj"], Exporter)

    def test_instance_entry_point_accepted(self, monkeypatch: Any) -> None:
        instance = FakeObjExporter()
        patch_eps(monkeypatch, {extensions.EXPORTERS_GROUP: [FakeEP("obj", instance)]})

        loaded: Any = load_exporters()["obj"]
        assert loaded is instance

    def test_import_error_warns_and_continues(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(
            monkeypatch,
            {
                extensions.EXPORTERS_GROUP: [
                    FakeEP("broken", ImportError("no such module")),
                    FakeEP("obj", FakeObjExporter),
                ]
            },
        )

        registry = load_exporters(logger)

        assert "obj" in registry
        assert any("failed to load" in str(c) for c in logger.warning.call_args_list)

    def test_wrong_type_rejected(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(monkeypatch, {extensions.EXPORTERS_GROUP: [FakeEP("junk", dict)]})

        assert load_exporters(logger) == {}
        assert any(
            "is not an Exporter" in str(c) for c in logger.warning.call_args_list
        )

    def test_missing_attrs_rejected(self, monkeypatch: Any) -> None:
        class NoConvert:
            name = "obj"
            extensions = (".obj",)

        logger = Mock()
        patch_eps(
            monkeypatch, {extensions.EXPORTERS_GROUP: [FakeEP("partial", NoConvert)]}
        )

        assert load_exporters(logger) == {}
        assert logger.warning.called

    def test_empty_name_rejected(self, monkeypatch: Any) -> None:
        class NoName(FakeObjExporter):
            name = ""

        logger = Mock()
        patch_eps(monkeypatch, {extensions.EXPORTERS_GROUP: [FakeEP("noname", NoName)]})

        assert load_exporters(logger) == {}
        assert any("sets no name" in str(c) for c in logger.warning.call_args_list)

    def test_invalid_extensions_rejected(self, monkeypatch: Any) -> None:
        class BadExtensions(FakeObjExporter):
            extensions: Any = [".obj"]  # list, not tuple

        logger = Mock()
        patch_eps(
            monkeypatch,
            {extensions.EXPORTERS_GROUP: [FakeEP("badext", BadExtensions)]},
        )

        assert load_exporters(logger) == {}
        assert any(
            "invalid extensions" in str(c) for c in logger.warning.call_args_list
        )

    def test_override_builtin_warns_with_package(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(
            monkeypatch,
            {
                extensions.EXPORTERS_GROUP: [
                    FakeEP("stl", StlOverride, "acme-exporters")
                ]
            },
        )

        registry = load_exporters(logger)

        assert isinstance(registry["stl"], StlOverride)
        assert any(
            "overrides" in str(c) and "acme-exporters" in str(c)
            for c in logger.warning.call_args_list
        )

    def test_entry_points_failure_never_raises(self, monkeypatch: Any) -> None:
        def boom(group: str | None = None) -> Any:
            raise RuntimeError("metadata unreadable")

        monkeypatch.setattr("importlib.metadata.entry_points", boom)
        logger = Mock()

        assert load_exporters(logger) == {}
        assert logger.warning.called


class TestConvertFormatPluginDispatch:
    def test_routes_unknown_format_to_plugin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        plugin = FakeObjExporter()
        patch_eps(monkeypatch, {extensions.EXPORTERS_GROUP: [FakeEP("obj", plugin)]})
        glb = tmp_path / "weapon.glb"
        glb.write_bytes(b"stub")  # plugin must not need a parseable GLB
        out = tmp_path / "out"

        paths = convert_format(
            glb, "obj", out, ExportOptions(units="mm", per_part=True)
        )

        assert paths == [out / "weapon.obj"]
        assert paths[0].exists()
        assert plugin.calls == [(glb, out, {"units": "mm", "per_part": True})]

    def test_plugin_overrides_builtin_format(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        plugin = StlOverride()
        patch_eps(monkeypatch, {extensions.EXPORTERS_GROUP: [FakeEP("stl", plugin)]})
        glb = tmp_path / "weapon.glb"
        glb.write_bytes(b"stub")

        paths = convert_format(glb, "stl", tmp_path / "out")

        assert len(plugin.calls) == 1
        assert paths == [tmp_path / "out" / "weapon.obj"]

    def test_unknown_format_without_plugin_still_raises(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        patch_eps(monkeypatch, {})

        with pytest.raises(ExportError, match="Unknown export format"):
            convert_format(tmp_path / "x.glb", "obj", tmp_path)

    def test_glb_passthrough_unchanged(self, tmp_path: Path, monkeypatch: Any) -> None:
        patch_eps(monkeypatch, {})
        glb = tmp_path / "scene.glb"
        glb.write_bytes(b"stub")

        assert convert_format(glb, "glb", tmp_path) == [glb]


class TestLoadPluginCommands:
    def test_command_and_group_instances(self, monkeypatch: Any) -> None:
        group = click.Group("tools")
        patch_eps(
            monkeypatch,
            {
                extensions.COMMANDS_GROUP: [
                    FakeEP("hello", hello_cmd),
                    FakeEP("tools", group),
                ]
            },
        )

        commands = load_plugin_commands()

        assert commands == [hello_cmd, group]
        assert all(isinstance(cmd, click.Command) for cmd in commands)

    def test_class_target_instantiated_and_named(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch, {extensions.COMMANDS_GROUP: [FakeEP("bare", click.Group)]}
        )

        commands = load_plugin_commands()

        assert len(commands) == 1
        assert isinstance(commands[0], click.Group)
        assert commands[0].name == "bare"

    def test_non_command_rejected(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(
            monkeypatch,
            {
                extensions.COMMANDS_GROUP: [
                    FakeEP("junk", dict),
                    FakeEP("fn", lambda: None),
                ]
            },
        )

        assert load_plugin_commands(logger) == []
        assert any(
            "is not a Click command" in str(c) for c in logger.warning.call_args_list
        )

    def test_broken_plugin_warns_and_continues(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(
            monkeypatch,
            {
                extensions.COMMANDS_GROUP: [
                    FakeEP("broken", ImportError("no such module")),
                    FakeEP("hello", hello_cmd),
                ]
            },
        )

        commands = load_plugin_commands(logger)

        assert commands == [hello_cmd]
        assert logger.warning.called


class TestLoadStateProviders:
    def test_provider_loaded(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {extensions.STATE_PROVIDERS_GROUP: [FakeEP("spin", FakeStateProvider)]},
        )

        registry = load_state_providers()

        assert isinstance(registry["spin"], FakeStateProvider)
        assert isinstance(registry["spin"], StateProvider)
        assert callable(registry["spin"].layout)

    def test_instance_entry_point_accepted(self, monkeypatch: Any) -> None:
        instance = FakeStateProvider()
        patch_eps(
            monkeypatch,
            {extensions.STATE_PROVIDERS_GROUP: [FakeEP("spin", instance)]},
        )

        loaded: Any = load_state_providers()["spin"]
        assert loaded is instance

    def test_missing_layout_rejected(self, monkeypatch: Any) -> None:
        class NoLayout:
            name = "spin"

        logger = Mock()
        patch_eps(
            monkeypatch,
            {extensions.STATE_PROVIDERS_GROUP: [FakeEP("nolayout", NoLayout)]},
        )

        assert load_state_providers(logger) == {}
        assert any(
            "is not a StateProvider" in str(c) for c in logger.warning.call_args_list
        )

    def test_non_callable_layout_rejected(self, monkeypatch: Any) -> None:
        class BadLayout:
            name = "spin"
            layout = 42

        logger = Mock()
        patch_eps(
            monkeypatch,
            {extensions.STATE_PROVIDERS_GROUP: [FakeEP("badlayout", BadLayout)]},
        )

        assert load_state_providers(logger) == {}
        assert any(
            "no callable layout" in str(c) for c in logger.warning.call_args_list
        )

    def test_empty_name_rejected(self, monkeypatch: Any) -> None:
        class NoName(FakeStateProvider):
            name = ""

        logger = Mock()
        patch_eps(
            monkeypatch,
            {extensions.STATE_PROVIDERS_GROUP: [FakeEP("noname", NoName)]},
        )

        assert load_state_providers(logger) == {}
        assert any("sets no name" in str(c) for c in logger.warning.call_args_list)

    def test_broken_plugin_warns_and_continues(self, monkeypatch: Any) -> None:
        logger = Mock()
        patch_eps(
            monkeypatch,
            {
                extensions.STATE_PROVIDERS_GROUP: [
                    FakeEP("broken", ImportError("no such module")),
                    FakeEP("spin", FakeStateProvider),
                ]
            },
        )

        registry = load_state_providers(logger)

        assert "spin" in registry
        assert logger.warning.called


class TestDescribeExtensions:
    def test_shape_covers_all_groups(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {
                extensions.EXPORTERS_GROUP: [
                    FakeEP("obj", FakeObjExporter, "acme-obj")
                ],
                extensions.COMMANDS_GROUP: [FakeEP("hello", hello_cmd, "acme-cmd")],
                extensions.STATE_PROVIDERS_GROUP: [
                    FakeEP("spin", FakeStateProvider, "acme-state")
                ],
                extensions.VIEWERS_GROUP: [
                    FakeEP("fancy", FakeViewerProvider, "acme-viewer")
                ],
            },
        )

        described = describe_extensions()

        assert set(described) == {
            "extractors",
            "exporters",
            "commands",
            "scene_states",
            "viewers",
        }
        assert described["exporters"]["obj"] == {
            "package": "acme-obj",
            "class": "FakeObjExporter",
            "extensions": [".obj"],
        }
        assert described["commands"]["hello"]["package"] == "acme-cmd"
        assert described["scene_states"]["spin"] == {
            "package": "acme-state",
            "class": "FakeStateProvider",
        }
        assert described["viewers"]["fancy"] == {
            "package": "acme-viewer",
            "class": "FakeViewerProvider",
        }
        assert described["extractors"]["Texture2D"]["package"] == "built-in"

    def test_empty_groups(self, monkeypatch: Any) -> None:
        patch_eps(monkeypatch, {})

        described = describe_extensions()

        assert described["exporters"] == {}
        assert described["commands"] == {}
        assert described["scene_states"] == {}
        assert "Mesh" in described["extractors"]


class TestCaching:
    def test_load_once_and_reset(self, monkeypatch: Any) -> None:
        calls: list[str] = []
        groups = {extensions.EXPORTERS_GROUP: [FakeEP("obj", FakeObjExporter)]}

        def counting_entry_points(group: str | None = None) -> Any:
            calls.append(group or "")
            return groups.get(group, []) if group else {}

        monkeypatch.setattr("importlib.metadata.entry_points", counting_entry_points)

        first = load_exporters()
        assert load_exporters() is first
        assert calls == [extensions.EXPORTERS_GROUP]

        reset_extension_cache()

        second = load_exporters()
        assert second is not first
        assert calls == [extensions.EXPORTERS_GROUP] * 2

    def test_reset_clears_all_groups(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {
                extensions.COMMANDS_GROUP: [FakeEP("hello", hello_cmd)],
                extensions.STATE_PROVIDERS_GROUP: [FakeEP("spin", FakeStateProvider)],
            },
        )
        assert load_plugin_commands()
        assert load_state_providers()

        reset_extension_cache()
        patch_eps(monkeypatch, {})

        assert load_plugin_commands() == []
        assert load_state_providers() == {}
        assert load_exporters() == {}


class TestViewerLoading:
    def test_load_viewers(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {extensions.VIEWERS_GROUP: [FakeEP("fancy", FakeViewerProvider)]},
        )
        viewers = extensions.load_viewers()
        assert viewers["fancy"].name == "fancy"
        assert viewers["fancy"].render_html(b"", {}) == "<html></html>"

    def test_invalid_viewer_skipped(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {extensions.VIEWERS_GROUP: [FakeEP("bad", object())]},
        )
        assert extensions.load_viewers() == {}

    def test_rewog_entries_reported_builtin(self, monkeypatch: Any) -> None:
        patch_eps(
            monkeypatch,
            {
                extensions.VIEWERS_GROUP: [
                    FakeEP("default", FakeViewerProvider, "rewog")
                ]
            },
        )
        described = describe_extensions()
        assert described["viewers"]["fancy"]["package"] == "built-in"
