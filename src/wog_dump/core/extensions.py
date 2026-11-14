"""Plugin loading for the WOG Dump extension groups.

Five entry-point groups extend the tool:

- ``wog_dump.extractors``: per-Unity-type asset extractors (loaded by
  ``core.unpack.get_extractors``, kept there for historical reasons).
- ``wog_dump.exporters``: GLB format converters (``Exporter``).
- ``wog_dump.commands``: Click commands added to the CLI group.
- ``wog_dump.scene_states``: scene-state layout providers
  (``StateProvider``).
- ``wog_dump.viewers``: HTML viewer providers (``ViewerProvider``).

The built-in exporters, scene states and the default viewer register
through the same groups from the ``rewog`` distribution itself, so
every extension flows through one mechanism; entries shipped by rewog
are reported as ``built-in``.

All loaders share the extractor loader's guarantees: an entry point
may target a class (instantiated with no arguments) or a ready
instance; import, instantiation and validation failures log a warning
and never break the pipeline; a plugin overriding a built-in wins with
a warning naming the distributing package. Results are cached per
process; ``reset_extension_cache`` clears them (tests).

``PLUGIN_API_VERSION`` is the contract version plugins compile
against; it changes only when a protocol shape breaks (see
docs/plugins.md and tests/unit/test_plugin_api_contract.py).

This module is import-safe and side-effect-free: heavy or cyclic
dependencies (``core.export``, ``core.unpack``) are imported inside the
loaders, and the scene tree types are TYPE_CHECKING-only so the
dependency direction scene -> scene_states -> extensions stays one-way.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import click

from ..utils.logging import get_logger

if TYPE_CHECKING:
    from .scene import _GameObjectInfo
    from .scene_states import PartPlacement

__all__ = [
    "COMMANDS_GROUP",
    "EXPORTERS_GROUP",
    "PLUGIN_API_VERSION",
    "STATE_PROVIDERS_GROUP",
    "VIEWERS_GROUP",
    "Exporter",
    "StateLayout",
    "StateProvider",
    "ViewerProvider",
    "describe_extensions",
    "load_exporters",
    "load_plugin_commands",
    "load_state_providers",
    "load_viewers",
    "reset_extension_cache",
]

PLUGIN_API_VERSION = 1

EXPORTERS_GROUP = "wog_dump.exporters"
COMMANDS_GROUP = "wog_dump.commands"
STATE_PROVIDERS_GROUP = "wog_dump.scene_states"
VIEWERS_GROUP = "wog_dump.viewers"


@runtime_checkable
class Exporter(Protocol):
    """Contract for ``wog_dump.exporters`` plugins.

    ``name`` is the format key passed to ``convert_format``;
    ``extensions`` lists the file suffixes the format produces.
    ``convert`` receives the built GLB plus the ``ExportOptions``
    fields as keyword options (``units``, ``per_part``) and returns
    the written files.
    """

    name: str
    extensions: tuple[str, ...]

    def convert(
        self, glb_path: Path, output_dir: Path, **options: Any
    ) -> list[Path]: ...


class StateLayout(Protocol):
    """Layout callable contract, derived from the ``core.scene_states``
    state functions (``apply_field_strip`` and friends).

    ``root`` is the scene tree root (``scene._GameObjectInfo``),
    ``unit_scale`` converts Unity units to glTF meters and ``stats`` is
    the scene stats object whose ``skipped`` dict records skipped
    parts. State-specific parameters (``explode_distance``,
    ``part_order``, ``mesh_bounds_fn``, ...) arrive as keyword
    ``options``. The callable rewrites node translations in Unity space
    and returns the applied placements in glTF meters.
    """

    def __call__(
        self,
        root: _GameObjectInfo,
        unit_scale: float,
        stats: Any,
        **options: Any,
    ) -> list[PartPlacement]: ...


@runtime_checkable
class StateProvider(Protocol):
    """Contract for ``wog_dump.scene_states`` plugins.

    ``name`` is the scene-state key (like ``field-strip``); ``layout``
    may be a method or any callable attribute matching ``StateLayout``.
    """

    name: str
    layout: StateLayout


@runtime_checkable
class ViewerProvider(Protocol):
    """Contract for ``wog_dump.viewers`` plugins.

    ``name`` selects the provider (``build-scene --viewer-provider``);
    ``render_html`` mirrors ``core.viewer.render_viewer_html``: it
    receives the GLB bytes plus the scene manifest and returns one HTML
    document. ``linked=False`` must be self-contained (offline file).
    """

    name: str

    def render_html(
        self,
        glb: bytes,
        manifest: dict,
        *,
        linked: bool = False,
        glb_url: str | None = None,
        vendor_base: str = "./vendor",
        font_url_base: str | None = None,
    ) -> str: ...


# ----------------------------------------------------------------------
# Shared entry-point plumbing
# ----------------------------------------------------------------------


def _package_name(entry_point: Any) -> str:
    """Distributing package name of an entry point (best effort).

    Extensions shipped by the rewog distribution itself are reported
    as ``built-in`` so listings and override warnings treat them as
    the defaults they are.
    """
    try:
        metadata = getattr(entry_point.dist, "metadata", None) or {}
        name = str(metadata.get("Name", "plugin"))
    except Exception:
        return "plugin"
    return "built-in" if name.lower() == "rewog" else name


def _loaded_plugins(group: str, logger: Any) -> Iterator[tuple[Any, Any, str]]:
    """Yield (entry point, instance, package) for one entry-point group.

    Same contract as the extractor loader: class targets are called
    with no arguments, instance targets pass through, and any failure
    only logs a warning.
    """
    from importlib.metadata import entry_points

    try:
        selected = list(entry_points(group=group))
    except Exception as e:
        logger.warning(f"Could not read entry points for {group}: {e}")
        return
    for entry_point in selected:
        try:
            loaded = entry_point.load()
            instance = loaded() if isinstance(loaded, type) else loaded
        except Exception as e:
            logger.warning(f"Plugin {entry_point.name!r} failed to load: {e}")
            continue
        yield entry_point, instance, _package_name(entry_point)


# ----------------------------------------------------------------------
# Exporters (wog_dump.exporters)
# ----------------------------------------------------------------------

_LOADED_EXPORTERS: dict[str, Exporter] | None = None
_LOADED_COMMANDS: list[click.Command] | None = None
_LOADED_STATE_PROVIDERS: dict[str, StateProvider] | None = None
_LOADED_VIEWERS: dict[str, ViewerProvider] | None = None
_EXPORTER_META: dict[str, dict[str, Any]] = {}
_COMMAND_META: dict[str, dict[str, Any]] = {}
_STATE_PROVIDER_META: dict[str, dict[str, Any]] = {}
_VIEWER_META: dict[str, dict[str, Any]] = {}


def load_exporters(logger: Any | None = None) -> dict[str, Exporter]:
    """Plugin exporters by format key, cached per process.

    A plugin whose ``name`` matches a built-in format overrides it;
    the warning naming the package is emitted here (once, at load).
    """
    global _LOADED_EXPORTERS, _EXPORTER_META
    if _LOADED_EXPORTERS is None:
        logger = logger or get_logger()
        from .export import EXPORT_FORMATS

        plugins: dict[str, Exporter] = {}
        meta: dict[str, dict[str, Any]] = {}
        for entry_point, instance, package in _loaded_plugins(EXPORTERS_GROUP, logger):
            try:
                name = getattr(instance, "name", None)
                suffixes = getattr(instance, "extensions", None)
                if not isinstance(instance, Exporter):
                    logger.warning(
                        f"Plugin {entry_point.name!r} is not an Exporter, skipped"
                    )
                    continue
                if not isinstance(name, str) or not name:
                    logger.warning(f"Plugin {entry_point.name!r} sets no name, skipped")
                    continue
                if not isinstance(suffixes, tuple) or not all(
                    isinstance(s, str) and s for s in suffixes
                ):
                    logger.warning(
                        f"Plugin {entry_point.name!r} has invalid extensions, skipped"
                    )
                    continue
                if name in EXPORT_FORMATS and package != "built-in":
                    logger.warning(
                        f"Plugin {package} overrides the built-in exporter for {name}"
                    )
                plugins[name] = instance
                meta[name] = {
                    "package": package,
                    "class": type(instance).__name__,
                    "extensions": list(suffixes),
                }
            except Exception as e:
                logger.warning(f"Plugin {entry_point.name!r} failed to load: {e}")
        _LOADED_EXPORTERS = plugins
        _EXPORTER_META = meta
    return _LOADED_EXPORTERS


# ----------------------------------------------------------------------
# CLI commands (wog_dump.commands)
# ----------------------------------------------------------------------


def load_plugin_commands(logger: Any | None = None) -> list[click.Command]:
    """Plugin Click commands/groups, cached per process.

    The integrator adds these to the top-level CLI group. A command
    without a name falls back to its entry-point name so
    ``Group.add_command`` always has a key.
    """
    global _LOADED_COMMANDS, _COMMAND_META
    if _LOADED_COMMANDS is None:
        logger = logger or get_logger()
        commands: list[click.Command] = []
        meta: dict[str, dict[str, Any]] = {}
        for entry_point, instance, package in _loaded_plugins(COMMANDS_GROUP, logger):
            try:
                if not isinstance(instance, click.Command):
                    logger.warning(
                        f"Plugin {entry_point.name!r} is not a Click command, skipped"
                    )
                    continue
                if not instance.name:
                    instance.name = entry_point.name
                commands.append(instance)
                meta[instance.name] = {
                    "package": package,
                    "class": type(instance).__name__,
                }
            except Exception as e:
                logger.warning(f"Plugin {entry_point.name!r} failed to load: {e}")
        _LOADED_COMMANDS = commands
        _COMMAND_META = meta
    return _LOADED_COMMANDS


# ----------------------------------------------------------------------
# Scene-state providers (wog_dump.scene_states)
# ----------------------------------------------------------------------


def load_state_providers(logger: Any | None = None) -> dict[str, StateProvider]:
    """Plugin scene-state providers by state name, cached per process.

    Lookup/merging with the built-in ``SCENE_STATES`` is wired by
    ``core.scene_states``; this loader only validates and registers.
    """
    global _LOADED_STATE_PROVIDERS, _STATE_PROVIDER_META
    if _LOADED_STATE_PROVIDERS is None:
        logger = logger or get_logger()
        providers: dict[str, StateProvider] = {}
        meta: dict[str, dict[str, Any]] = {}
        for entry_point, instance, package in _loaded_plugins(
            STATE_PROVIDERS_GROUP, logger
        ):
            try:
                name = getattr(instance, "name", None)
                layout = getattr(instance, "layout", None)
                if not isinstance(instance, StateProvider):
                    logger.warning(
                        f"Plugin {entry_point.name!r} is not a StateProvider, skipped"
                    )
                    continue
                if not isinstance(name, str) or not name:
                    logger.warning(f"Plugin {entry_point.name!r} sets no name, skipped")
                    continue
                if not callable(layout):
                    logger.warning(
                        f"Plugin {entry_point.name!r} has no callable layout, skipped"
                    )
                    continue
                providers[name] = instance
                meta[name] = {
                    "package": package,
                    "class": type(instance).__name__,
                }
            except Exception as e:
                logger.warning(f"Plugin {entry_point.name!r} failed to load: {e}")
        _LOADED_STATE_PROVIDERS = providers
        _STATE_PROVIDER_META = meta
    return _LOADED_STATE_PROVIDERS


# ----------------------------------------------------------------------
# Viewer providers (wog_dump.viewers)
# ----------------------------------------------------------------------


def load_viewers(logger: Any | None = None) -> dict[str, ViewerProvider]:
    """Plugin viewer providers by name, cached per process."""
    global _LOADED_VIEWERS, _VIEWER_META
    if _LOADED_VIEWERS is None:
        logger = logger or get_logger()
        providers: dict[str, ViewerProvider] = {}
        meta: dict[str, dict[str, Any]] = {}
        for entry_point, instance, package in _loaded_plugins(VIEWERS_GROUP, logger):
            try:
                name = getattr(instance, "name", None)
                if not isinstance(instance, ViewerProvider):
                    logger.warning(
                        f"Plugin {entry_point.name!r} is not a ViewerProvider, skipped"
                    )
                    continue
                if not isinstance(name, str) or not name:
                    logger.warning(f"Plugin {entry_point.name!r} sets no name, skipped")
                    continue
                providers[name] = instance
                meta[name] = {
                    "package": package,
                    "class": type(instance).__name__,
                }
            except Exception as e:
                logger.warning(f"Plugin {entry_point.name!r} failed to load: {e}")
        _LOADED_VIEWERS = providers
        _VIEWER_META = meta
    return _LOADED_VIEWERS


# ----------------------------------------------------------------------
# Cache control and summary
# ----------------------------------------------------------------------


def reset_extension_cache() -> None:
    """Drop every cached group so the next load re-reads entry points."""
    global _LOADED_EXPORTERS, _LOADED_COMMANDS, _LOADED_STATE_PROVIDERS
    global _LOADED_VIEWERS
    global _EXPORTER_META, _COMMAND_META, _STATE_PROVIDER_META, _VIEWER_META
    _LOADED_EXPORTERS = None
    _LOADED_COMMANDS = None
    _LOADED_STATE_PROVIDERS = None
    _LOADED_VIEWERS = None
    _EXPORTER_META = {}
    _COMMAND_META = {}
    _STATE_PROVIDER_META = {}
    _VIEWER_META = {}


def describe_extensions() -> dict[str, Any]:
    """Summarize all five extension groups.

    Each group maps registered keys to ``{"package", "class"}`` records
    (exporters also carry ``extensions``); extractors come from the
    unpacker registry, the other groups from this module's loaders.
    """
    from .unpack import get_extractors

    load_exporters()
    load_plugin_commands()
    load_state_providers()
    load_viewers()
    return {
        "extractors": {
            obj_type: {
                "package": extractor.plugin_package,
                "class": type(extractor).__name__,
            }
            for obj_type, extractor in get_extractors().items()
        },
        "exporters": dict(_EXPORTER_META),
        "commands": dict(_COMMAND_META),
        "scene_states": dict(_STATE_PROVIDER_META),
        "viewers": dict(_VIEWER_META),
    }
