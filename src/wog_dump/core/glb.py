"""Minimal glTF 2.0 / GLB writer for scene export.

Implements just enough of the spec for the Blender scene pipeline:
embedded PNG textures, PBR materials, node hierarchies with TRS
transforms, meshes with multiple primitives (one per material) and
perspective cameras with ``KHR_lights_punctual`` lights.  Written by
hand (instead of via a glTF library) to keep the dependency list of
the package unchanged.
"""

from __future__ import annotations

import json
import math
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["GLBWriter", "MeshPrimitive", "SceneError"]

GLB_MAGIC = 0x46546C67  # 'glTF'
JSON_CHUNK_TYPE = 0x4E4F534A  # 'JSON'
BIN_CHUNK_TYPE = 0x004E4942  # 'BIN'

COMPONENT_FLOAT = 5126
COMPONENT_UNSIGNED_SHORT = 5123
COMPONENT_UNSIGNED_INT = 5125

TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY_BUFFER = 34963

FILTER_LINEAR = 9729
FILTER_LINEAR_MIPMAP_LINEAR = 9987
WRAP_REPEAT = 10497


class SceneError(Exception):
    """Raised when a scene cannot be exported."""


@dataclass
class MeshPrimitive:
    """Geometry for one material slot of a mesh.

    ``positions``/``normals`` are flat float triples, ``uvs`` flat float
    pairs, ``indices`` triangle vertex indices (counter-clockwise in
    glTF's right-handed space).
    """

    positions: list[float]
    indices: list[int]
    normals: list[float] | None = None
    uvs: list[float] | None = None
    material: int | None = None


@dataclass
class _GLTFMaterial:
    name: str
    base_color_factor: list[float] | None = None
    base_color_texture: int | None = None
    metallic: float = 0.0
    roughness: float = 0.5
    alpha_mode: str = "OPAQUE"
    alpha_cutoff: float = 0.5
    double_sided: bool = False


@dataclass
class _GLTFNode:
    name: str
    mesh: int | None = None
    translation: tuple[float, float, float] | None = None
    rotation: tuple[float, float, float, float] | None = None  # x, y, z, w
    scale: tuple[float, float, float] | None = None
    camera: int | None = None
    light: dict[str, object] | None = None
    children: list[int] = field(default_factory=list)


def _srgb_to_linear(c: float) -> float:
    """Convert an sRGB channel to linear space (glTF factors are linear)."""
    if c <= 0.04045:
        return c / 12.92
    return float(((c + 0.055) / 1.055) ** 2.4)


class GLBWriter:
    """Accumulates glTF entities and serializes them as a GLB file."""

    def __init__(self, generator: str = "WOG Dump") -> None:
        self.generator = generator
        self._bin = bytearray()
        self._buffer_views: list[dict[str, int]] = []
        self._accessors: list[dict[str, object]] = []
        self._meshes: list[dict[str, object]] = []
        self._materials: list[_GLTFMaterial] = []
        self._images: list[dict[str, object]] = []
        self._samplers: list[dict[str, int]] = []
        self._textures: list[dict[str, int]] = []
        self._nodes: list[_GLTFNode] = []
        self._cameras: list[dict[str, object]] = []
        self._lights: list[dict[str, object]] = []
        self._root_children: list[int] = []

    # ------------------------------------------------------------------
    # Buffer primitives
    # ------------------------------------------------------------------

    def _add_buffer_view(self, data: bytes, target: int | None = None) -> int:
        offset = (len(self._bin) + 3) & ~3  # align to 4 bytes
        self._bin.extend(b"\x00" * (offset - len(self._bin)))
        self._bin.extend(data)
        view: dict[str, int] = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if target is not None:
            view["target"] = target
        self._buffer_views.append(view)
        return len(self._buffer_views) - 1

    def _add_accessor(
        self,
        view: int,
        component_type: int,
        count: int,
        type_str: str,
        min_values: list[float] | None = None,
        max_values: list[float] | None = None,
    ) -> int:
        accessor: dict[str, object] = {
            "bufferView": view,
            "componentType": component_type,
            "count": count,
            "type": type_str,
        }
        if min_values is not None:
            accessor["min"] = [round(v, 6) for v in min_values]
        if max_values is not None:
            accessor["max"] = [round(v, 6) for v in max_values]
        self._accessors.append(accessor)
        return len(self._accessors) - 1

    # ------------------------------------------------------------------
    # Textures and materials
    # ------------------------------------------------------------------

    def add_image(self, data: bytes, mime_type: str = "image/png") -> int:
        """Embed raw image bytes and return the image index."""
        view = self._add_buffer_view(data)
        self._images.append({"bufferView": view, "mimeType": mime_type})
        return len(self._images) - 1

    def add_texture(self, image_index: int) -> int:
        """Create a texture bound to an image (linear filtering, repeat)."""
        if not self._samplers:
            self._samplers.append(
                {
                    "magFilter": FILTER_LINEAR,
                    "minFilter": FILTER_LINEAR_MIPMAP_LINEAR,
                    "wrapS": WRAP_REPEAT,
                    "wrapT": WRAP_REPEAT,
                }
            )
        self._textures.append({"sampler": 0, "source": image_index})
        return len(self._textures) - 1

    def add_material(
        self,
        name: str,
        base_color_factor: tuple[float, float, float, float] | None = None,
        base_color_texture: int | None = None,
        metallic: float = 0.0,
        roughness: float = 0.5,
        alpha_mode: str = "OPAQUE",
        alpha_cutoff: float = 0.5,
        double_sided: bool = False,
    ) -> int:
        """Add a PBR material; sRGB colors are converted to linear space."""
        factor = None
        if base_color_factor is not None:
            factor = [_srgb_to_linear(c) for c in base_color_factor]
        self._materials.append(
            _GLTFMaterial(
                name=name,
                base_color_factor=factor,
                base_color_texture=base_color_texture,
                metallic=metallic,
                roughness=roughness,
                alpha_mode=alpha_mode,
                alpha_cutoff=alpha_cutoff,
                double_sided=double_sided,
            )
        )
        return len(self._materials) - 1

    # ------------------------------------------------------------------
    # Meshes
    # ------------------------------------------------------------------

    def add_mesh(self, name: str, primitives: list[MeshPrimitive]) -> int:
        """Add a mesh with one glTF primitive per MeshPrimitive."""
        if not primitives:
            raise SceneError(f"Mesh {name!r} has no primitives")

        gltf_primitives = []
        for primitive in primitives:
            attributes: dict[str, int] = {}
            positions = primitive.positions
            if not positions:
                continue

            min_values = [math.inf] * 3
            max_values = [-math.inf] * 3
            for i in range(0, len(positions), 3):
                for axis in range(3):
                    value = positions[i + axis]
                    if value < min_values[axis]:
                        min_values[axis] = value
                    if value > max_values[axis]:
                        max_values[axis] = value

            packed = struct.pack(f"<{len(positions)}f", *positions)
            view = self._add_buffer_view(packed, TARGET_ARRAY_BUFFER)
            attributes["POSITION"] = self._add_accessor(
                view,
                COMPONENT_FLOAT,
                len(positions) // 3,
                "VEC3",
                min_values=min_values,
                max_values=max_values,
            )

            if primitive.normals:
                packed = struct.pack(f"<{len(primitive.normals)}f", *primitive.normals)
                view = self._add_buffer_view(packed, TARGET_ARRAY_BUFFER)
                attributes["NORMAL"] = self._add_accessor(
                    view, COMPONENT_FLOAT, len(primitive.normals) // 3, "VEC3"
                )

            if primitive.uvs:
                packed = struct.pack(f"<{len(primitive.uvs)}f", *primitive.uvs)
                view = self._add_buffer_view(packed, TARGET_ARRAY_BUFFER)
                attributes["TEXCOORD_0"] = self._add_accessor(
                    view, COMPONENT_FLOAT, len(primitive.uvs) // 2, "VEC2"
                )

            indices = primitive.indices
            if max(indices) < 65536:
                packed = struct.pack(f"<{len(indices)}H", *indices)
                component = COMPONENT_UNSIGNED_SHORT
            else:
                packed = struct.pack(f"<{len(indices)}I", *indices)
                component = COMPONENT_UNSIGNED_INT
            view = self._add_buffer_view(packed, TARGET_ELEMENT_ARRAY_BUFFER)
            indices_accessor = self._add_accessor(
                view, component, len(indices), "SCALAR"
            )

            gltf_primitive: dict[str, object] = {
                "attributes": attributes,
                "indices": indices_accessor,
                "mode": 4,
            }
            if primitive.material is not None:
                gltf_primitive["material"] = primitive.material
            gltf_primitives.append(gltf_primitive)

        if not gltf_primitives:
            raise SceneError(f"Mesh {name!r} produced no valid primitives")

        self._meshes.append({"name": name, "primitives": gltf_primitives})
        return len(self._meshes) - 1

    # ------------------------------------------------------------------
    # Nodes, cameras, lights
    # ------------------------------------------------------------------

    def add_node(
        self,
        name: str,
        mesh: int | None = None,
        translation: tuple[float, float, float] | None = None,
        rotation: tuple[float, float, float, float] | None = None,
        scale: tuple[float, float, float] | None = None,
        parent: int | None = None,
    ) -> int:
        """Add a node; ``parent=None`` attaches to the scene root."""
        node = _GLTFNode(
            name=name,
            mesh=mesh,
            translation=translation,
            rotation=rotation,
            scale=scale,
        )
        self._nodes.append(node)
        index = len(self._nodes) - 1
        if parent is None:
            self._root_children.append(index)
        else:
            self._nodes[parent].children.append(index)
        return index

    def attach_child(self, parent: int, child: int) -> None:
        """Move a root-level node under an existing parent node."""
        self._root_children.remove(child)
        self._nodes[parent].children.append(child)

    def add_camera(
        self,
        name: str,
        yfov: float = math.radians(50.0),
        znear: float = 0.001,
        zfar: float = 100.0,
    ) -> int:
        self._cameras.append(
            {
                "name": name,
                "type": "perspective",
                "perspective": {
                    "aspectRatio": 1.777778,
                    "yfov": yfov,
                    "znear": znear,
                    "zfar": zfar,
                },
            }
        )
        return len(self._cameras) - 1

    def add_directional_light(
        self, name: str, color: tuple[float, float, float], intensity: float
    ) -> int:
        """Add a KHR_lights_punctual directional light, returns its index."""
        self._lights.append(
            {
                "name": name,
                "type": "directional",
                "color": list(color),
                "intensity": intensity,
            }
        )
        return len(self._lights) - 1

    def add_camera_node(
        self,
        name: str,
        camera: int,
        eye: tuple[float, float, float],
        target: tuple[float, float, float],
    ) -> int:
        """Add a camera node rotated to look from ``eye`` at ``target``.

        Positions and the computed orientation are in glTF space (Y up,
        camera looks along local -Z).
        """
        self._nodes.append(
            _GLTFNode(
                name=name,
                camera=camera,
                translation=eye,
                rotation=_look_at(eye, target),
            )
        )
        index = len(self._nodes) - 1
        self._root_children.append(index)
        return index

    def add_light_node(
        self,
        name: str,
        light: int,
        eye: tuple[float, float, float],
        target: tuple[float, float, float],
    ) -> int:
        """Add a light node aimed from ``eye`` at ``target``.

        The node references the light through the KHR_lights_punctual
        extension object (``{"KHR_lights_punctual": {"light": i}}``) -
        a bare index is rejected by strict parsers such as Blender's.
        """
        self._nodes.append(
            _GLTFNode(
                name=name,
                light={"KHR_lights_punctual": {"light": light}},
                translation=eye,
                rotation=_look_at(eye, target),
            )
        )
        index = len(self._nodes) - 1
        self._root_children.append(index)
        return index

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _build_document(self) -> dict[str, object]:
        extensions_used = []
        if self._lights:
            extensions_used.append("KHR_lights_punctual")

        materials = []
        for material in self._materials:
            pbr: dict[str, object] = {
                "metallicFactor": material.metallic,
                "roughnessFactor": material.roughness,
            }
            if material.base_color_texture is not None:
                pbr["baseColorTexture"] = {"index": material.base_color_texture}
            if material.base_color_factor is not None:
                pbr["baseColorFactor"] = material.base_color_factor

            entry: dict[str, object] = {
                "name": material.name,
                "pbrMetallicRoughness": pbr,
            }
            if material.alpha_mode != "OPAQUE":
                entry["alphaMode"] = material.alpha_mode
                if material.alpha_mode == "MASK":
                    entry["alphaCutoff"] = material.alpha_cutoff
            if material.double_sided:
                entry["doubleSided"] = True
            materials.append(entry)

        nodes: list[dict[str, object]] = []
        for node in self._nodes:
            node_entry: dict[str, object] = {"name": node.name}
            if node.mesh is not None:
                node_entry["mesh"] = node.mesh
            if node.camera is not None:
                node_entry["camera"] = node.camera
            if node.translation is not None:
                node_entry["translation"] = [round(v, 9) for v in node.translation]
            if node.rotation is not None:
                node_entry["rotation"] = [round(v, 9) for v in node.rotation]
            if node.scale is not None:
                node_entry["scale"] = [round(v, 9) for v in node.scale]
            if node.light is not None:
                node_entry["extensions"] = node.light
            if node.children:
                node_entry["children"] = node.children
            nodes.append(node_entry)

        document: dict[str, object] = {
            "asset": {"version": "2.0", "generator": self.generator},
            "scene": 0,
            "scenes": [{"nodes": self._root_children}],
            "nodes": nodes,
            "meshes": self._meshes,
            "accessors": self._accessors,
            "bufferViews": self._buffer_views,
            "buffers": [{"byteLength": len(self._bin)}],
            "materials": materials,
        }
        if self._cameras:
            document["cameras"] = self._cameras
        if self._images:
            document["images"] = self._images
            document["samplers"] = self._samplers
            document["textures"] = self._textures
        if self._lights:
            document["extensionsUsed"] = extensions_used
            document["extensions"] = {"KHR_lights_punctual": {"lights": self._lights}}
        return document

    def write(self, path: Path) -> None:
        """Serialize to a GLB file (temp-file + atomic rename)."""
        document = self._build_document()
        json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
        json_padding = (4 - len(json_bytes) % 4) % 4
        json_bytes += b" " * json_padding

        bin_padding = (4 - len(self._bin) % 4) % 4
        binary = bytes(self._bin) + b"\x00" * bin_padding

        total = 12 + 8 + len(json_bytes) + 8 + len(binary)
        path.parent.mkdir(parents=True, exist_ok=True)
        # os.replace swaps the directory entry: a pre-planted symlink at
        # the destination is replaced instead of being followed
        data = (
            struct.pack("<III", GLB_MAGIC, 2, total)
            + struct.pack("<II", len(json_bytes), JSON_CHUNK_TYPE)
            + json_bytes
            + struct.pack("<II", len(binary), BIN_CHUNK_TYPE)
            + binary
        )
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


def _look_at(
    eye: tuple[float, float, float], target: tuple[float, float, float]
) -> tuple[float, float, float, float]:
    """Rotation quaternion orienting local -Z towards ``target``."""
    forward = _normalize(_sub(target, eye))
    up_hint = (0.0, 1.0, 0.0)
    if abs(forward[0]) < 1e-6 and abs(forward[2]) < 1e-6:
        up_hint = (0.0, 0.0, 1.0)
    right = _normalize(_cross(forward, up_hint))
    up = _cross(right, forward)

    # Column-major rotation matrix with camera axes: X=right, Y=up, Z=-forward
    m = [
        right[0],
        right[1],
        right[2],
        up[0],
        up[1],
        up[2],
        -forward[0],
        -forward[1],
        -forward[2],
    ]
    return _matrix_to_quaternion(m)


def _sub(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _normalize(v: tuple[float, ...]) -> tuple[float, float, float]:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-12:
        raise SceneError("Cannot normalize zero-length vector")
    return (v[0] / length, v[1] / length, v[2] / length)


def _cross(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _matrix_to_quaternion(m: list[float]) -> tuple[float, float, float, float]:
    """Convert a column-major 3x3 rotation matrix to a quaternion (x, y, z, w)."""
    # glTF stores matrices column-major; unpack to row-major r[i][j]
    r00, r10, r20, r01, r11, r21, r02, r12, r22 = m
    trace = r00 + r11 + r22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (r21 - r12) / s
        y = (r02 - r20) / s
        z = (r10 - r01) / s
    elif r00 > r11 and r00 > r22:
        s = math.sqrt(1.0 + r00 - r11 - r22) * 2.0
        w = (r21 - r12) / s
        x = 0.25 * s
        y = (r01 + r10) / s
        z = (r02 + r20) / s
    elif r11 > r22:
        s = math.sqrt(1.0 + r11 - r00 - r22) * 2.0
        w = (r02 - r20) / s
        x = (r01 + r10) / s
        y = 0.25 * s
        z = (r12 + r21) / s
    else:
        s = math.sqrt(1.0 + r22 - r00 - r11) * 2.0
        w = (r10 - r01) / s
        x = (r02 + r20) / s
        y = (r12 + r21) / s
        z = 0.25 * s
    return (x, y, z, w)
