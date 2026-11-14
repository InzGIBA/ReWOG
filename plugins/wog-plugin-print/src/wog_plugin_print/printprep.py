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
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wog_dump.core.export import GlbScene, parse_glb, write_stl
from wog_dump.core.paths import is_safe_weapon_name

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

__all__ = ["PRINT_SCHEMA", "PartReport", "prepare_print"]


PRINT_SCHEMA = "wog-dump.print/1"

# Orientation: candidate bed normals come from the largest faces.
ORIENT_MAX_CANDIDATES = 24
ORIENT_PARALLEL_DOT = 0.999  # ~2.6 deg: treat nearer normals as one candidate
OVERHANG_ANGLE_DEG = 45.0
# Faces resting on the bed are support, not overhang; this tolerance
# (mm) decides "resting on the bed" after rotation round-off.
BED_CONTACT_TOL_MM = 1e-6

# Bed splitting.
BED_MAX_CHUNKS = 64
BED_FIT_TOL_MM = 1e-6

# Rough FDM heuristic (not a slicer): extrusion time scaled by an
# overhead factor plus a fixed per-layer perimeter/travel overhead.
ESTIMATE_OVERHEAD_FACTOR = 1.5
ESTIMATE_LAYER_HEIGHT_MM = 0.2
ESTIMATE_PERIMETER_S_PER_LAYER = 2.0
DEFAULT_DENSITY_G_CM3 = 1.24  # PLA
DEFAULT_FLOW_RATE_MM3_S = 5.0
DEFAULT_FILAMENT_PRICE_PER_KG = 25.0


@dataclass
class PartReport:
    """One part's print readiness."""

    name: str
    status: str = "ok"  # "ok", "fixed" or "warnings"
    triangles: int = 0
    watertight: bool = False
    size_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    notes: list[str] = field(default_factory=list)
    orientation: dict[str, Any] | None = None
    split: dict[str, Any] | None = None
    estimate: dict[str, Any] | None = None


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
    from wog_dump.core.export import _read_accessor  # parser helper, single source

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
    orient: bool = False,
    bed_size_mm: tuple[float, float, float] | None = None,
    estimate: bool = False,
    density_g_cm3: float = DEFAULT_DENSITY_G_CM3,
    flow_rate_mm3_s: float = DEFAULT_FLOW_RATE_MM3_S,
    filament_price_per_kg: float = DEFAULT_FILAMENT_PRICE_PER_KG,
) -> dict[str, Any]:
    """Prepare slicer-ready STLs from one built scene.

    Writes ``print/<weapon>__<part>.stl`` (or ``print/<weapon>.stl``
    when ``merged``) plus ``print_report.json``; returns the report.

    Optional stages (all off by default, report sections appear only
    when the matching argument is used):

    - ``orient``: lay each part's largest supporting face on the bed.
    - ``bed_size_mm``: split parts overhanging the bed in X/Y into
      ``<part>_chunkNN.stl`` files; Z overflow only warns.
    - ``estimate``: volume/mass/time/cost heuristic per part and totals.
    """
    if not is_safe_weapon_name(glb_path.stem):
        raise ValueError(f"Invalid weapon name: {glb_path.stem!r}")
    if bed_size_mm is not None and any(v <= 0 for v in bed_size_mm):
        raise ValueError(f"Bed size must be positive, got {bed_size_mm!r}")
    if estimate and (density_g_cm3 <= 0 or flow_rate_mm3_s <= 0):
        raise ValueError("Density and flow rate must be positive")
    if estimate and filament_price_per_kg < 0:
        raise ValueError("Filament price must not be negative")

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

    if orient:
        for index, (name, mesh) in enumerate(repaired):
            oriented, info = _orient_mesh(mesh)
            repaired[index] = (name, oriented)
            reports[index].orientation = info
            reports[index].size_mm = _extents(oriented)

    # Per-part export units: (part index, chunk meshes). Unsplit parts
    # travel as a single chunk so --merge lays out chunks the same way.
    units: list[tuple[int, list[Any]]] = []
    for index, (name, mesh) in enumerate(repaired):
        if bed_size_mm is None:
            units.append((index, [mesh]))
            continue
        chunks = _split_for_bed(mesh, bed_size_mm, name)
        z_overflow = float(mesh.extents[2]) > bed_size_mm[2] + BED_FIT_TOL_MM
        if z_overflow:
            reports[index].notes.append(
                f"Z height {float(mesh.extents[2]):.1f} mm exceeds bed Z "
                f"{bed_size_mm[2]:.1f} mm (not split)"
            )
        reports[index].split = {
            "was_split": len(chunks) > 1,
            "z_overflow": z_overflow,
            "chunks": [
                {"size_mm": [round(v, 2) for v in _extents(chunk)]} for chunk in chunks
            ],
        }
        units.append((index, chunks))

    raw_estimates: list[dict[str, float]] = []
    if estimate:
        raw_estimates = [
            _estimate_mesh(mesh, density_g_cm3, flow_rate_mm3_s, filament_price_per_kg)
            for _, mesh in repaired
        ]
        for report, raw in zip(reports, raw_estimates):
            report.estimate = _round_estimate(raw)

    output_dir.mkdir(parents=True, exist_ok=True)

    if merged:
        placed = _grid_place(
            [mesh for _, chunks in units for mesh in chunks], spacing_mm
        )
        combined = placed[0]
        for other in placed[1:]:
            combined = _concat(combined, other)
        write_stl(_triangles_of(combined), output_dir / f"{glb_path.stem}.stl")
    else:
        used: set[str] = set()
        for index, chunks in units:
            name = repaired[index][0]
            base = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
            stem = base
            suffix = 1
            while stem in used:
                suffix += 1
                stem = f"{base}_{suffix}"
            used.add(stem)
            split_info = reports[index].split
            for chunk_index, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    file_name = (
                        f"{glb_path.stem}__{stem}_chunk{chunk_index + 1:02d}.stl"
                    )
                else:
                    file_name = f"{glb_path.stem}__{stem}.stl"
                write_stl(_triangles_of(chunk), output_dir / file_name)
                if split_info is not None:
                    split_info["chunks"][chunk_index]["file"] = file_name

    report_doc: dict[str, Any] = {
        "schema": PRINT_SCHEMA,
        "weapon": glb_path.stem,
        "units": "mm",
        "real_scale": True,
        "merged": merged,
        "parts": [],
    }
    for r in reports:
        part_doc: dict[str, Any] = {
            "name": r.name,
            "status": r.status,
            "triangles": r.triangles,
            "watertight": r.watertight,
            "size_mm": [round(v, 2) for v in r.size_mm],
            "notes": r.notes,
        }
        if r.orientation is not None:
            part_doc["orientation"] = r.orientation
        if r.split is not None:
            part_doc["split"] = r.split
        if r.estimate is not None:
            part_doc["estimate"] = r.estimate
        report_doc["parts"].append(part_doc)
    if orient:
        report_doc["orientation"] = {
            "overhang_angle_deg": OVERHANG_ANGLE_DEG,
            "max_candidates": ORIENT_MAX_CANDIDATES,
        }
    if bed_size_mm is not None:
        report_doc["bed_size_mm"] = [round(v, 2) for v in bed_size_mm]
        report_doc["max_chunks_per_part"] = BED_MAX_CHUNKS
    if estimate:
        totals = {
            key: sum(raw[key] for raw in raw_estimates)
            for key in ("volume_cm3", "mass_g", "time_s", "cost")
        }
        report_doc["estimate"] = {
            "parameters": {
                "density_g_cm3": density_g_cm3,
                "flow_rate_mm3_s": flow_rate_mm3_s,
                "filament_price_per_kg": filament_price_per_kg,
            },
            "heuristic": {
                "overhead_factor": ESTIMATE_OVERHEAD_FACTOR,
                "layer_height_mm": ESTIMATE_LAYER_HEIGHT_MM,
                "perimeter_overhead_s_per_layer": ESTIMATE_PERIMETER_S_PER_LAYER,
                "formula": (
                    "time_s = volume_mm3 / flow_rate_mm3_s * overhead_factor"
                    " + height_mm / layer_height_mm"
                    " * perimeter_overhead_s_per_layer"
                ),
                "note": "rough FDM heuristic, not a slicer simulation",
            },
            "totals": _round_estimate(totals),
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


def _extents(mesh: Any) -> tuple[float, float, float]:
    bounds = mesh.bounds
    return (
        float(bounds[1][0] - bounds[0][0]),
        float(bounds[1][1] - bounds[0][1]),
        float(bounds[1][2] - bounds[0][2]),
    )


def _orient_mesh(mesh: Any) -> tuple[Any, dict[str, Any]]:
    """Rotate the largest supporting face onto the bed (normal -> -Z).

    Candidates are the part's face normals, largest faces first, with
    near-parallel normals deduplicated. Score = (overhang area, height),
    both minimized; the overhang proxy counts faces steeper than 45 deg
    facing down, excluding faces resting on the bed (they are support,
    not overhang). Ties keep the first candidate, so the choice is
    deterministic.
    """
    import numpy as np

    trimesh = _load_trimesh()
    info: dict[str, Any] = {
        "normal": [0.0, 0.0, -1.0],
        "rotation": np.eye(4).tolist(),
        "overhang_area_mm2": 0.0,
        "height_mm": _extents(mesh)[2],
        "candidates": 0,
    }
    if len(mesh.faces) == 0:
        return mesh, info

    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    # Area descending, then normal components: stable across runs even
    # when several faces share the same area.
    order = np.lexsort((normals[:, 2], normals[:, 1], normals[:, 0], -areas))

    candidates: list[NDArray[np.float64]] = []
    for face_index in order:
        if areas[face_index] <= 0.0:
            continue
        normal = normals[face_index]
        if any(
            float(np.dot(normal, kept)) > ORIENT_PARALLEL_DOT for kept in candidates
        ):
            continue
        candidates.append(normal)
        if len(candidates) >= ORIENT_MAX_CANDIDATES:
            break

    overhang_limit = -math.cos(math.radians(OVERHANG_ANGLE_DEG))
    best_key: tuple[float, float] | None = None
    best_matrix: NDArray[np.float64] | None = None
    best_normal: NDArray[np.float64] | None = None
    best_overhang = 0.0
    best_height = 0.0
    for normal in candidates:
        matrix = np.asarray(
            trimesh.geometry.align_vectors(normal, [0.0, 0.0, -1.0]),
            dtype=np.float64,
        )
        rotation = matrix[:3, :3]
        rotated = vertices @ rotation.T
        rotated_normals = normals @ rotation.T
        z_min = float(rotated[:, 2].min())
        height = float(rotated[:, 2].max()) - z_min
        centroid_z = rotated[faces].mean(axis=1)[:, 2]
        overhang_mask = (rotated_normals[:, 2] < overhang_limit) & (
            centroid_z > z_min + BED_CONTACT_TOL_MM
        )
        overhang = float(areas[overhang_mask].sum())
        key = (overhang, height)
        if best_key is None or key < best_key:
            best_key = key
            best_matrix = matrix
            best_normal = normal
            best_overhang = overhang
            best_height = height

    if best_matrix is None or best_normal is None:
        return mesh, info

    oriented = mesh.copy()
    oriented.apply_transform(best_matrix)
    z_min = float(oriented.bounds[0][2])
    oriented.apply_translation([0.0, 0.0, -z_min])
    info = {
        "normal": [round(float(v), 6) for v in best_normal],
        "rotation": [[round(float(v), 6) for v in row] for row in best_matrix],
        "overhang_area_mm2": round(best_overhang, 3),
        "height_mm": round(best_height, 3),
        "candidates": len(candidates),
    }
    return oriented, info


def _split_for_bed(
    mesh: Any, bed: tuple[float, float, float], part_name: str
) -> list[Any]:
    """Cut a mesh into bed-fitting chunks along X/Y, never Z.

    Each cut is perpendicular to the longest over-bed axis and peels off
    one bed-width slab; the remainder is cut again until everything
    fits. Depth-first order keeps chunk numbering spatially stable.
    """
    import numpy as np

    chunks: list[Any] = []
    pending: list[Any] = [mesh]
    while pending:
        current = pending.pop()
        extents = current.extents
        over = [float(extents[i]) - bed[i] for i in (0, 1)]
        if over[0] <= BED_FIT_TOL_MM and over[1] <= BED_FIT_TOL_MM:
            chunks.append(current)
            continue
        axis = 0 if over[0] >= over[1] else 1
        normal = np.zeros(3, dtype=np.float64)
        normal[axis] = 1.0
        origin = np.asarray(current.bounds[0], dtype=np.float64).copy()
        origin[axis] += bed[axis]
        low = _slice_half(current, -normal, origin)
        high = _slice_half(current, normal, origin)
        if low is None or high is None or len(low.faces) == 0 or len(high.faces) == 0:
            # Degenerate cut: keep the piece rather than loop forever.
            chunks.append(current)
            continue
        if len(chunks) + len(pending) + 2 > BED_MAX_CHUNKS:
            raise ValueError(
                f"Part {part_name!r} needs more than {BED_MAX_CHUNKS} chunks "
                f"for bed {bed[0]:g}x{bed[1]:g}x{bed[2]:g} mm; "
                "use a larger bed or split the part manually"
            )
        pending.append(high)
        pending.append(low)
    return chunks


def _slice_half(
    mesh: Any, normal: NDArray[np.float64], origin: NDArray[np.float64]
) -> Any | None:
    """Portion of ``mesh`` on the positive side of the plane, capped.

    Prefers trimesh's slicer; it needs scipy (and shapely for capping),
    which the print extra does not pull in, so a numpy-only fallback
    keeps splitting available without them.
    """
    trimesh = _load_trimesh()
    try:
        return trimesh.intersections.slice_mesh_plane(mesh, normal, origin, cap=True)
    except ImportError:
        return _slice_half_numpy(mesh, normal, origin)


def _slice_half_numpy(
    mesh: Any, normal: NDArray[np.float64], origin: NDArray[np.float64]
) -> Any | None:
    """Plane slice with capping, using only numpy.

    Clips every triangle against the half-space, then chains the cut
    segments into loops and ear-clips them into cap faces.
    """
    import numpy as np

    trimesh = _load_trimesh()
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return None
    unit = normal / np.linalg.norm(normal)
    distances = (vertices - origin) @ unit

    points: list[NDArray[np.float64]] = [vertices[i] for i in range(len(vertices))]
    new_faces: list[tuple[int, int, int]] = []
    segments: list[tuple[int, int]] = []
    # Adjacent faces compute the same cut point independently; dedupe by
    # rounded coordinates so the cap segments chain into closed loops.
    point_index: dict[tuple[float, float, float], int] = {}

    def point_key(point: NDArray[np.float64]) -> tuple[float, float, float]:
        return (
            round(float(point[0]), 6),
            round(float(point[1]), 6),
            round(float(point[2]), 6),
        )

    for index, vertex in enumerate(vertices):
        point_index[point_key(vertex)] = index

    def intersection(a: int, b: int) -> int:
        da, db = distances[a], distances[b]
        t = float(da / (da - db))
        point = vertices[a] + t * (vertices[b] - vertices[a])
        key = point_key(point)
        if key in point_index:
            return point_index[key]
        points.append(point)
        point_index[key] = len(points) - 1
        return len(points) - 1

    for face in faces:
        a, b, c = (int(i) for i in face)
        inside = [distances[i] >= 0.0 for i in (a, b, c)]
        if all(inside):
            new_faces.append((a, b, c))
            continue
        if not any(inside):
            continue
        # Sutherland-Hodgman clip of the triangle against the half-space.
        poly: list[int] = []
        cut_points: list[int] = []
        corners = (a, b, c)
        for k in range(3):
            cur, nxt = corners[k], corners[(k + 1) % 3]
            cur_in, nxt_in = distances[cur] >= 0.0, distances[nxt] >= 0.0
            if cur_in:
                poly.append(cur)
            if cur_in != nxt_in:
                point = intersection(cur, nxt)
                poly.append(point)
                cut_points.append(point)
        for k in range(len(poly) - 2):
            new_faces.append((poly[0], poly[k + 1], poly[k + 2]))
        if len(cut_points) == 2 and cut_points[0] != cut_points[1]:
            segments.append((cut_points[0], cut_points[1]))

    cap_faces = _cap_faces(points, segments, unit)
    all_faces = new_faces + cap_faces
    if not all_faces:
        return None
    all_vertices = np.asarray(points, dtype=np.float64)
    result = trimesh.Trimesh(
        vertices=all_vertices,
        faces=np.asarray(all_faces, dtype=np.int64),
        process=False,
    )
    result.merge_vertices()
    result.update_faces(result.nondegenerate_faces())
    result.remove_unreferenced_vertices()
    return result


def _cap_faces(
    points: list[NDArray[np.float64]],
    segments: list[tuple[int, int]],
    unit: NDArray[np.float64],
) -> list[tuple[int, int, int]]:
    """Triangulate the cut cross-section so the sliced half stays solid."""
    import numpy as np

    if not segments:
        return []
    # Undirected chaining: the cut-edge direction produced by clipping
    # depends on where the kept corner sits in the clipped polygon, so
    # orientation is recovered from the loop's signed area below.
    adjacency: dict[int, list[int]] = {}
    for start, end in segments:
        adjacency.setdefault(start, []).append(end)
        adjacency.setdefault(end, []).append(start)
    # Plane basis with u x v = unit; 2D signed area then tells winding.
    helper = np.eye(3)[int(np.argmin(np.abs(unit)))]
    u = np.cross(unit, helper)
    u /= np.linalg.norm(u)
    v = np.cross(unit, u)

    faces: list[tuple[int, int, int]] = []
    visited: set[int] = set()
    for start in list(adjacency):
        if start in visited or len(adjacency[start]) != 2:
            continue
        loop: list[int] = [start]
        visited.add(start)
        prev, node = start, adjacency[start][0]
        while node != start:
            neighbors = adjacency.get(node, [])
            if node in visited or len(neighbors) != 2:
                break  # open boundary (non-watertight input): leave uncapped
            visited.add(node)
            loop.append(node)
            prev, node = node, neighbors[0] if neighbors[0] != prev else neighbors[1]
        else:
            if len(loop) < 3:
                continue
            coords = np.asarray([points[i] for i in loop], dtype=np.float64)
            flat = np.column_stack(((coords - coords[0]) @ u, (coords - coords[0]) @ v))
            area = float(
                np.sum(
                    flat[:, 0] * np.roll(flat[:, 1], -1)
                    - np.roll(flat[:, 0], -1) * flat[:, 1]
                )
            )
            if area < 0:
                loop.reverse()  # ear clipping needs CCW input
                flat = flat[::-1]
            # Flip winding: the cap's outward normal faces away from the
            # kept (positive) side, i.e. along -unit.
            faces.extend((loop[i], loop[k], loop[j]) for i, j, k in _ear_clip(flat))
    return faces


def _ear_clip(flat: NDArray[np.float64]) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon given in 2D."""
    indices = list(range(len(flat)))
    triangles: list[tuple[int, int, int]] = []

    def cross(o: int, a: int, b: int) -> float:
        return float(
            (flat[a, 0] - flat[o, 0]) * (flat[b, 1] - flat[o, 1])
            - (flat[a, 1] - flat[o, 1]) * (flat[b, 0] - flat[o, 0])
        )

    while len(indices) > 3:
        for k in range(len(indices)):
            prev = indices[k - 1]
            cur = indices[k]
            nxt = indices[(k + 1) % len(indices)]
            if cross(prev, cur, nxt) <= 1e-12:
                continue  # reflex or collinear corner
            if any(
                cross(prev, cur, p) >= -1e-12
                and cross(cur, nxt, p) >= -1e-12
                and cross(nxt, prev, p) >= -1e-12
                for p in indices
                if p not in (prev, cur, nxt)
            ):
                continue
            triangles.append((prev, cur, nxt))
            indices.pop(k)
            break
        else:
            break  # degenerate polygon: fan whatever remains
    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))
    elif len(indices) > 3:
        triangles.extend(
            (indices[0], indices[k], indices[k + 1]) for k in range(1, len(indices) - 1)
        )
    return triangles


def _estimate_mesh(
    mesh: Any, density_g_cm3: float, flow_rate_mm3_s: float, price_per_kg: float
) -> dict[str, float]:
    volume_mm3 = abs(float(mesh.volume))
    height_mm = float(mesh.extents[2])
    volume_cm3 = volume_mm3 / 1000.0
    mass_g = volume_cm3 * density_g_cm3
    time_s = (
        volume_mm3 / flow_rate_mm3_s * ESTIMATE_OVERHEAD_FACTOR
        + height_mm / ESTIMATE_LAYER_HEIGHT_MM * ESTIMATE_PERIMETER_S_PER_LAYER
    )
    return {
        "volume_cm3": volume_cm3,
        "mass_g": mass_g,
        "time_s": time_s,
        "cost": mass_g / 1000.0 * price_per_kg,
    }


def _round_estimate(raw: dict[str, float]) -> dict[str, float]:
    return {
        "volume_cm3": round(raw["volume_cm3"], 3),
        "mass_g": round(raw["mass_g"], 2),
        "time_s": round(raw["time_s"], 1),
        "cost": round(raw["cost"], 2),
    }
