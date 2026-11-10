"""Example WOG Dump extractor plugin: Sprite metadata dumps.

Install next to wog-dump and the unpacker picks the Sprite extractor
up automatically through the ``wog_dump.extractors`` entry point group:

    pip install ./examples/plugin-sprite
    wog-dump unpack-assets --list-extractors
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wog_dump.core.paths import contained_path
from wog_dump.core.unpack import AssetExtractor


class SpriteExtractor(AssetExtractor):
    """Dumps Sprite metadata as JSON (the texture needs a real importer)."""

    obj_type = "Sprite"

    def _extract_data(self, data: Any, output_dir: Path) -> Path | None:
        rect = getattr(data, "m_Rect", None)

        def axis(value: Any, name: str) -> float:
            return float(getattr(value, name, 0.0))

        payload = {
            "name": data.m_Name,
            "rect": (
                {
                    "x": axis(rect, "x"),
                    "y": axis(rect, "y"),
                    "width": axis(rect, "width"),
                    "height": axis(rect, "height"),
                }
                if rect is not None
                else None
            ),
            "pixels_to_units": float(getattr(data, "m_PixelsToUnits", 0.0)),
        }
        output_path = contained_path(output_dir, f"{data.m_Name}.sprite.json")
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return output_path
