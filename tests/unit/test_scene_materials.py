"""Unit tests for PBR material conversion (Unity slots -> glTF channels)."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

from wog_dump.core.glb import GLBWriter
from wog_dump.core.scene import (
    SceneBuilder,
    SceneStats,
    _metallic_roughness_png,
    _occlusion_png,
)


def fake_texture(name: str, color: tuple[int, int, int, int]) -> Any:
    return SimpleNamespace(m_Name=name, image=Image.new("RGBA", (4, 4), color))


def fake_material(
    name: str,
    textures: dict[str, int],
    floats: dict[str, float] | None = None,
) -> Any:
    return SimpleNamespace(
        m_Name=name,
        m_SavedProperties=SimpleNamespace(
            m_Colors=[("_Color", SimpleNamespace(r=1.0, g=1.0, b=1.0, a=1.0))],
            m_Floats=list((floats or {}).items()),
            m_TexEnvs=[
                (slot, SimpleNamespace(m_Texture=SimpleNamespace(path_id=pid)))
                for slot, pid in textures.items()
            ],
        ),
    )


def convert(
    tmp_path: Path, materials: dict[int, Any], textures: dict[int, Any]
) -> tuple[GLBWriter, dict[int, int], SceneStats]:
    builder = SceneBuilder()
    builder.config = builder.config.model_copy(update={"base_dir": tmp_path})
    writer = GLBWriter(generator="unit-test")
    stats = SceneStats(weapon="test")
    mapping = builder._convert_materials(writer, materials, textures, tmp_path, stats)
    return writer, mapping, stats


class TestChannelConversion:
    def test_metallic_gloss_to_gltf_channels(self) -> None:
        texture = fake_texture("mg", (200, 50, 60, 100))
        data = _metallic_roughness_png(texture, metallic_from_red=True)
        pixel = Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))
        assert pixel == (255, 155, 200)

    def test_spec_gloss_stays_dielectric(self) -> None:
        texture = fake_texture("sg", (200, 50, 60, 100))
        data = _metallic_roughness_png(texture, metallic_from_red=False)
        pixel = Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))
        assert pixel == (255, 155, 0)

    def test_occlusion_green_to_red(self) -> None:
        texture = fake_texture("ao", (10, 123, 200, 255))
        data = _occlusion_png(texture)
        pixel = Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))
        assert pixel[0] == 123


class TestConvertMaterials:
    def test_full_pbr_slots_bound(self, tmp_path: Path) -> None:
        textures = {
            1: fake_texture("albedo", (120, 120, 120, 255)),
            2: fake_texture("normals", (128, 128, 255, 255)),
            3: fake_texture("metalgloss", (220, 0, 0, 80)),
            4: fake_texture("occlusion", (0, 200, 0, 255)),
        }
        material = fake_material(
            "steel",
            {
                "_MainTex": 1,
                "_BumpMap": 2,
                "_MetallicGlossMap": 3,
                "_OcclusionMap": 4,
            },
            floats={"_BumpScale": 0.5},
        )
        writer, mapping, stats = convert(tmp_path, {10: material}, textures)
        document = writer._build_document()
        pbr = document["materials"][0]["pbrMetallicRoughness"]

        assert mapping == {10: 0}
        assert pbr["baseColorTexture"]["index"] == 0
        assert pbr["metallicRoughnessTexture"]["index"] == 2
        assert pbr["metallicFactor"] == 1.0
        assert pbr["roughnessFactor"] == 1.0
        normal = document["materials"][0]["normalTexture"]
        assert normal == {"index": 1, "scale": 0.5}
        assert document["materials"][0]["occlusionTexture"]["index"] == 3
        assert stats.textures == 4
        assert (tmp_path / "textures" / "metalgloss_mr.png").is_file()
        assert (tmp_path / "textures" / "occlusion_occ.png").is_file()

    def test_spec_gloss_fallback(self, tmp_path: Path) -> None:
        textures = {1: fake_texture("specgloss", (200, 200, 200, 40))}
        material = fake_material("chrome", {"_SpecGlossMap": 1})
        writer, _, stats = convert(tmp_path, {10: material}, textures)
        pbr = writer._build_document()["materials"][0]["pbrMetallicRoughness"]
        assert "metallicRoughnessTexture" in pbr
        assert stats.textures == 1

    def test_glossiness_and_metallic_factors(self, tmp_path: Path) -> None:
        material = fake_material(
            "paint", {}, floats={"_Glossiness": 0.8, "_Metallic": 0.9}
        )
        writer, _, _ = convert(tmp_path, {10: material}, {})
        pbr = writer._build_document()["materials"][0]["pbrMetallicRoughness"]
        assert pbr["metallicFactor"] == 0.9
        assert abs(pbr["roughnessFactor"] - 0.2) < 1e-9

    def test_shininess_fallback_without_glossiness(self, tmp_path: Path) -> None:
        material = fake_material("legacy", {}, floats={"_Shininess": 0.25})
        writer, _, _ = convert(tmp_path, {10: material}, {})
        pbr = writer._build_document()["materials"][0]["pbrMetallicRoughness"]
        assert pbr["roughnessFactor"] == 0.5
        assert pbr["metallicFactor"] == 0.0

    def test_shared_texture_embedded_once(self, tmp_path: Path) -> None:
        textures = {1: fake_texture("shared", (10, 20, 30, 255))}
        materials = {
            10: fake_material("a", {"_MainTex": 1}),
            11: fake_material("b", {"_MainTex": 1}),
        }
        writer, mapping, stats = convert(tmp_path, materials, textures)
        assert len(mapping) == 2
        assert stats.textures == 1
        assert len(writer._build_document()["images"]) == 1

    def test_unsafe_texture_name_skips_material(self, tmp_path: Path) -> None:
        textures = {1: fake_texture("../evil", (10, 20, 30, 255))}
        material = fake_material("bad", {"_MainTex": 1})
        _, mapping, stats = convert(tmp_path, {10: material}, textures)
        assert mapping == {}
        assert stats.skipped["unsafe_texture_name"] == 1

    def test_missing_texture_pid_ignored(self, tmp_path: Path) -> None:
        material = fake_material("dangling", {"_MainTex": 99, "_BumpMap": 98})
        writer, mapping, stats = convert(tmp_path, {10: material}, {})
        assert mapping == {10: 0}
        assert stats.textures == 0
        pbr = writer._build_document()["materials"][0]["pbrMetallicRoughness"]
        assert "baseColorTexture" not in pbr


class TestGLBMaterialDocument:
    def test_pbr_textures_serialized(self, tmp_path: Path) -> None:
        writer = GLBWriter()
        base = writer.add_texture(writer.add_image(b"base"))
        normal = writer.add_texture(writer.add_image(b"normal"))
        mr = writer.add_texture(writer.add_image(b"mr"))
        occ = writer.add_texture(writer.add_image(b"occ"))
        writer.add_material(
            "full",
            base_color_texture=base,
            normal_texture=normal,
            normal_scale=0.75,
            metallic_roughness_texture=mr,
            occlusion_texture=occ,
            occlusion_strength=0.5,
        )
        document = writer._build_document()
        material = document["materials"][0]
        assert material["normalTexture"] == {"index": normal, "scale": 0.75}
        assert material["occlusionTexture"] == {"index": occ, "strength": 0.5}
        pbr = material["pbrMetallicRoughness"]
        assert pbr["metallicRoughnessTexture"] == {"index": mr}

    def test_defaults_omit_optional_channels(self) -> None:
        writer = GLBWriter()
        writer.add_material("plain")
        material = writer._build_document()["materials"][0]
        assert "normalTexture" not in material
        assert "occlusionTexture" not in material
        assert "metallicRoughnessTexture" not in material["pbrMetallicRoughness"]
