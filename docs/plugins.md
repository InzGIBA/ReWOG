# Writing an extractor plugin

ReWOG opens one extension point: per-type Unity object extractors.
A pip package registers a class in the ``wog_dump.extractors`` entry
point group and the unpacker picks it up automatically.

## Minimal plugin

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

The extractor subclasses ``wog_dump.core.unpack.AssetExtractor``, sets
``obj_type`` to the Unity type name and implements ``_extract_data``:

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

Install it into the same environment as wog-dump:

```bash
pip install ./wog-plugin-sprite
wog-dump unpack-assets --list-extractors   # your extractor is listed
```

## Guarantees and rules

- The entry point may point to a class (instantiated with no arguments)
  or to a ready instance.
- ``obj_type`` conflict with a built-in extractor: the plugin wins, a
  warning names the overriding package. Overriding is the supported way
  to replace built-in behavior.
- A plugin that fails to import, instantiate or validate logs a warning
  and never breaks an unpack.
- Filenames come from untrusted bundles: build output paths only
  through ``wog_dump.core.paths.contained_path`` (the base class
  validates ``m_Name`` before your ``_extract_data`` runs).
- Plugins are arbitrary code. Install packages you trust, same as any
  other Python extension (pytest plugins, for example).

A runnable example lives in ``examples/plugin-sprite/``.
