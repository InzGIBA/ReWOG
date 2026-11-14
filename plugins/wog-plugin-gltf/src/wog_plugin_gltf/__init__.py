"""Separate glTF exporter plugin for WOG Dump.

Splits a built GLB scene into ``<stem>.gltf`` JSON, a ``<stem>.bin``
buffer and a ``textures/`` folder, for web pipelines that prefer
separate assets over one embedded GLB:

    pip install ./plugins/wog-plugin-gltf
    wog-dump build-scene ak47 --format gltf

Pure stdlib: the GLB layout follows ``wog_dump.core.glb`` (12-byte
header, JSON chunk, BIN chunk, images embedded through bufferViews).
Geometry is copied verbatim, so ``units``/``per_part`` are ignored.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

__all__ = ["SeparateGltfExporter"]

_GLB_MAGIC = 0x46546C67  # 'glTF'
_JSON_CHUNK_TYPE = 0x4E4F534A  # 'JSON'
_BIN_CHUNK_TYPE = 0x004E4942  # 'BIN'

_MIME_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def _parse_glb(data: bytes, source: Path) -> tuple[dict[str, Any], bytes]:
    """Split GLB bytes into the JSON document and the exact BIN chunk."""
    if len(data) < 20:
        raise ValueError(f"Not a valid GLB file: {source}")
    magic, version, total = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC or version != 2 or total != len(data):
        raise ValueError(f"Not a valid GLB file: {source}")
    json_len, json_type = struct.unpack_from("<II", data, 12)
    if json_type != _JSON_CHUNK_TYPE or 20 + json_len + 8 > len(data):
        raise ValueError(f"GLB has no JSON chunk: {source}")
    try:
        document = json.loads(data[20 : 20 + json_len])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"GLB JSON chunk is not valid JSON: {source}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"GLB JSON chunk is not an object: {source}")
    bin_offset = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<II", data, bin_offset)
    if bin_type != _BIN_CHUNK_TYPE:
        raise ValueError(f"GLB has no binary chunk: {source}")
    binary = data[bin_offset + 8 : bin_offset + 8 + bin_len]
    if len(binary) != bin_len:
        raise ValueError(f"GLB binary chunk is truncated: {source}")
    return document, binary


def _view_bytes(binary: bytes, views: list[Any], index: Any, source: Path) -> bytes:
    """Slice one bufferView out of the BIN chunk, validating its range."""
    if not isinstance(index, int) or not 0 <= index < len(views):
        raise ValueError(f"GLB image references an invalid bufferView: {source}")
    view = views[index]
    if not isinstance(view, dict) or view.get("buffer", 0) != 0:
        raise ValueError(f"GLB bufferView {index} does not use buffer 0: {source}")
    offset = view.get("byteOffset", 0)
    length = view.get("byteLength")
    if (
        not isinstance(offset, int)
        or not isinstance(length, int)
        or offset < 0
        or length < 0
        or offset + length > len(binary)
    ):
        raise ValueError(f"GLB bufferView {index} is out of bounds: {source}")
    return binary[offset : offset + length]


def _texture_name(image: dict[str, Any], index: int, used: set[str]) -> str:
    """Sanitized, collision-free file name for one embedded image."""
    mime = image.get("mimeType")
    ext = _MIME_EXTENSIONS.get(mime) if isinstance(mime, str) else None
    if ext is None:
        raise ValueError(f"GLB image {index} has an unsupported mimeType: {mime!r}")
    raw = image.get("name")
    base = _UNSAFE_NAME.sub("_", str(raw)).strip("_-") if raw else ""
    if not base:
        base = f"image_{index}"
    name = f"{base}.{ext}"
    suffix = 1
    while name in used:
        name = f"{base}_{suffix}.{ext}"
        suffix += 1
    used.add(name)
    return name


class SeparateGltfExporter:
    """Writes a GLB scene as separate .gltf, .bin and texture files.

    Fulfills the ``wog_dump.core.extensions.Exporter`` protocol. The
    shared ``units``/``per_part`` options are accepted but ignored:
    the geometry bytes are copied unchanged.
    """

    name: str = "gltf"
    extensions: tuple[str, ...] = (".gltf",)

    def convert(self, glb_path: Path, output_dir: Path, **options: Any) -> list[Path]:
        document, binary = _parse_glb(glb_path.read_bytes(), glb_path)
        views = document.get("bufferViews", [])
        images = document.get("images", [])
        if not isinstance(views, list) or not isinstance(images, list):
            raise ValueError(f"GLB bufferViews/images are not lists: {glb_path}")

        # Extract and validate everything before writing any output.
        textures: list[tuple[str, bytes]] = []
        used: set[str] = set()
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ValueError(f"GLB image {index} is not an object: {glb_path}")
            if "bufferView" not in image:
                continue
            data = _view_bytes(binary, views, image["bufferView"], glb_path)
            filename = _texture_name(image, index, used)
            textures.append((filename, data))
            image["uri"] = f"textures/{filename}"
            del image["bufferView"]

        stem = glb_path.stem
        bin_name = f"{stem}.bin"
        buffer_entry: dict[str, Any] = {"uri": bin_name, "byteLength": len(binary)}
        buffers = document.get("buffers")
        if isinstance(buffers, list) and buffers:
            buffers[0] = buffer_entry
        else:
            document["buffers"] = [buffer_entry]

        output_dir.mkdir(parents=True, exist_ok=True)
        bin_path = output_dir / bin_name
        bin_path.write_bytes(binary)

        texture_paths: list[Path] = []
        if textures:
            textures_dir = output_dir / "textures"
            textures_dir.mkdir(parents=True, exist_ok=True)
            for filename, data in textures:
                texture_path = textures_dir / filename
                texture_path.write_bytes(data)
                texture_paths.append(texture_path)

        gltf_path = output_dir / f"{stem}.gltf"
        gltf_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return [gltf_path, bin_path, *texture_paths]
