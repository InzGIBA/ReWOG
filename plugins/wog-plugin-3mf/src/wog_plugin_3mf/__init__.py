"""WOG Dump exporter plugin: GLB to 3MF (3D Manufacturing Format).

Install next to wog-dump and ``convert_format`` picks the ``3mf``
format up automatically through the ``wog_dump.exporters`` entry
point group:

    pip install ./plugins/wog-plugin-3mf
    wog-dump build-scene ak47 --format 3mf

Pure-Python 3MF core-spec writer (zipfile + xml.etree); geometry is
loaded through trimesh and baked into world space in millimeters.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import trimesh

__all__ = ["ThreeMFExporter"]

_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MODEL_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"

# GLB geometry is in meters; the model below declares unit="millimeter".
_MM_PER_UNIT = {"m": 1000.0, "cm": 10.0, "mm": 1.0}

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'

# XML 1.0 forbids most control characters even when escaped.
_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _fmt(value: float) -> str:
    """Compact fixed-point float: no exponent, no trailing zeros."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _clean_name(name: str) -> str:
    cleaned = _ILLEGAL_XML_CHARS.sub("", name).strip()
    return cleaned or "part"


def _serialize(root: ET.Element) -> bytes:
    return (_XML_DECL + ET.tostring(root, encoding="unicode")).encode("utf-8")


def _content_types_xml() -> bytes:
    types = ET.Element("Types", {"xmlns": _CONTENT_TYPES_NS})
    ET.SubElement(
        types,
        "Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    ET.SubElement(
        types,
        "Default",
        {
            "Extension": "model",
            "ContentType": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
        },
    )
    return _serialize(types)


def _rels_xml() -> bytes:
    relationships = ET.Element("Relationships", {"xmlns": _RELATIONSHIPS_NS})
    ET.SubElement(
        relationships,
        "Relationship",
        {"Id": "rel0", "Type": _MODEL_REL_TYPE, "Target": "/3D/3dmodel.model"},
    )
    return _serialize(relationships)


def _model_xml(parts: list[tuple[str, trimesh.Trimesh]]) -> bytes:
    model = ET.Element(
        "model", {"unit": "millimeter", "xml:lang": "en-US", "xmlns": _CORE_NS}
    )
    resources = ET.SubElement(model, "resources")
    build = ET.SubElement(model, "build")
    for object_id, (name, mesh) in enumerate(parts, start=1):
        obj = ET.SubElement(
            resources,
            "object",
            {"id": str(object_id), "type": "model", "name": _clean_name(name)},
        )
        mesh_el = ET.SubElement(obj, "mesh")
        vertices = ET.SubElement(mesh_el, "vertices")
        for x, y, z in mesh.vertices:
            ET.SubElement(
                vertices, "vertex", {"x": _fmt(x), "y": _fmt(y), "z": _fmt(z)}
            )
        triangles = ET.SubElement(mesh_el, "triangles")
        for v1, v2, v3 in mesh.faces:
            ET.SubElement(
                triangles,
                "triangle",
                {"v1": str(int(v1)), "v2": str(int(v2)), "v3": str(int(v3))},
            )
        ET.SubElement(build, "item", {"objectid": str(object_id)})
    return _serialize(model)


def _world_meshes(scene: trimesh.Scene) -> Iterator[tuple[str, trimesh.Trimesh]]:
    """Yield (part name, world-space mesh) per geometry node.

    Matches the built-in STL per-part split: one entry per mesh-bearing
    node under its own name, not per top-level ancestor (weapon GLBs
    nest every part under a single root node).
    """
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        mesh = scene.geometry[geometry_name].copy()
        mesh.apply_transform(transform)
        yield str(node), mesh


def _collect_parts(
    scene: trimesh.Scene, per_part: bool, merged_name: str
) -> list[tuple[str, trimesh.Trimesh]]:
    groups: dict[str, list[trimesh.Trimesh]] = {}
    for name, mesh in _world_meshes(scene):
        key = name if per_part else merged_name
        groups.setdefault(key, []).append(mesh)
    return [
        (name, meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes))
        for name, meshes in groups.items()
    ]


class ThreeMFExporter:
    """Writes the GLB scene as a 3MF package (zip + XML, millimeters)."""

    name = "3mf"
    extensions = (".3mf",)

    def convert(self, glb_path: Path, output_dir: Path, **options: Any) -> list[Path]:
        units = str(options.get("units", "m"))
        if units not in _MM_PER_UNIT:
            raise ValueError(f"Unsupported units {units!r}: expected 'm', 'cm' or 'mm'")
        per_part = bool(options.get("per_part", False))

        scene = trimesh.load(glb_path, force="scene")
        if not isinstance(scene, trimesh.Scene) or not scene.geometry:
            raise ValueError(f"Scene has no geometry to export: {glb_path}")

        parts = _collect_parts(scene, per_part, glb_path.stem)
        if not parts:
            raise ValueError(f"Scene has no geometry to export: {glb_path}")
        scale = _MM_PER_UNIT[units]
        if scale != 1.0:
            for _, mesh in parts:
                mesh.apply_scale(scale)

        # Build the package in memory so a failed export never leaves a
        # partial .3mf or a temp file behind.
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _content_types_xml())
            archive.writestr("_rels/.rels", _rels_xml())
            archive.writestr("3D/3dmodel.model", _model_xml(parts))

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{glb_path.stem}.3mf"
        output_path.write_bytes(package.getvalue())
        return [output_path]
