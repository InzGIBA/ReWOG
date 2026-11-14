# Writing WOG Dump plugins

ReWOG is a stable core plus plugins, extending through five entry-point
groups. A pip package declares its plugins in `pyproject.toml`, installs
into the same environment as wog-dump, and the tool picks them up
automatically — no configuration. The built-in exporters, scene states
and the default viewer register through the same groups from the rewog
distribution itself (reported as `built-in`).

| Group                    | Extends                          | Loader                                    |
| ------------------------ | -------------------------------- | ----------------------------------------- |
| `wog_dump.extractors`    | Per-Unity-type asset extractors  | `wog_dump.core.unpack.get_extractors`     |
| `wog_dump.exporters`     | GLB export formats               | `wog_dump.core.extensions.load_exporters` |
| `wog_dump.commands`      | CLI commands                     | `wog_dump.core.extensions.load_plugin_commands` |
| `wog_dump.scene_states`  | Scene-state layouts              | `wog_dump.core.extensions.load_state_providers` |
| `wog_dump.viewers`       | HTML viewer providers            | `wog_dump.core.extensions.load_viewers`   |

**Plugin API version: 1** (`wog_dump.core.extensions.PLUGIN_API_VERSION`).
The public contract is pinned by
`tests/unit/test_plugin_api_contract.py`; a breaking change bumps the
version and documents the migration here. Plugins depend on `rewog`
and import only its public API (`wog_dump.core.*`, `wog_dump.utils.*`,
`wog_dump.cli.errors`); the core never imports plugins.

## Guarantees (all groups)

- The entry point may point to a **class** (instantiated with no
  arguments) or to a **ready instance**.
- A plugin that fails to import, instantiate or validate logs a
  warning and is skipped — it **never breaks the pipeline**.
- Overriding a built-in (extractor type or export format) is the
  supported way to replace built-in behavior: the plugin wins and a
  warning names the overriding package.
- Results are cached per process (entry points are read once).
  `wog_dump.core.extensions.reset_extension_cache()` clears the cache
  (used by tests).

**Security note:** plugins are arbitrary Python code running with your
permissions. Install packages you trust, same as any other Python
extension (pytest plugins, for example).

## Extractor plugins (`wog_dump.extractors`)

Per-type Unity object extractors, picked up by the unpacker.

```
wog-plugin-sprite/
├── pyproject.toml
└── src/
    └── wog_plugin_sprite/
        └── __init__.py
```

pyproject.toml:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "wog-plugin-sprite"
version = "0.1.0"
requires-python = ">=3.12"

[project.entry-points."wog_dump.extractors"]
sprite = "wog_plugin_sprite:SpriteExtractor"

[tool.hatch.build.targets.wheel]
packages = ["src/wog_plugin_sprite"]
```

The extractor subclasses `wog_dump.core.unpack.AssetExtractor`, sets
`obj_type` to the Unity type name and implements `_extract_data`:

```python
from pathlib import Path
from typing import Any

from wog_dump.core.paths import contained_path
from wog_dump.core.unpack import AssetExtractor


class SpriteExtractor(AssetExtractor):
    obj_type = "Sprite"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        output_path = contained_path(output_dir, f"{data.m_Name}.json")
        output_path.write_text(f'{{"name": "{data.m_Name}"}}', encoding="utf-8")
        return output_path
```

Install and list it:

```bash
pip install ./wog-plugin-sprite
wog-dump unpack-assets --list-extractors   # your extractor is listed
```

Filenames come from untrusted bundles: build output paths only through
`wog_dump.core.paths.contained_path` (the base class validates
`m_Name` before your `_extract_data` runs).

Runnable example: `examples/plugin-sprite/`.

## Exporter plugins (`wog_dump.exporters`)

A plugin exporter converts the built GLB scene to another format.
`core.export.convert_format` dispatches any format that is not
built-in (`glb`, `stl`, `usd`, `usdz`, `fbx`) to a registered plugin;
a plugin may also override a built-in format (warning at load time).

The plugin object fulfills the `Exporter` protocol
(`wog_dump.core.extensions`):

- `name: str` — the format key (`"obj"`).
- `extensions: tuple[str, ...]` — file suffixes produced (`(".obj",)`).
- `convert(glb_path: Path, output_dir: Path, **options) -> list[Path]`
  — reads the GLB and returns the written files. `options` carries the
  `ExportOptions` fields: `units` (`"m"`, `"cm"`, `"mm"`) and
  `per_part` (`bool`).

```toml
[project.entry-points."wog_dump.exporters"]
obj = "wog_plugin_export_obj:ObjExporter"
```

```python
from pathlib import Path
from typing import Any

import trimesh


class ObjExporter:
    name = "obj"
    extensions = (".obj",)

    def convert(self, glb_path: Path, output_dir: Path, **options: Any) -> list[Path]:
        scene = trimesh.load_scene(glb_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{glb_path.stem}.obj"
        output_path.write_text(str(scene.export(file_type="obj")), encoding="utf-8")
        return [output_path]
```

Library usage (the GLB is always the source of truth, in meters):

```python
from wog_dump.core.export import ExportOptions, convert_format

convert_format(glb_path, "obj", output_dir, ExportOptions(units="mm"))
```

The CLI `--format` allowlist is wired to the same registry by the CLI
integrator. Declare heavy dependencies (like `trimesh`) in your own
package, not in wog-dump.

Runnable example: `examples/plugin-export-obj/`.

## CLI command plugins (`wog_dump.commands`)

A plugin ships a Click `Command` or `Group`; the CLI adds it to the
top-level `wog-dump` group.

```toml
[project.entry-points."wog_dump.commands"]
hello = "wog_plugin_hello:hello"
```

```python
import click


@click.command("hello")
@click.option("--name", default="world", help="Who to greet")
def hello(name: str) -> None:
    """Say hello from a plugin."""
    click.echo(f"Hello, {name}!")
```

```bash
pip install ./wog-plugin-hello
wog-dump hello --name gunsmith
```

Rules:

- The target must be a `click.Command` (or `click.Group`) instance or
  a class instantiable with no arguments; anything else is skipped
  with a warning.
- A command without a name falls back to its entry-point name.
- `load_plugin_commands() -> list[click.Command]` is what the CLI
  calls; it never raises on a broken plugin.

## Scene-state plugins (`wog_dump.scene_states`)

Scene states rearrange the weapon's parts before scene emission
(built-ins: `assembled`, `field-strip`, `exploded`, `parts`). A plugin
provider adds a new state through the `StateProvider` protocol:

- `name: str` — the state key (used like `--state <name>`).
- `layout` — a callable matching the built-in state functions:

```python
def layout(
    root,          # scene._GameObjectInfo tree root
    unit_scale: float,   # Unity units -> glTF meters
    stats,         # scene stats; record skips in stats.skipped
    **options,     # state-specific parameters
) -> list[PartPlacement]: ...
```

The callable rewrites node translations **in Unity space** (the rest
of the pipeline handles coordinate conversion) and returns
`PartPlacement(name, offset)` records with the applied displacement
in **glTF meters** (Z mirrored), for the scene manifest.

```toml
[project.entry-points."wog_dump.scene_states"]
lift = "wog_plugin_lift:LiftProvider"
```

```python
from typing import Any

from wog_dump.core.scene_states import PartPlacement


class LiftProvider:
    """Raise every top-level mesh part by ``height`` meters."""

    name = "lift"

    def layout(
        self, root: Any, unit_scale: float, stats: Any, **options: Any
    ) -> list[PartPlacement]:
        height = float(options.get("height", 0.1))
        placements = []
        for node in root.children:
            if node.mesh is None:
                continue
            x, y, z = node.translation
            node.translation = (x, y + height / unit_scale, z)
            placements.append(
                PartPlacement(name=node.name, offset=(0.0, height, 0.0))
            )
        return placements
```

`load_state_providers() -> dict[str, StateProvider]` validates and
registers providers; `core.scene_states` wires the lookup and the
merge with `SCENE_STATES`.

## Viewer plugins (`wog_dump.viewers`)

A viewer provider turns a built GLB plus its `scene.json` manifest into
one HTML document. The built-in three.js viewer registers as `default`;
alternative viewers (different engines, annotation styles, embed
targets) register under other names and are selected with
`build-scene --viewer-provider <name>`.

```toml
[project.entry-points."wog_dump.viewers"]
minimal = "wog_plugin_minimal:MinimalViewer"
```

```python
class MinimalViewer:
    name = "minimal"

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
        # linked=False must produce a self-contained offline document
        ...
```

The contract mirrors `wog_dump.core.viewer.render_viewer_html`; the
manifest carries `viewer_states` (per-state part translations) and
`annotations` when the scene was built with a viewer in mind.

## Inspecting installed extensions

The `plugins` command lists everything registered across the four
groups (built-in extractors are hidden):

```bash
wog-dump plugins          # rich tables per group
wog-dump plugins --json   # machine-readable summary
```

The JSON shape comes from
`wog_dump.core.extensions.describe_extensions()`:

```python
{
    "extractors": {"Sprite": {"package": "wog-plugin-sprite", "class": "SpriteExtractor"}, ...},
    "exporters": {"obj": {"package": "wog-plugin-export-obj", "class": "ObjExporter", "extensions": [".obj"]}},
    "commands": {"hello": {"package": "wog-plugin-hello", "class": "Command"}},
    "scene_states": {"lift": {"package": "wog-plugin-lift", "class": "LiftProvider"}},
}
```

Built-in extractors report `"package": "built-in"`.

## Quick start: scaffold a plugin

`scripts/new_plugin.py` generates a ready-to-install package (pyproject
with the right entry point, module skeleton, importable tests) for any
of the four groups:

```bash
python scripts/new_plugin.py my-format --type exporter
python scripts/new_plugin.py my-state --type state
python scripts/new_plugin.py my-cmd --type command
python scripts/new_plugin.py my-viewer --type viewer
python scripts/new_plugin.py MyType --type extractor   # names are kebab-case

uv pip install -e plugins/wog-plugin-my-format
python -m pytest plugins/wog-plugin-my-format/tests -p no:cacheprovider
wog-dump plugins
```

## Official plugins

Feature plugins shipped in `plugins/` (install the ones you need):

| Package | Group | What it adds |
| --- | --- | --- |
| `wog-plugin-web` | commands | `serve`: local web UI with a persistent job queue (fastapi/uvicorn) |
| `wog-plugin-render` | commands | `render`: turntable previews and videos through headless Blender |
| `wog-plugin-print` | commands | `print-export`: repair, orientation, bed splitting, estimates (trimesh) |
| `wog-plugin-mitm` | commands | `import-keys`: keys/identity from mitmproxy/Fiddler captures |
| `wog-plugin-3mf` | exporters | `--format 3mf`: print-ready 3MF (mm, per-part objects) |
| `wog-plugin-gltf` | exporters | `--format gltf`: separate `.gltf` + `.bin` + `textures/` for web pipelines |
| `wog-plugin-showcase` | scene_states | `--state showcase`: circular carousel layout, bottoms on the ground |
| `wog-plugin-report` | commands | `scene-report`: Markdown/JSON report of a built scene |

```bash
uv pip install -e plugins/wog-plugin-3mf
wog-dump build-scene ak47 --format 3mf
```

Minimal teaching examples live in `examples/` (sprite extractor, OBJ
exporter).
