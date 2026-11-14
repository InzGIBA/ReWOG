"""Plugin API contract tests (PLUGIN_API_VERSION 1).

These pin the public surface third-party plugins compile against.
If a change here is intentional, bump PLUGIN_API_VERSION and document
the migration in docs/plugins.md.
"""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

from wog_dump.core import extensions
from wog_dump.core.export import (
    ExportOptions,
    fbx_exporter,
    stl_exporter,
    usd_exporter,
    usdz_exporter,
)
from wog_dump.core.extensions import (
    Exporter,
    StateProvider,
    ViewerProvider,
)
from wog_dump.core.scene_states import (
    SCENE_STATES,
    ExplodedState,
    FieldStripState,
    PartPlacement,
    PartsState,
)
from wog_dump.core.viewer import DefaultViewerProvider


class TestApiVersion:
    def test_version(self) -> None:
        assert extensions.PLUGIN_API_VERSION == 1

    def test_group_names(self) -> None:
        assert extensions.EXPORTERS_GROUP == "wog_dump.exporters"
        assert extensions.COMMANDS_GROUP == "wog_dump.commands"
        assert extensions.STATE_PROVIDERS_GROUP == "wog_dump.scene_states"
        assert extensions.VIEWERS_GROUP == "wog_dump.viewers"


class TestProtocolShapes:
    def test_exporter_convert_signature(self) -> None:
        params = inspect.signature(Exporter.convert).parameters
        assert list(params) == ["self", "glb_path", "output_dir", "options"]
        assert params["options"].kind == inspect.Parameter.VAR_KEYWORD

    def test_state_layout_signature(self) -> None:
        params = inspect.signature(extensions.StateLayout.__call__).parameters
        assert list(params) == ["self", "root", "unit_scale", "stats", "options"]
        assert params["options"].kind == inspect.Parameter.VAR_KEYWORD

    def test_viewer_render_signature(self) -> None:
        params = inspect.signature(ViewerProvider.render_html).parameters
        assert list(params) == [
            "self",
            "glb",
            "manifest",
            "linked",
            "glb_url",
            "vendor_base",
            "font_url_base",
        ]
        for name in ("linked", "glb_url", "vendor_base", "font_url_base"):
            assert params[name].kind == inspect.Parameter.KEYWORD_ONLY

    def test_part_placement_fields(self) -> None:
        placement = PartPlacement(name="bolt", offset=(0.1, 0.2, 0.3))
        assert placement.name == "bolt"
        assert placement.offset == (0.1, 0.2, 0.3)

    def test_export_options_fields(self) -> None:
        options = ExportOptions()
        assert options.units == "m"
        assert options.per_part is False


class TestBuiltinProviders:
    def test_exporters_satisfy_protocol(self) -> None:
        for exporter in (stl_exporter, fbx_exporter, usd_exporter, usdz_exporter):
            assert isinstance(exporter, Exporter)
        assert stl_exporter.name == "stl"
        assert usdz_exporter.extensions == (".usdz",)

    def test_states_satisfy_protocol(self) -> None:
        for provider in (FieldStripState(), ExplodedState(), PartsState()):
            assert isinstance(provider, StateProvider)
        assert {p.name for p in (FieldStripState(), ExplodedState(), PartsState())} == (
            SCENE_STATES - {"assembled"}
        )

    def test_default_viewer_satisfies_protocol(self) -> None:
        assert isinstance(DefaultViewerProvider(), ViewerProvider)
        assert DefaultViewerProvider.name == "default"


class TestPublicHelpers:
    def test_transforms_importable(self) -> None:
        from wog_dump.core.transforms import (
            compose_matrix,
            identity_matrix,
            invert_matrix,
            multiply_matrix,
            transform_point,
        )

        for fn in (
            compose_matrix,
            identity_matrix,
            invert_matrix,
            multiply_matrix,
            transform_point,
        ):
            assert callable(fn)

    def test_paths_importable(self) -> None:
        from wog_dump.core.paths import (
            contained_path,
            is_safe_weapon_name,
            safe_object_name,
        )

        assert is_safe_weapon_name("ak47")
        assert callable(contained_path)
        assert callable(safe_object_name)

    def test_state_helpers(self) -> None:
        from wog_dump.core.scene_states import (
            apply_plugin_state,
            available_states,
            normalize_part_name,
        )

        assert normalize_part_name("_Barrel") == "barrel"
        assert "assembled" in available_states()
        assert callable(apply_plugin_state)

    def test_catalog_schema(self) -> None:
        from wog_dump.core.catalog import CATALOG_SCHEMA, build_catalog

        assert isinstance(CATALOG_SCHEMA, str)
        assert callable(build_catalog)

    def test_viewer_entry_signature(self) -> None:
        from wog_dump.core.viewer import render_viewer_html

        params = inspect.signature(render_viewer_html).parameters
        assert list(params) == [
            "glb",
            "manifest",
            "linked",
            "glb_url",
            "vendor_base",
            "font_url_base",
        ]


class TestBuiltinEntryPoints:
    def test_pyproject_registers_builtins(self) -> None:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        groups = data["project"]["entry-points"]
        assert set(groups["wog_dump.exporters"]) == {"stl", "fbx", "usd", "usdz"}
        assert set(groups["wog_dump.scene_states"]) == {
            "field-strip",
            "exploded",
            "parts",
        }
        assert set(groups["wog_dump.viewers"]) == {"default"}
