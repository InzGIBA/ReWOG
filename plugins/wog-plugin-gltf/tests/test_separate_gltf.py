"""Tests for the separate glTF exporter plugin."""

from __future__ import annotations

import json
import struct
import tomllib
import zlib
from pathlib import Path
from typing import Any

import pytest
from wog_plugin_gltf import SeparateGltfExporter

from wog_dump.core.extensions import Exporter
from wog_dump.core.glb import GLBWriter, MeshPrimitive

_GLB_MAGIC = 0x46546C67
_JSON_CHUNK_TYPE = 0x4E4F534A
_BIN_CHUNK_TYPE = 0x004E4942


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    """A real 1x1 PNG so byte-identity checks are meaningful."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    scanline = b"\x00" + bytes(color)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanline))
        + chunk(b"IEND", b"")
    )


def _build_glb(path: Path, images: list[tuple[bytes, str]] | None = None) -> None:
    """Build a one-mesh scene, optionally with embedded images."""
    writer = GLBWriter()
    material: int | None = None
    if images:
        first_texture: int | None = None
        for data, mime in images:
            texture = writer.add_texture(writer.add_image(data, mime))
            if first_texture is None:
                first_texture = texture
        material = writer.add_material("receiver", base_color_texture=first_texture)
    primitive = MeshPrimitive(
        positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        normals=[0.0, 0.0, 1.0] * 3,
        uvs=[0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices=[0, 1, 2],
        material=material,
    )
    mesh = writer.add_mesh("receiver_mesh", [primitive])
    writer.add_node("receiver", mesh=mesh, translation=(0.0, 0.5, 0.0))
    writer.write(path)


def _glb_parts(path: Path) -> tuple[dict[str, Any], bytes]:
    """Split a GLB into its JSON document and BIN chunk bytes."""
    data = path.read_bytes()
    json_len = struct.unpack_from("<I", data, 12)[0]
    document: dict[str, Any] = json.loads(data[20 : 20 + json_len])
    bin_offset = 20 + json_len
    bin_len = struct.unpack_from("<I", data, bin_offset)[0]
    return document, data[bin_offset + 8 : bin_offset + 8 + bin_len]


def _pack_glb(document: dict[str, Any], binary: bytes) -> bytes:
    """Re-serialize like GLBWriter.write (for tampered-input tests)."""
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary += b"\x00" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return (
        struct.pack("<III", _GLB_MAGIC, 2, total)
        + struct.pack("<II", len(json_bytes), _JSON_CHUNK_TYPE)
        + json_bytes
        + struct.pack("<II", len(binary), _BIN_CHUNK_TYPE)
        + binary
    )


@pytest.fixture()
def textured_glb(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    """GLB with a named PNG and an unnamed JPEG; returns (path, png, jpeg)."""
    png = _png_bytes((200, 30, 30))
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(24)) + b"\xff\xd9"
    glb = tmp_path / "ak47.glb"
    _build_glb(glb, [(png, "image/png"), (jpeg, "image/jpeg")])
    document, binary = _glb_parts(glb)
    document["images"][0]["name"] = "receiver base color"
    glb.write_bytes(_pack_glb(document, binary))
    return glb, png, jpeg


def test_roundtrip_textured(
    textured_glb: tuple[Path, bytes, bytes], tmp_path: Path
) -> None:
    glb, png, jpeg = textured_glb
    out = tmp_path / "export"
    written = SeparateGltfExporter().convert(glb, out, units="mm", per_part=True)

    gltf_path = out / "ak47.gltf"
    bin_path = out / "ak47.bin"
    tex_png = out / "textures" / "receiver_base_color.png"
    tex_jpg = out / "textures" / "image_1.jpg"
    assert written == [gltf_path, bin_path, tex_png, tex_jpg]
    assert all(path.is_file() for path in written)

    text = gltf_path.read_text(encoding="utf-8")
    assert text.startswith('{\n  "asset"')
    document = json.loads(text)
    original, binary = _glb_parts(glb)

    assert document["buffers"] == [{"uri": "ak47.bin", "byteLength": len(binary)}]
    assert bin_path.read_bytes() == binary
    assert tex_png.read_bytes() == png
    assert tex_jpg.read_bytes() == jpeg
    uris = ["textures/receiver_base_color.png", "textures/image_1.jpg"]
    for image, uri in zip(document["images"], uris):
        assert image["uri"] == uri
        assert "bufferView" not in image
    for key in (
        "asset",
        "scenes",
        "nodes",
        "meshes",
        "accessors",
        "bufferViews",
        "materials",
        "samplers",
        "textures",
    ):
        assert document[key] == original[key]


def test_no_images(tmp_path: Path) -> None:
    glb = tmp_path / "plain.glb"
    _build_glb(glb)
    out = tmp_path / "export"
    written = SeparateGltfExporter().convert(glb, out)

    assert written == [out / "plain.gltf", out / "plain.bin"]
    assert not (out / "textures").exists()
    document = json.loads(written[0].read_text(encoding="utf-8"))
    assert "images" not in document
    _, binary = _glb_parts(glb)
    assert written[1].read_bytes() == binary
    assert document["buffers"] == [{"uri": "plain.bin", "byteLength": len(binary)}]


@pytest.mark.parametrize(
    "corruption", ["magic", "version", "json-chunk", "bin-chunk", "truncated"]
)
def test_corrupted_glb_rejected(tmp_path: Path, corruption: str) -> None:
    glb = tmp_path / "scene.glb"
    _build_glb(glb)
    data = bytearray(glb.read_bytes())
    json_len = struct.unpack_from("<I", data, 12)[0]
    bin_header = 20 + json_len
    if corruption == "magic":
        data[0:4] = b"XXXX"
    elif corruption == "version":
        struct.pack_into("<I", data, 4, 1)
    elif corruption == "json-chunk":
        struct.pack_into("<I", data, 16, 0)
    elif corruption == "bin-chunk":
        struct.pack_into("<I", data, bin_header + 4, 0)
    else:
        del data[-4:]
    glb.write_bytes(bytes(data))

    out = tmp_path / "out"
    with pytest.raises(ValueError):
        SeparateGltfExporter().convert(glb, out)
    assert not out.exists()


@pytest.mark.parametrize(
    "mutation", ["length", "offset", "negative-offset", "bad-index", "foreign-buffer"]
)
def test_out_of_bounds_bufferview_rejected(tmp_path: Path, mutation: str) -> None:
    glb = tmp_path / "scene.glb"
    _build_glb(glb, [(_png_bytes((1, 2, 3)), "image/png")])
    document, binary = _glb_parts(glb)
    view = document["bufferViews"][document["images"][0]["bufferView"]]
    if mutation == "length":
        view["byteLength"] = len(binary) + 1
    elif mutation == "offset":
        view["byteOffset"] = len(binary)
        view["byteLength"] = 1
    elif mutation == "negative-offset":
        view["byteOffset"] = -4
    elif mutation == "bad-index":
        document["images"][0]["bufferView"] = 99
    else:
        view["buffer"] = 1
    glb.write_bytes(_pack_glb(document, binary))

    out = tmp_path / "out"
    with pytest.raises(ValueError):
        SeparateGltfExporter().convert(glb, out)
    assert not out.exists()


def test_texture_names_sanitized_and_unique(tmp_path: Path) -> None:
    glb = tmp_path / "scene.glb"
    png = _png_bytes((9, 9, 9))
    _build_glb(glb, [(png, "image/png")] * 3)
    document, binary = _glb_parts(glb)
    document["images"][0]["name"] = "../../etc/passwd"
    document["images"][1]["name"] = "etc passwd"
    document["images"][2]["name"] = "   "
    glb.write_bytes(_pack_glb(document, binary))

    out = tmp_path / "out"
    written = SeparateGltfExporter().convert(glb, out)
    textures = written[2:]
    assert [path.name for path in textures] == [
        "etc_passwd.png",
        "etc_passwd_1.png",
        "image_2.png",
    ]
    for path in textures:
        assert path.parent == out / "textures"
        assert path.read_bytes() == png


def test_unsupported_mime_rejected(tmp_path: Path) -> None:
    glb = tmp_path / "scene.glb"
    _build_glb(glb, [(b"webp-bytes", "image/webp")])
    with pytest.raises(ValueError, match="mimeType"):
        SeparateGltfExporter().convert(glb, tmp_path / "out")


def test_external_uri_image_untouched(tmp_path: Path) -> None:
    glb = tmp_path / "scene.glb"
    _build_glb(glb, [(_png_bytes((4, 5, 6)), "image/png")])
    document, binary = _glb_parts(glb)
    external = {"uri": "textures/external.png", "mimeType": "image/png"}
    document["images"][0] = external
    glb.write_bytes(_pack_glb(document, binary))

    out = tmp_path / "out"
    written = SeparateGltfExporter().convert(glb, out)
    assert written == [out / "scene.gltf", out / "scene.bin"]
    assert not (out / "textures").exists()
    result = json.loads(written[0].read_text(encoding="utf-8"))
    assert result["images"][0] == external


def test_protocol_conformance() -> None:
    exporter = SeparateGltfExporter()
    assert isinstance(exporter, Exporter)
    assert exporter.name == "gltf"
    assert exporter.extensions == (".gltf",)


def test_entry_point_declaration() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    entry_points = config["project"]["entry-points"]["wog_dump.exporters"]
    assert entry_points == {"gltf": "wog_plugin_gltf:SeparateGltfExporter"}
