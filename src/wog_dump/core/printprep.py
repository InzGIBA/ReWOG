"""3D-print preparation: diagnostics, repair and merged layout.

Consumes the built scene GLB (the exporter reads the same file), splits
it into per-part meshes, repairs and checks each part with trimesh and
writes slicer-ready binary STLs in millimeters at true scale.

Status per part:

- ``ok``      already watertight
- ``fixed``   repaired (vertex merge, degenerate/duplicate faces, hole
  filling) and watertight afterwards
- ``warnings`` still not watertight after repair; the part prints with
  the holes it has
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .export import GlbScene, parse_glb, write_stl
from .paths import is_safe_weapon_name

__all__ = ["PRINT_SCHEMA", "PartReport", "prepare_print"]


PRINT_SCHEMA = "wog-dump.print/1"


@dataclass
class PartReport:
    """One part's print readiness."""

    name: str
    status: str = "ok"  # "ok", "fixed" or "warnings"
    triangles: int = 0
    watertight: bool = False
    size_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    notes: list[str] = field(default_factory=list)


def _load_trimesh() -> Any:
    import trimesh

    return trimesh


def _repair(mesh: Any, report: PartReport) -> None:
    """Apply trimesh's cheap repairs once, recording what changed."""
    from trimesh import repair

    before_faces = len(mesh.faces)
    before_vertices = len(mesh.vertices)

    mesh.remove_infinite_values()
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    repair.fix_normals(mesh, multibody=True)
    repair.fill_holes(mesh)
    mesh.remove_unreferenced_vertices()

    if len(mesh.faces) != before_faces or len(mesh.vertices) != before_vertices:
        report.notes.append(
            f"geometry changed: {before_faces}->{len(mesh.faces)} faces, "
            f"{before_vertices}->{len(mesh.vertices)} vertices"
        )


def _part_meshes(
    scene: GlbScene, wanted: set[str] | None, unit_scale: float
) -> list[tuple[str, Any]]:
    """World-space trimesh objects per meshed top-level node.

    Nested mesh nodes travel with their parent (the parent's world
    matrix already contains them through the transform chain... they do
    not: each GLB node carries its own mesh, so every meshed node is
    its own printable part).
    """
    trimesh = _load_trimesh()
    parts: list[tuple[str, Any]] = []
    for node in scene.nodes:
        if node.mesh is None:
            continue
        if wanted is not None and node.name not in wanted:
            continue
        mesh = scene.meshes[node.mesh]
        positions = node_mesh_vertices(node, scene, unit_scale)
        if not positions:
            continue
        faces = _globally_indexed_faces(mesh)
        trimesh_obj = trimesh.Trimesh(vertices=positions, faces=faces, process=False)
        parts.append((node.name, trimesh_obj))
    return parts


def node_mesh_vertices(
    node: Any, scene: GlbScene, unit_scale: float
) -> list[list[float]]:
    """Local vertices of the node's mesh transformed into its world."""
    from .export import _read_accessor  # parser helper, single source

    mesh = scene.meshes[node.mesh]
    out: list[list[float]] = []
    for primitive in mesh.primitives:
        out.extend(
            [
                [
                    primitive.positions[i] * unit_scale,
                    primitive.positions[i + 1] * unit_scale,
                    primitive.positions[i + 2] * unit_scale,
                ]
                for i in range(0, len(primitive.positions), 3)
            ]
        )
    del _read_accessor  # positions are already plain floats
    return out


def _globally_indexed_faces(mesh: Any) -> list[list[int]]:
    """Faces offset per primitive so multi-primitive meshes stay one body."""
    faces: list[list[int]] = []
    offset = 0
    for primitive in mesh.primitives:
        faces.extend(
            [int(c) + offset for c in primitive.indices[i : i + 3]]
            for i in range(0, len(primitive.indices), 3)
        )
        offset += len(primitive.positions) // 3
    return faces


def prepare_print(
    glb_path: Path,
    output_dir: Path,
    parts_filter: set[str] | None = None,
    merged: bool = False,
    spacing_mm: float = 5.0,
) -> dict:
    """Prepare slicer-ready STLs from one built scene.

    Writes ``print/<weapon>__<part>.stl`` (or ``print/<weapon>.stl``
    when ``merged``) plus ``print_report.json``; returns the report.
    """
    if not is_safe_weapon_name(glb_path.stem):
        raise ValueError(f"Invalid weapon name: {glb_path.stem!r}")

    scene = parse_glb(glb_path)
    # GLB units are meters; STL for slicers is millimeters
    unit_scale = 1000.0
    parts = _part_meshes(scene, parts_filter, unit_scale)
    if not parts and parts_filter is None:
        raise ValueError("No printable parts in the scene")

    reports: list[PartReport] = []
    repaired: list[tuple[str, Any]] = []
    for name, mesh in parts:
        report = PartReport(name=name, triangles=len(mesh.faces))
        _repair(mesh, report)
        report.triangles = len(mesh.faces)
        report.watertight = bool(mesh.is_watertight)
        bounds = mesh.bounds
        report.size_mm = (
            float(bounds[1][0] - bounds[0][0]),
            float(bounds[1][1] - bounds[0][1]),
            float(bounds[1][2] - bounds[0][2]),
        )
        if not report.watertight:
            import trimesh as _trimesh

            paired = _trimesh.grouping.group_rows(mesh.edges_sorted, require_count=2)
            holes = len(mesh.edges_sorted) - 2 * len(paired)
            report.notes.append(f"{holes} open edges remain")
            report.status = "warnings"
        elif report.notes:
            report.status = "fixed"
        else:
            report.status = "ok"
        reports.append(report)
        repaired.append((name, mesh))

    output_dir.mkdir(parents=True, exist_ok=True)
    from .export import ExportError

    if merged:
        placed = _grid_place([mesh for _, mesh in repaired], spacing_mm)
        combined = placed[0]
        for other in placed[1:]:
            combined = _concat(combined, other)
        write_stl(_triangles_of(combined), output_dir / f"{glb_path.stem}.stl")
    else:
        used: set[str] = set()
        for name, mesh in repaired:
            base = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
            stem = base
            suffix = 1
            while stem in used:
                suffix += 1
                stem = f"{base}_{suffix}"
            used.add(stem)
            write_stl(_triangles_of(mesh), output_dir / f"{glb_path.stem}__{stem}.stl")

    report_doc = {
        "schema": PRINT_SCHEMA,
        "weapon": glb_path.stem,
        "units": "mm",
        "real_scale": True,
        "merged": merged,
        "parts": [
            {
                "name": r.name,
                "status": r.status,
                "triangles": r.triangles,
                "watertight": r.watertight,
                "size_mm": [round(v, 2) for v in r.size_mm],
                "notes": r.notes,
            }
            for r in reports
        ],
    }
    (output_dir / "print_report.json").write_text(
        json.dumps(report_doc, indent=2), encoding="utf-8"
    )
    return report_doc


def _triangles_of(mesh: Any) -> list[Any]:
    return [tuple(map(tuple, mesh.triangles[i])) for i in range(len(mesh.triangles))]


def _concat(a: Any, b: Any) -> Any:
    trimesh = _load_trimesh()
    return trimesh.util.concatenate([a, b])


def _grid_place(meshes: list[Any], spacing_mm: float) -> list[Any]:
    """Shift meshes in X so their bounding boxes never overlap."""
    placed: list[Any] = []
    cursor = 0.0
    for mesh in meshes:
        bounds = mesh.bounds
        width = float(bounds[1][0] - bounds[0][0])
        shift = cursor - float(bounds[0][0])
        mesh.apply_translation([shift, 0.0, 0.0])
        placed.append(mesh)
        cursor += width + spacing_mm
    return placed
