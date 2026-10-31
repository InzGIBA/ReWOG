"""Integration tests: build a Blender scene from a real decrypted bundle.

Runs against ``runtime/decrypted/ak47.unity3d`` when present (skipped
otherwise) and validates the exported GLB end to end.
"""

from __future__ import annotations

import array
import json
import math
import struct
from pathlib import Path

import pytest

from wog_dump.core.config import get_config, set_config
from wog_dump.core.scene import SceneBuilder

REPO_ROOT = Path(__file__).resolve().parents[2]
AK47_BUNDLE = REPO_ROOT / "runtime" / "decrypted" / "ak47.unity3d"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not AK47_BUNDLE.exists(), reason="runtime/decrypted/ak47.unity3d not present"
    ),
]


def parse_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, version, total = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67
    assert version == 2
    assert total == len(data)
    json_len, _ = struct.unpack("<II", data[12:20])
    document = json.loads(data[20 : 20 + json_len])
    bin_offset = 20 + json_len
    bin_len, _ = struct.unpack("<II", data[bin_offset : bin_offset + 8])
    binary = data[bin_offset + 8 : bin_offset + 8 + bin_len]
    return document, binary


def read_accessor(document: dict, binary: bytes, accessor_index: int) -> array.array:
    accessor = document["accessors"][accessor_index]
    view = document["bufferViews"][accessor["bufferView"]]
    offset = view["byteOffset"]
    fmt = {5126: "f", 5123: "H", 5125: "I"}[accessor["componentType"]]
    return array.array(fmt, binary[offset : offset + view["byteLength"]])


def world_bounds(document: dict) -> list[list[float]]:
    def node_matrix(node: dict) -> list[list[float]]:
        t = node.get("translation", [0, 0, 0])
        r = node.get("rotation", [0, 0, 0, 1])
        s = node.get("scale", [1, 1, 1])
        x, y, z, w = r
        xx, yy, zz = x * x, y * y, z * z
        xy, xz, yz = x * y, x * z, y * z
        wx, wy, wz = w * x, w * y, w * z
        R = [
            1 - 2 * (yy + zz),
            2 * (xy + wz),
            2 * (xz - wy),
            2 * (xy - wz),
            1 - 2 * (xx + zz),
            2 * (yz + wx),
            2 * (xz + wy),
            2 * (yz - wx),
            1 - 2 * (xx + yy),
        ]
        return [
            [R[0] * s[0], R[1] * s[0], R[2] * s[0], t[0]],
            [R[3] * s[1], R[4] * s[1], R[5] * s[1], t[1]],
            [R[6] * s[2], R[7] * s[2], R[8] * s[2], t[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def matmul(a: list, b: list) -> list:
        return [
            [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)
        ]

    bounds = [[math.inf] * 3, [-math.inf] * 3]

    def walk(node_index: int, parent: list) -> None:
        node = document["nodes"][node_index]
        m = matmul(parent, node_matrix(node))
        if "mesh" in node:
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                accessor = document["accessors"][primitive["attributes"]["POSITION"]]
                mn, mx = accessor["min"], accessor["max"]
                for a in (mn[0], mx[0]):
                    for b in (mn[1], mx[1]):
                        for c in (mn[2], mx[2]):
                            world = [
                                m[0][0] * a + m[0][1] * b + m[0][2] * c + m[0][3],
                                m[1][0] * a + m[1][1] * b + m[1][2] * c + m[1][3],
                                m[2][0] * a + m[2][1] * b + m[2][2] * c + m[2][3],
                            ]
                            for axis in range(3):
                                bounds[0][axis] = min(bounds[0][axis], world[axis])
                                bounds[1][axis] = max(bounds[1][axis], world[axis])
        for child in node.get("children", []):
            walk(child, m)

    identity = [[1 if i == j else 0 for j in range(4)] for i in range(4)]
    for root_index in document["scenes"][0]["nodes"]:
        walk(root_index, identity)
    return bounds


@pytest.fixture(scope="module")
def built_scene(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output_dir = tmp_path_factory.mktemp("scene_ak47")
    config = get_config()
    set_config(
        base_dir=REPO_ROOT,
        decrypted_dir=REPO_ROOT / "runtime" / "decrypted",
    )
    builder = SceneBuilder(get_config())
    glb = builder.build_scene("ak47", output_dir=output_dir)
    assert glb.exists()
    return output_dir


class TestAk47Scene:
    def test_output_layout(self, built_scene: Path) -> None:
        assert (built_scene / "ak47.glb").exists()
        assert (built_scene / "scene.json").exists()
        assert (built_scene / "blender_setup.py").exists()
        assert (built_scene / "textures" / "ak47_1_hd.png").exists()
        assert (built_scene / "textures" / "ak47_4_hd.png").exists()

    def test_scene_counts(self, built_scene: Path) -> None:
        document, _ = parse_glb(built_scene / "ak47.glb")
        mesh_names = {mesh["name"] for mesh in document["meshes"]}
        # real weapon geometry only: no arrows, cutaway or VFX meshes
        assert len(document["meshes"]) == 173
        assert "receiver" in mesh_names
        assert "bolt_carrier" in mesh_names
        assert "custom_bayonet" in mesh_names
        assert not any(
            name.endswith("_in") or name.endswith("_out") for name in mesh_names
        )
        assert not any(name.endswith("_cut") for name in mesh_names)
        assert not any("muzzle" in name or "gas_anim" in name for name in mesh_names)

    def test_materials_and_textures(self, built_scene: Path) -> None:
        document, _ = parse_glb(built_scene / "ak47.glb")
        material_names = {m["name"] for m in document["materials"]}
        assert {"ak47_1_hd", "ak47_2_hd", "ak47_3_hd", "ak47_4_hd"} <= material_names
        assert "metal" in material_names
        # every textured material references an embedded texture
        for material in document["materials"]:
            pbr = material["pbrMetallicRoughness"]
            if "baseColorTexture" in pbr:
                texture_index = pbr["baseColorTexture"]["index"]
                image_index = document["textures"][texture_index]["source"]
                assert document["images"][image_index]["mimeType"] == "image/png"

    def test_every_primitive_has_valid_indices_and_bounds(
        self, built_scene: Path
    ) -> None:
        document, binary = parse_glb(built_scene / "ak47.glb")
        for mesh in document["meshes"]:
            for primitive in mesh["primitives"]:
                positions = read_accessor(
                    document, binary, primitive["attributes"]["POSITION"]
                )
                indices = read_accessor(document, binary, primitive["indices"])
                vertex_count = len(positions) // 3
                assert len(indices) % 3 == 0
                assert max(indices) < vertex_count
                accessor = document["accessors"][primitive["attributes"]["POSITION"]]
                assert accessor["min"][0] == pytest.approx(
                    min(positions[0::3]), abs=1e-4
                )

    def test_assembled_weapon_dimensions(self, built_scene: Path) -> None:
        # An assembled AK-47 with bayonet is roughly 1.3-1.5 m long; the
        # scene is in meters. Scattered parts would blow these bounds up.
        document, _ = parse_glb(built_scene / "ak47.glb")
        bounds = world_bounds(document)
        size = [bounds[1][i] - bounds[0][i] for i in range(3)]
        assert 1.0 < size[0] < 1.8, f"length {size[0]} not weapon-like"
        assert 0.1 < size[1] < 0.7, f"height {size[1]} not weapon-like"
        assert 0.1 < size[2] < 0.7, f"width {size[2]} not weapon-like"

    def test_camera_and_lights_present(self, built_scene: Path) -> None:
        document, _ = parse_glb(built_scene / "ak47.glb")
        assert len(document["cameras"]) == 1
        lights = document["extensions"]["KHR_lights_punctual"]["lights"]
        assert len(lights) == 3
        assert all(light["type"] == "directional" for light in lights)

        # KHR_lights_punctual node references must be extension objects
        # ({"light": i}), not bare indices - strict parsers such as
        # Blender's gltf_from_dict reject the latter
        light_nodes = [n for n in document["nodes"] if "extensions" in n]
        assert len(light_nodes) == 3
        for node in light_nodes:
            extension = node["extensions"]["KHR_lights_punctual"]
            assert isinstance(extension, dict)
            assert isinstance(extension["light"], int)

    def test_manifest(self, built_scene: Path) -> None:
        manifest = json.loads((built_scene / "scene.json").read_text())
        assert manifest["weapon"] == "ak47"
        assert manifest["units"] == "meters"
        assert manifest["meshes"] == 173
        assert manifest["skipped"]["arrows"] == 102
        assert manifest["skipped"]["cut"] == 14
        assert "magazine" in manifest["parts"]
        assert "magazine" in manifest["field_strip"]
