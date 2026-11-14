"""Example WOG Dump exporter plugin: GLB to OBJ through trimesh.

Install next to wog-dump and ``convert_format`` picks the ``obj``
format up automatically through the ``wog_dump.exporters`` entry
point group:

    pip install ./examples/plugin-export-obj
    wog-dump build-scene ak47 --format obj
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import trimesh

# Same convention as the built-in STL exporter: the GLB is in meters
_UNIT_SCALES = {"m": 1.0, "cm": 100.0, "mm": 1000.0}


class ObjExporter:
    """Writes the GLB scene as one merged Wavefront OBJ."""

    name = "obj"
    extensions = (".obj",)

    def convert(self, glb_path: Path, output_dir: Path, **options: Any) -> list[Path]:
        scene = trimesh.load_scene(glb_path)
        if not scene.geometry:
            raise ValueError(f"Scene has no geometry to export: {glb_path}")
        scale = _UNIT_SCALES.get(str(options.get("units", "m")), 1.0)
        if scale != 1.0:
            scene.apply_scale(scale)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{glb_path.stem}.obj"
        output_path.write_text(str(scene.export(file_type="obj")), encoding="utf-8")
        return [output_path]
