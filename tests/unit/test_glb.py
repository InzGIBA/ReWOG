"""Unit tests for the glTF 2.0 / GLB writer."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from wog_dump.core.glb import GLBWriter, MeshPrimitive, SceneError


def parse_glb(path: Path) -> tuple[dict, bytes]:
    """Parse a GLB file into (json document, binary chunk)."""
    data = path.read_bytes()
    magic, version, total = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67
    assert version == 2
    assert total == len(data)

    json_len, json_type = struct.unpack("<II", data[12:20])
    assert json_type == 0x4E4F534A
    document = json.loads(data[20 : 20 + json_len])

    bin_offset = 20 + json_len
    bin_len, bin_type = struct.unpack("<II", data[bin_offset : bin_offset + 8])
    assert bin_type == 0x004E4942
    binary = data[bin_offset + 8 : bin_offset + 8 + bin_len]
    return document, binary


def make_writer_scene() -> GLBWriter:
    writer = GLBWriter(generator="unit-test")
    # 1x1 quad in the XY plane, two primitives (two material slots)
    quad = MeshPrimitive(
        positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0],
        normals=[0.0, 0.0, 1.0] * 4,
        uvs=[0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
        indices=[0, 1, 2, 0, 2, 3],
        material=0,
    )
    dark = MeshPrimitive(
        positions=[0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0],
        indices=[0, 1, 2, 0, 2, 3],
        material=1,
    )
    mesh = writer.add_mesh("quad", [quad, dark])

    factor_material = writer.add_material(
        name="flat", base_color_factor=(1.0, 0.0, 0.0, 1.0), roughness=0.25
    )
    image = writer.add_image(b"\x89PNG-fake-bytes")
    texture = writer.add_texture(image)
    textured = writer.add_material(
        name="textured", base_color_texture=texture, metallic=0.1
    )
    assert (factor_material, textured) == (0, 1)

    root = writer.add_node("root", translation=(0.5, 0.0, 0.0))
    writer.add_node("child", mesh=mesh, translation=(1.0, 2.0, 3.0), parent=root)

    camera = writer.add_camera("cam")
    writer.add_camera_node("cam_node", camera, (0.0, 0.0, 5.0), (0.0, 0.0, 0.0))
    light = writer.add_directional_light("sun", (1.0, 1.0, 1.0), 3.0)
    writer.add_light_node("sun_node", light, (0.0, 5.0, 0.0), (0.0, 0.0, 0.0))
    return writer


class TestGLBWriter:
    def test_write_and_parse_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, binary = parse_glb(path)

        assert document["asset"]["version"] == "2.0"
        assert document["asset"]["generator"] == "unit-test"
        assert len(document["meshes"]) == 1
        assert len(document["meshes"][0]["primitives"]) == 2
        assert len(document["materials"]) == 2
        assert len(document["images"]) == 1
        assert "KHR_lights_punctual" in document["extensionsUsed"]
        assert len(document["cameras"]) == 1

    def test_node_hierarchy(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, _ = parse_glb(path)

        root_index = document["scenes"][0]["nodes"][0]
        root = document["nodes"][root_index]
        assert root["name"] == "root"
        assert root["translation"] == [0.5, 0.0, 0.0]

        child_index = root["children"][0]
        child = document["nodes"][child_index]
        assert child["mesh"] == 0
        assert child["translation"] == [1.0, 2.0, 3.0]

    def test_accessor_counts_and_bounds(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, _ = parse_glb(path)

        primitive = document["meshes"][0]["primitives"][0]
        position_accessor = document["accessors"][primitive["attributes"]["POSITION"]]
        assert position_accessor["count"] == 4
        assert position_accessor["type"] == "VEC3"
        assert position_accessor["componentType"] == 5126
        assert position_accessor["min"] == [0.0, 0.0, 0.0]
        assert position_accessor["max"] == [1.0, 1.0, 0.0]

        indices_accessor = document["accessors"][primitive["indices"]]
        assert indices_accessor["count"] == 6
        assert indices_accessor["componentType"] == 5123  # small mesh -> uint16

    def test_uint32_indices_for_large_meshes(self, tmp_path: Path) -> None:
        writer = GLBWriter()
        vertex_count = 70000
        positions = []
        for i in range(vertex_count):
            positions.extend((float(i), 0.0, 0.0))
        indices = [0, 1, vertex_count - 1]
        writer.add_mesh("big", [MeshPrimitive(positions=positions, indices=indices)])
        path = tmp_path / "big.glb"
        writer.write(path)
        document, _ = parse_glb(path)

        primitive = document["meshes"][0]["primitives"][0]
        indices_accessor = document["accessors"][primitive["indices"]]
        assert indices_accessor["componentType"] == 5125

    def test_material_factor_converted_to_linear(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, _ = parse_glb(path)

        flat = document["materials"][0]
        factor = flat["pbrMetallicRoughness"]["baseColorFactor"]
        assert factor[0] == pytest.approx(1.0)
        assert factor[1] == pytest.approx(0.0)
        assert flat["pbrMetallicRoughness"]["roughnessFactor"] == 0.25

        textured = document["materials"][1]
        pbr = textured["pbrMetallicRoughness"]
        assert pbr["baseColorTexture"]["index"] == 0
        assert "baseColorFactor" not in pbr
        assert pbr["metallicFactor"] == 0.1

    def test_image_bytes_embedded(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, binary = parse_glb(path)

        image = document["images"][0]
        assert image["mimeType"] == "image/png"
        view = document["bufferViews"][image["bufferView"]]
        chunk = binary[view["byteOffset"] : view["byteOffset"] + view["byteLength"]]
        assert chunk == b"\x89PNG-fake-bytes"

    def test_buffer_views_within_buffer_and_aligned(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, binary = parse_glb(path)

        buffer_length = document["buffers"][0]["byteLength"]
        # The BIN chunk is padded to 4 bytes; the declared buffer length
        # covers only the actual data
        assert buffer_length <= len(binary) < buffer_length + 4
        for view in document["bufferViews"]:
            assert view["byteOffset"] % 4 == 0
            assert view["byteOffset"] + view["byteLength"] <= buffer_length

    def test_empty_mesh_rejected(self) -> None:
        writer = GLBWriter()
        with pytest.raises(SceneError):
            writer.add_mesh("empty", [])

    def test_camera_node_looks_at_target(self, tmp_path: Path) -> None:
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, _ = parse_glb(path)

        camera_nodes = [n for n in document["nodes"] if n.get("camera") is not None]
        assert len(camera_nodes) == 1
        node = camera_nodes[0]
        assert node["translation"] == [0.0, 0.0, 5.0]
        # Camera at +Z looking at the origin: identity orientation is
        # already correct (local -Z towards the target)
        assert node["rotation"] == pytest.approx([0.0, 0.0, 0.0, 1.0], abs=1e-6)

    def test_light_node_extension_object_shape(self, tmp_path: Path) -> None:
        # KHR_lights_punctual requires the node extension value to be an
        # object ({"light": i}); a bare index fails strict parsers such
        # as Blender's gltf_from_dict ("Couldn't parse glTF")
        path = tmp_path / "scene.glb"
        make_writer_scene().write(path)
        document, _ = parse_glb(path)

        light_nodes = [n for n in document["nodes"] if "extensions" in n]
        assert len(light_nodes) == 1
        extension = light_nodes[0]["extensions"]["KHR_lights_punctual"]
        assert isinstance(extension, dict)
        assert extension == {"light": 0}
