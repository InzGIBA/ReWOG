"""Format exporters for built scenes: STL, USD/USDZ and FBX.

The GLB file is always the source of truth: every exporter reads the
scene the pipeline just wrote (``parse_glb``) and converts it. STL is
self-contained, USD needs the ``usd-core`` extra (``pip install
wog-dump[usd]``) and FBX goes through headless Blender (the ``fbx``
format has no pure-Python writer).

Units: the GLB scene is in meters. STL is unit-blind and slicers
assume millimeters, so ``ExportOptions.units`` bakes a scale into the
file (``mm`` multiplies coordinates by 1000). USD carries unit
metadata instead (metersPerUnit), FBX keeps Blender-native units.

Adding a format means adding an exporter class with an ``export(scene,
glb_path, output_dir, options)`` method and registering it in
``EXPORTERS`` (same pattern as the unpacker registry).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .paths import safe_object_name
from .transforms import compose_matrix, multiply_matrix, transform_point

__all__ = [
    "EXPORTERS",
    "EXPORT_FORMATS",
    "ExportError",
    "ExportOptions",
    "FBXExporter",
    "GlbImage",
    "GlbMaterial",
    "GlbMesh",
    "GlbNode",
    "GlbPrimitive",
    "GlbScene",
    "STLExporter",
    "USDExporter",
    "convert_format",
    "find_blender",
    "parse_glb",
    "write_stl",
]


class ExportError(Exception):
    """Raised when a scene cannot be converted to a target format."""


EXPORT_FORMATS = frozenset({"glb", "stl", "usd", "usdz", "fbx"})

# Unit name -> multiplier from meters (STL has no unit metadata)
UNIT_SCALES = {"m": 1.0, "cm": 100.0, "mm": 1000.0}

GLB_MAGIC = 0x46546C67
JSON_CHUNK_TYPE = 0x4E4F534A
BIN_CHUNK_TYPE = 0x004E4942


@dataclass
class ExportOptions:
    """Options shared by the exporters."""

    units: str = "m"  # "m", "cm" or "mm" (STL only)
    per_part: bool = False  # one STL per top-level part instead of merged


@dataclass
class GlbPrimitive:
    """One renderable primitive (flat VEC3 positions + triangle indices)."""

    positions: list[float]
    indices: list[int]
    material: int | None = None


@dataclass
class GlbMesh:
    name: str
    primitives: list[GlbPrimitive]


@dataclass
class GlbNode:
    """A node with its mesh reference and precomputed world transform."""

    name: str
    mesh: int | None
    world: list[float]  # column-major 4x4
    children: list[int]


@dataclass
class GlbMaterial:
    name: str
    base_color_factor: tuple[float, float, float, float] | None
    base_color_texture: int | None  # image index


@dataclass
class GlbImage:
    data: bytes
    mime_type: str


@dataclass
class GlbScene:
    """The parsed content of one GLB file."""

    nodes: list[GlbNode] = field(default_factory=list)
    meshes: list[GlbMesh] = field(default_factory=list)
    materials: list[GlbMaterial] = field(default_factory=list)
    images: list[GlbImage] = field(default_factory=list)


class SceneExporter(Protocol):
    """Contract every format exporter fulfills."""

    def export(
        self, scene: GlbScene, glb_path: Path, output_dir: Path, options: ExportOptions
    ) -> list[Path]: ...


# ----------------------------------------------------------------------
# GLB parsing
# ----------------------------------------------------------------------


def _read_accessor(document: dict[str, Any], binary: bytes, index: int) -> list[float]:
    """Read one float accessor (SCALAR, VEC2, VEC3 or VEC4)."""
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    component = {5126: "f", 5123: "H", 5125: "I"}[accessor["componentType"]]
    n_components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    count = accessor["count"] * n_components
    return list(struct.unpack_from(f"<{count}{component}", binary, offset))


def parse_glb(path: Path) -> GlbScene:
    """Parse a GLB written by the scene pipeline into plain structures."""
    data = path.read_bytes()
    if len(data) < 20:
        raise ExportError(f"Not a valid GLB file: {path}")
    magic, version, total = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2 or total != len(data):
        raise ExportError(f"Not a valid GLB file: {path}")
    json_len, json_type = struct.unpack_from("<II", data, 12)
    if json_type != JSON_CHUNK_TYPE:
        raise ExportError(f"GLB has no JSON chunk: {path}")
    document = json.loads(data[20 : 20 + json_len])
    bin_offset = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<II", data, bin_offset)
    if bin_type != BIN_CHUNK_TYPE:
        raise ExportError(f"GLB has no binary chunk: {path}")
    binary = data[bin_offset + 8 : bin_offset + 8 + bin_len]

    scene = GlbScene()
    for material in document.get("materials", []):
        pbr = material.get("pbrMetallicRoughness", {})
        texture = pbr.get("baseColorTexture")
        image_index = None
        if texture is not None:
            image_index = document["textures"][texture["index"]].get("source")
        scene.materials.append(
            GlbMaterial(
                name=material.get("name", "material"),
                base_color_factor=pbr.get("baseColorFactor"),
                base_color_texture=image_index,
            )
        )
    for image in document.get("images", []):
        buffer_view = document["bufferViews"][image["bufferView"]]
        offset = buffer_view.get("byteOffset", 0)
        scene.images.append(
            GlbImage(
                data=binary[offset : offset + buffer_view["byteLength"]],
                mime_type=image.get("mimeType", "image/png"),
            )
        )

    raw_nodes = document.get("nodes", [])
    identity = compose_matrix((0, 0, 0), (0, 0, 0, 1), (1, 1, 1))
    # Raw node indices stay the addressing scheme: children lists and
    # scenes reference them
    scene.nodes = [
        GlbNode(name="unused", mesh=None, world=identity, children=[])
        for _ in raw_nodes
    ]
    roots = document.get("scenes", [{}])[0].get("nodes", [])
    queue = [(index, identity) for index in roots]
    while queue:
        index, parent_world = queue.pop(0)
        raw = raw_nodes[index]
        local = compose_matrix(
            raw.get("translation", (0.0, 0.0, 0.0)),
            raw.get("rotation", (0.0, 0.0, 0.0, 1.0)),
            raw.get("scale", (1.0, 1.0, 1.0)),
        )
        world = multiply_matrix(parent_world, local)
        scene.nodes[index] = GlbNode(
            name=raw.get("name", "node"),
            mesh=raw.get("mesh"),
            world=world,
            children=raw.get("children", []),
        )
        for child in raw.get("children", []):
            queue.append((child, world))

    for raw_mesh in document.get("meshes", []):
        primitives = []
        for raw_primitive in raw_mesh.get("primitives", []):
            positions = _read_accessor(
                document, binary, raw_primitive["attributes"]["POSITION"]
            )
            indices_accessor = raw_primitive.get("indices")
            indices = (
                [int(i) for i in _read_accessor(document, binary, indices_accessor)]
                if indices_accessor is not None
                else []
            )
            primitives.append(
                GlbPrimitive(
                    positions=positions,
                    indices=indices,
                    material=raw_primitive.get("material"),
                )
            )
        scene.meshes.append(
            GlbMesh(name=raw_mesh.get("name", "mesh"), primitives=primitives)
        )

    return scene


# ----------------------------------------------------------------------
# STL
# ----------------------------------------------------------------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _triangle_normal(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _stl_bytes(triangles: list[Any], header: str = "WOG Dump solid") -> bytes:
    """Binary STL bytes for world-space triangles."""
    out = bytearray()
    out.extend(header.encode("ascii")[:80].ljust(80, b" "))
    out.extend(struct.pack("<I", len(triangles)))
    for triangle in triangles:
        a, b, c = triangle
        out.extend(struct.pack("<3f", *_triangle_normal(a, b, c)))
        for vertex in (a, b, c):
            out.extend(struct.pack("<3f", *vertex))
        out.extend(struct.pack("<H", 0))
    return bytes(out)


def write_stl(triangles: list[Any], path: Path) -> Path:
    """Write binary STL (units already baked into the coordinates)."""
    _atomic_write_bytes(path, _stl_bytes(triangles))
    return path


def _mesh_triangles(
    mesh: GlbMesh, world: list[float], unit_scale: float
) -> list[tuple[tuple[float, float, float], ...]]:
    """World-space triangles of one mesh, scaled by ``unit_scale``."""
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    for primitive in mesh.primitives:
        positions = primitive.positions
        for i in range(0, len(primitive.indices), 3):
            face = []
            for corner in primitive.indices[i : i + 3]:
                x, y, z = positions[corner * 3 : corner * 3 + 3]
                p = transform_point(world, (x, y, z))
                face.append((p[0] * unit_scale, p[1] * unit_scale, p[2] * unit_scale))
            triangles.append(tuple(face))
    return triangles


class STLExporter:
    """Binary STL export, merged or one file per meshed top-level part."""

    format = "stl"

    def export(
        self, scene: GlbScene, glb_path: Path, output_dir: Path, options: ExportOptions
    ) -> list[Path]:
        unit_scale = UNIT_SCALES.get(options.units)
        if unit_scale is None:
            raise ExportError(
                f"Unknown STL unit {options.units!r}, expected one of {sorted(UNIT_SCALES)}"
            )

        weapon = glb_path.stem
        if options.per_part:
            paths = []
            used: set[str] = set()
            for node in scene.nodes:
                if node.mesh is None:
                    continue
                name = safe_object_name(node.name) or "part"
                stem = name
                suffix = 1
                while stem in used:
                    suffix += 1
                    stem = f"{name}_{suffix}"
                used.add(stem)
                node_triangles = _mesh_triangles(
                    scene.meshes[node.mesh], node.world, unit_scale
                )
                if not node_triangles:
                    continue
                paths.append(
                    write_stl(node_triangles, output_dir / f"{weapon}__{stem}.stl")
                )
            if not paths:
                raise ExportError("Scene has no meshed parts for per-part STL")
            return paths

        triangles: list[tuple[tuple[float, float, float], ...]] = []
        for node in scene.nodes:
            if node.mesh is None:
                continue
            triangles.extend(
                _mesh_triangles(scene.meshes[node.mesh], node.world, unit_scale)
            )
        if not triangles:
            raise ExportError("Scene has no triangles to export")
        return [write_stl(triangles, output_dir / f"{weapon}.stl")]


# ----------------------------------------------------------------------
# FBX (headless Blender)
# ----------------------------------------------------------------------


def find_blender() -> Path | None:
    """Locate Blender: WOG_BLENDER_PATH first, then PATH."""
    configured = os.environ.get("WOG_BLENDER_PATH")
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate
    found = shutil.which("blender")
    return Path(found) if found else None


def _blender_fbx_script(glb_path: Path, fbx_path: Path) -> str:
    return f'''"""Auto-generated: import a WOG Dump GLB scene and export FBX."""
import bpy

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r"{glb_path}")
bpy.ops.export_scene.fbx(
    filepath=r"{fbx_path}",
    use_selection=False,
    add_leaf_bones=False,
    bake_space_transform=True,
)
'''


class FBXExporter:
    """FBX export via headless Blender (import GLB, export FBX)."""

    format = "fbx"

    def export(
        self, scene: GlbScene, glb_path: Path, output_dir: Path, options: ExportOptions
    ) -> list[Path]:
        blender = find_blender()
        if blender is None:
            raise ExportError(
                "Blender not found for FBX export: set WOG_BLENDER_PATH or "
                "install Blender (the fbx format has no pure-Python writer)"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        fbx_path = output_dir / f"{glb_path.stem}.fbx"
        script = _blender_fbx_script(glb_path, fbx_path)
        with tempfile.TemporaryDirectory(prefix="wog-fbx-") as tmp:
            script_path = Path(tmp) / "fbx_export.py"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [str(blender), "--background", "--python", str(script_path)],
                capture_output=True,
                text=True,
                timeout=600,
            )
        if result.returncode != 0 or not fbx_path.exists():
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
            raise ExportError(f"Blender FBX export failed: {' | '.join(tail)}")
        return [fbx_path]


_PRIM_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def _prim_name(name: str) -> str:
    """Sanitize a scene name into a valid USD prim name component.

    USD prim names allow alphanumerics and underscores only; anything
    else maps to an underscore, a leading digit gains a ``n_`` prefix
    and empty results fall back to ``node``.
    """
    base = _PRIM_NAME_RE.sub("_", name)
    if not base:
        return "node"
    if base[0].isdigit():
        base = f"n_{base}"
    return base


# ----------------------------------------------------------------------
# USD / USDZ (usd-core extra)
# ----------------------------------------------------------------------


class USDExporter:
    """USD export with hierarchy, meshes and preview materials.

    Requires ``usd-core`` (``pip install wog-dump[usd]``). ``format``
    selects ``usd`` (layer file, textures written alongside) or
    ``usdz`` (self-contained AR package). Geometry stays in meters,
    declared through the stage's metersPerUnit.
    """

    def __init__(self, format: str = "usd") -> None:
        self.format = format

    def export(
        self, scene: GlbScene, glb_path: Path, output_dir: Path, options: ExportOptions
    ) -> list[Path]:
        try:
            from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdUtils
        except ImportError as e:
            raise ExportError(
                f"usd-core is required for {self.format} export: "
                "pip install wog-dump[usd]"
            ) from e

        weapon = glb_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        # Textures must sit next to the layer with relative asset paths,
        # or the USDZ package cannot collect them
        textures_dir = output_dir / "textures"
        layer_path = output_dir / f"{weapon}.usd"
        stage = Usd.Stage.CreateNew(str(layer_path))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)

        self._materials(stage, scene, textures_dir)
        self._nodes(stage, scene, unit_scale=1.0)
        stage.GetRootLayer().Save()

        if self.format == "usd":
            if not layer_path.exists():
                raise ExportError(f"USD export produced no file: {layer_path}")
            return [layer_path]

        target = output_dir / f"{weapon}.usdz"
        if not UsdUtils.CreateNewUsdzPackage(
            Sdf.AssetPath(str(layer_path)), str(target)
        ):
            raise ExportError(f"USDZ packaging failed for {target}")
        layer_path.unlink(missing_ok=True)
        if not target.exists():
            raise ExportError(f"USDZ export produced no file: {target}")
        return [target]

    # -- internals -----------------------------------------------------

    def _materials(self, stage: Any, scene: GlbScene, textures_dir: Path) -> None:
        from pxr import Sdf, UsdShade

        for index, material in enumerate(scene.materials):
            mat_prim = UsdShade.Material.Define(stage, f"/World/Materials/mat_{index}")
            shader = UsdShade.Shader.Define(
                stage, f"/World/Materials/mat_{index}/preview"
            )
            shader.SetShaderId("UsdPreviewSurface")
            diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
            factor = material.base_color_factor or (1.0, 1.0, 1.0, 1.0)
            diffuse.Set((factor[0], factor[1], factor[2]))

            if material.base_color_texture is not None:
                image = scene.images[material.base_color_texture]
                textures_dir.mkdir(parents=True, exist_ok=True)
                texture_name = f"mat_{index}.png"
                _atomic_write_bytes(textures_dir / texture_name, image.data)
                texture_shader = UsdShade.Shader.Define(
                    stage, f"/World/Materials/mat_{index}/diffuse_texture"
                )
                texture_shader.SetShaderId("UsdUVTexture")
                # Relative to the layer so USDZ packaging collects it
                texture_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                    f"./textures/{texture_name}"
                )
                texture_shader.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
                diffuse.ConnectToSource(texture_shader.GetOutput("rgb"))

            mat_prim.CreateSurfaceOutput().ConnectToSource(
                shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
            )

    def _nodes(self, stage: Any, scene: GlbScene, unit_scale: float) -> None:
        from pxr import UsdGeom

        referenced: set[int] = set()
        for node in scene.nodes:
            referenced.update(node.children)
        roots = [i for i in range(len(scene.nodes)) if i not in referenced]

        used_names: dict[str, set[str]] = {}

        def unique_name(parent_path: str, name: str) -> str:
            taken = used_names.setdefault(parent_path, set())
            base = _prim_name(name)
            stem = base
            suffix = 1
            while stem in taken:
                suffix += 1
                stem = f"{base}_{suffix}"
            taken.add(stem)
            return stem

        def bind_material(mesh_prim: Any, material_index: int | None) -> None:
            from pxr import UsdShade

            if material_index is None:
                return
            UsdShade.MaterialBindingAPI(mesh_prim).Bind(
                UsdShade.Material.Get(stage, f"/World/Materials/mat_{material_index}")
            )

        queue: list[tuple[int, Any]] = [(index, None) for index in roots]
        while queue:
            index, parent_prim = queue.pop(0)
            node = scene.nodes[index]
            parent_path = (
                str(parent_prim.GetPath()) if parent_prim is not None else "/World"
            )
            xform = UsdGeom.Xform.Define(
                stage, f"{parent_path}/{unique_name(parent_path, node.name)}"
            )
            if node.mesh is not None:
                mesh = scene.meshes[node.mesh]
                for p_index, primitive in enumerate(mesh.primitives):
                    prim_name = unique_name(
                        str(xform.GetPath()),
                        f"{mesh.name or 'mesh'}_{p_index}",
                    )
                    mesh_prim = UsdGeom.Mesh.Define(
                        stage, f"{xform.GetPath()}/{prim_name}"
                    )
                    positions = primitive.positions
                    points = [
                        (
                            positions[i] * unit_scale,
                            positions[i + 1] * unit_scale,
                            positions[i + 2] * unit_scale,
                        )
                        for i in range(0, len(positions), 3)
                    ]
                    mesh_prim.CreatePointsAttr(points)
                    mesh_prim.CreateFaceVertexCountsAttr(
                        [3] * (len(primitive.indices) // 3)
                    )
                    mesh_prim.CreateFaceVertexIndicesAttr(primitive.indices)
                    bind_material(mesh_prim.GetPrim(), primitive.material)
            for child in node.children:
                queue.append((child, xform))


# ----------------------------------------------------------------------
# Registry and library entry point
# ----------------------------------------------------------------------


def _registry() -> dict[str, Callable[[], SceneExporter]]:
    """Format name -> zero-argument exporter factory."""
    return {
        "stl": STLExporter,
        "fbx": FBXExporter,
        "usd": USDExporter,
        "usdz": lambda: USDExporter(format="usdz"),
    }


EXPORTERS = _registry()


def convert_format(
    glb_path: Path,
    format: str,
    output_dir: Path,
    options: ExportOptions | None = None,
) -> list[Path]:
    """Convert one built GLB scene to a target format (library entry)."""
    if format not in EXPORT_FORMATS:
        raise ExportError(
            f"Unknown export format {format!r}, expected one of {sorted(EXPORT_FORMATS)}"
        )
    if format == "glb":
        return [glb_path]
    scene = parse_glb(glb_path)
    exporter = EXPORTERS[format]()
    return exporter.export(scene, glb_path, output_dir, options or ExportOptions())
