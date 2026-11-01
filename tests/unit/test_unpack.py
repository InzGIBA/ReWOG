"""Unit tests for the unpack module (extractors and processors)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from wog_dump.core.config import WOGConfig
from wog_dump.core.unpack import (
    EXTRACTORS,
    AssetUnpacker,
    UnpackError,
    WeaponListProcessor,
)


def make_unity_object(obj_type: str, data: object) -> Mock:
    """Build a mock UnityPy object of the given type wrapping `data`."""
    obj = Mock()
    obj.type.name = obj_type
    obj.read.return_value = data
    obj.path_id = 1
    return obj


def make_env(objects: list[Mock]) -> Mock:
    env = Mock()
    env.objects = objects
    return env


def make_swizzled_normal(size: int = 8) -> Image.Image:
    """Synthesize an image with the Unity swizzled normal map signature:
    red forced white, X in alpha, Y in green, both forming unit normals."""
    img = Image.new("RGBA", (size, size))
    img.putdata(
        [
            (
                255,
                round(127.5 * (1 + 0.9 * math.cos((x * 3 + y) * 0.7))),
                0,
                round(127.5 * (1 + 0.9 * math.sin((x * 3 + y) * 0.7))),
            )
            for y in range(size)
            for x in range(size)
        ]
    )
    return img


class TestExtractors:
    """Tests for the per-type extractor classes."""

    def test_registry_covers_supported_types(self) -> None:
        assert set(EXTRACTORS) == {
            "TextAsset",
            "Texture2D",
            "Mesh",
            "Material",
            "AnimationClip",
            "AudioClip",
        }

    def test_text_asset_plain(self, tmp_path: Path) -> None:
        data = Mock(m_Name="notes", m_Script="plain text")
        result = EXTRACTORS["TextAsset"].extract(
            make_unity_object("TextAsset", data), tmp_path
        )

        assert result == tmp_path / "notes.txt"
        assert result.read_text(encoding="utf-8") == "plain text"

    def test_text_asset_json_detection(self, tmp_path: Path) -> None:
        data = Mock(m_Name="config", m_Script='{"key": 1}')
        result = EXTRACTORS["TextAsset"].extract(
            make_unity_object("TextAsset", data), tmp_path
        )

        assert result == tmp_path / "config.json"

    def test_text_asset_xml_detection(self, tmp_path: Path) -> None:
        data = Mock(m_Name="layout", m_Script="<root></root>")
        result = EXTRACTORS["TextAsset"].extract(
            make_unity_object("TextAsset", data), tmp_path
        )

        assert result == tmp_path / "layout.xml"

    def test_text_asset_binary(self, tmp_path: Path) -> None:
        payload = bytes(range(256))  # Not valid UTF-8
        data = Mock(m_Name="blob", m_Script=payload)
        result = EXTRACTORS["TextAsset"].extract(
            make_unity_object("TextAsset", data), tmp_path
        )

        assert result == tmp_path / "blob.bytes"
        assert result.read_bytes() == payload

    def test_text_asset_unnamed_skipped(self, tmp_path: Path) -> None:
        data = Mock(m_Name="", m_Script="content")
        assert (
            EXTRACTORS["TextAsset"].extract(
                make_unity_object("TextAsset", data), tmp_path
            )
            is None
        )

    def test_texture_plain_rgba_preserved(self, tmp_path: Path) -> None:
        img = Image.new("RGBA", (8, 8))
        img.putdata(
            [
                ((x * 30) % 256, (y * 30) % 256, 40, 255)
                for y in range(8)
                for x in range(8)
            ]
        )
        data = Mock(m_Name="skin", image=img)
        result = EXTRACTORS["Texture2D"].extract(
            make_unity_object("Texture2D", data), tmp_path
        )

        assert result == tmp_path / "skin.png"
        assert Image.open(result).mode == "RGBA"

    def test_texture_swizzled_normal_unswizzled(self, tmp_path: Path) -> None:
        """Swizzled normal maps are exported opaque, with X moved out of alpha."""
        source = make_swizzled_normal()
        data = Mock(m_Name="gas", image=source)
        result = EXTRACTORS["Texture2D"].extract(
            make_unity_object("Texture2D", data), tmp_path
        )

        saved = Image.open(result)
        assert saved.mode == "RGB"
        # X component (from alpha) now lives in red, Y (green) unchanged
        assert saved.getpixel((4, 2))[0] == source.getpixel((4, 2))[3]
        assert saved.getpixel((4, 2))[1] == source.getpixel((4, 2))[1]

    def test_texture_without_image_skipped(self, tmp_path: Path) -> None:
        data = Mock(m_Name="skin", image=None)
        assert (
            EXTRACTORS["Texture2D"].extract(
                make_unity_object("Texture2D", data), tmp_path
            )
            is None
        )

    def test_mesh_uses_unitypy_exporter(self, tmp_path: Path) -> None:
        """Vertices live in packed m_VertexData on Unity 6, so the OBJ text
        comes from UnityPy's mesh exporter."""
        data = Mock(m_Name="gun_mesh")
        obj_text = "g gun_mesh\nv 1.0 2.0 3.0\nvt 0.5 0.5\nf 1 1 1\n"

        with patch(
            "wog_dump.core.unpack.MeshExporter.export_mesh_obj", return_value=obj_text
        ) as mock_export:
            result = EXTRACTORS["Mesh"].extract(
                make_unity_object("Mesh", data), tmp_path
            )

        mock_export.assert_called_once_with(data)
        assert result == tmp_path / "gun_mesh.obj"
        assert result.read_text() == obj_text

    def test_mesh_legacy_fallback_when_exporter_fails(self, tmp_path: Path) -> None:
        vertex = SimpleNamespace(x=1.0, y=2.0, z=3.0)
        uv = SimpleNamespace(x=0.5, y=0.5)
        normal = SimpleNamespace(x=0.0, y=1.0, z=0.0)
        data = Mock(
            m_Name="gun_mesh",
            m_Vertices=[vertex],
            m_UV=[uv],
            m_Normals=[normal],
            m_Triangles=[0, 1, 2],
        )

        with patch(
            "wog_dump.core.unpack.MeshExporter.export_mesh_obj",
            side_effect=RuntimeError("unsupported vertex format"),
        ):
            result = EXTRACTORS["Mesh"].extract(
                make_unity_object("Mesh", data), tmp_path
            )

        assert result == tmp_path / "gun_mesh.obj"
        content = result.read_text()
        assert "v 1.0 2.0 3.0" in content
        assert "vt 0.5 0.5" in content
        assert "vn 0.0 1.0 0.0" in content
        assert "f 1 2 3" in content

    def test_mesh_legacy_fallback_when_exporter_output_empty(
        self, tmp_path: Path
    ) -> None:
        vertex = SimpleNamespace(x=1.0, y=2.0, z=3.0)
        data = Mock(
            m_Name="gun_mesh",
            m_Vertices=[vertex],
            m_UV=[],
            m_Normals=[],
            m_Triangles=[],
        )

        with patch(
            "wog_dump.core.unpack.MeshExporter.export_mesh_obj", return_value=""
        ):
            result = EXTRACTORS["Mesh"].extract(
                make_unity_object("Mesh", data), tmp_path
            )

        assert result == tmp_path / "gun_mesh.obj"
        assert "v 1.0 2.0 3.0" in result.read_text()

    def test_mesh_without_vertex_data_skipped(self, tmp_path: Path) -> None:
        data = SimpleNamespace(m_Name="empty")

        with patch(
            "wog_dump.core.unpack.MeshExporter.export_mesh_obj",
            side_effect=RuntimeError("no vertex data"),
        ):
            assert (
                EXTRACTORS["Mesh"].extract(make_unity_object("Mesh", data), tmp_path)
                is None
            )

    def test_material(self, tmp_path: Path) -> None:
        props = Mock(
            m_Colors=[{"first": "_Color", "second": {"r": 1, "g": 0, "b": 0, "a": 1}}],
            m_Floats=[{"first": "_Gloss", "second": 0.5}],
            m_TexEnvs=[
                {"first": "_MainTex", "second": {"m_Texture": {"m_Name": "tex"}}}
            ],
        )
        data = Mock(
            m_Name="mat", m_Shader={"m_Name": "Standard"}, m_SavedProperties=props
        )
        result = EXTRACTORS["Material"].extract(
            make_unity_object("Material", data), tmp_path
        )

        assert result == tmp_path / "mat.material.json"
        payload = json.loads(result.read_text())
        assert payload["shader"] == "Standard"
        assert payload["properties"]["_Color"] == {
            "type": "color",
            "value": [1, 0, 0, 1],
        }
        assert payload["properties"]["_Gloss"] == {"type": "float", "value": 0.5}
        assert payload["properties"]["_MainTex"]["texture_name"] == "tex"

    def test_material_typed_unitypy_objects(self, tmp_path: Path) -> None:
        """Current UnityPy returns typed objects: (name, value) tuples,
        ColorRGBA and PPtr - the extractor must not require dict access."""
        tex_env = SimpleNamespace(m_Texture=SimpleNamespace(m_PathID=0))
        tex_env.m_Texture.deref = lambda: (_ for _ in ()).throw(RuntimeError("no"))
        props = SimpleNamespace(
            m_Colors=[("_Color", SimpleNamespace(r=1, g=0, b=0, a=1))],
            m_Floats=[("_Gloss", 0.5)],
            m_TexEnvs=[("_MainTex", tex_env)],
        )
        shader = SimpleNamespace(m_PathID=-808, m_FileID=0)
        shader.deref = lambda: SimpleNamespace()  # no name resolvable
        data = SimpleNamespace(m_Name="mat2", m_Shader=shader, m_SavedProperties=props)

        result = EXTRACTORS["Material"].extract(
            make_unity_object("Material", data), tmp_path
        )

        assert result == tmp_path / "mat2.material.json"
        payload = json.loads(result.read_text())
        assert payload["shader"] == "PPtr(-808)"
        assert payload["properties"]["_Color"] == {
            "type": "color",
            "value": [1, 0, 0, 1],
        }
        assert payload["properties"]["_MainTex"]["texture_name"] == "None"

    def test_animation_clip(self, tmp_path: Path) -> None:
        data = Mock(m_Name="reload", m_Length=2.5, m_FrameRate=30, m_Legacy=False)
        EXTRACTORS["AnimationClip"].extract(
            make_unity_object("AnimationClip", data), tmp_path
        )

        payload = json.loads((tmp_path / "reload.anim.json").read_text())
        assert payload == {
            "name": "reload",
            "length": 2.5,
            "frame_rate": 30,
            "legacy": False,
        }

    def test_audio_clip(self, tmp_path: Path) -> None:
        data = Mock(m_Name="shot", m_Format=1, m_Frequency=44100, m_Channels=2)
        EXTRACTORS["AudioClip"].extract(make_unity_object("AudioClip", data), tmp_path)

        payload = json.loads((tmp_path / "shot.audio.json").read_text())
        assert payload["frequency"] == 44100

    def test_extractor_error_returns_none(self, tmp_path: Path) -> None:
        """A failing read() is contained instead of propagating."""
        obj = Mock()
        obj.type.name = "TextAsset"
        obj.read.side_effect = RuntimeError("boom")

        assert EXTRACTORS["TextAsset"].extract(obj, tmp_path) is None


class TestWeaponListProcessor:
    """Tests for the weapon list processor."""

    def test_parse_weapon_names(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)
        content = "ak74.png\r\nm4a1.png\n# comment\nglock17\nbad/name\n\n"

        names = processor._parse_weapon_names(content)

        assert names == ["ak74", "m4a1", "glock17"]

    def test_filter_weapons(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)
        # "wolf" is in the default blacklist; "a" is too short
        filtered = processor._filter_weapons(["ak74", "wolf", "a"])

        assert filtered == ["ak74"]

    def test_filter_weapons_too_long(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)
        long_name = "x" * 51
        assert processor._filter_weapons([long_name]) == []

    def test_extract_weapon_list(
        self, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        processor = WeaponListProcessor(test_config)
        banners = Mock(m_Name="new_banners", m_Script=b"ak74.png\r\nm4a1.png\n")

        with patch("wog_dump.core.unpack.UnityPy.load") as mock_load:
            mock_load.return_value = make_env([make_unity_object("TextAsset", banners)])
            weapons = processor.extract_weapon_list(mock_unity_asset)

        assert "ak74" in weapons
        assert "m4a1" in weapons

    def test_extract_weapon_list_missing_file(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)

        with pytest.raises(UnpackError, match="not found"):
            processor.extract_weapon_list(Path("/nonexistent.unity3d"))

    def test_extract_weapon_list_no_banners(
        self, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        processor = WeaponListProcessor(test_config)
        other = Mock(m_Name="something_else", m_Script=b"ak74.png\n")

        with patch("wog_dump.core.unpack.UnityPy.load") as mock_load:
            mock_load.return_value = make_env([make_unity_object("TextAsset", other)])

            with pytest.raises(UnpackError, match="new_banners"):
                processor.extract_weapon_list(mock_unity_asset)

    def test_extract_weapon_list_atlas_format(
        self, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        """The server's banner asset may be a texture-atlas index (tai):

        ``<name>.png<tab><tab><atlas info>`` - the weapon name is the
        first whitespace-separated token of each line.
        """
        processor = WeaponListProcessor(test_config)
        banners = Mock(
            m_Name="new_banners",
            m_Script=(
                b"# new_banners.tai\r\n"
                b"# comment line\r\n"
                b"ak47.png\t\tnew_banners0.dds, 0, 2D, 0.25, 0, 0, 0.25, 0.125\r\n"
                b"m4a1.png\t\tnew_banners0.dds, 0, 2D, 0.5, 0, 0, 0.25, 0.125\r\n"
            ),
        )

        with patch("wog_dump.core.unpack.UnityPy.load") as mock_load:
            mock_load.return_value = make_env([make_unity_object("TextAsset", banners)])
            weapons = processor.extract_weapon_list(mock_unity_asset)

        assert weapons == ["ak47", "m4a1"]

    def test_extract_weapon_list_latin1_fallback(
        self, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        """Non-UTF-8 banner content is decoded via latin-1 instead of crashing."""
        processor = WeaponListProcessor(test_config)
        banners = Mock(m_Name="new_banners", m_Script=b"ak74.png\nak\xff\xfepng\n\xff")

        with patch("wog_dump.core.unpack.UnityPy.load") as mock_load:
            mock_load.return_value = make_env([make_unity_object("TextAsset", banners)])
            weapons = processor.extract_weapon_list(mock_unity_asset)

        # The decodable, allowlist-matching name survives; the mojibake
        # line is decoded without crashing and then rejected by the
        # strict weapon-name validation
        assert weapons == ["ak74"]

    def test_save_and_load_weapon_list(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)

        processor.save_weapon_list(["ak74", "m4a1"])
        assert processor.load_weapon_list() == ["ak74", "m4a1"]

    def test_save_empty_weapon_list_rejected(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)

        with pytest.raises(ValueError):
            processor.save_weapon_list([])

    def test_load_weapon_list_missing(self, test_config: WOGConfig) -> None:
        processor = WeaponListProcessor(test_config)

        with pytest.raises(UnpackError):
            processor.load_weapon_list(try_migration=False)

    def test_process_weapon_list_asset(
        self, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        processor = WeaponListProcessor(test_config)
        banners = Mock(m_Name="new_banners", m_Script=b"ak74.png\n")

        with patch("wog_dump.core.unpack.UnityPy.load") as mock_load:
            mock_load.return_value = make_env([make_unity_object("TextAsset", banners)])
            weapons = processor.process_weapon_list_asset(mock_unity_asset)

        assert weapons == ["ak74"]
        assert processor.db.get_weapons() == ["ak74"]


class TestAssetUnpacker:
    """Tests for the asset unpacker orchestration."""

    def test_unpack_missing_file(self, test_config: WOGConfig) -> None:
        unpacker = AssetUnpacker(test_config)

        with pytest.raises(UnpackError, match="not found"):
            unpacker.unpack_asset(Path("/nonexistent.unity3d"))

    @patch("wog_dump.core.unpack.UnityPy.load")
    def test_unpack_asset_mixed_objects(
        self, mock_load: Mock, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        text = Mock(m_Name="notes", m_Script="hello")
        image = Image.new("RGB", (2, 2), (10, 20, 30))
        texture = Mock(m_Name="skin", image=image)
        unsupported = Mock()
        unsupported.type.name = "Transform"

        mock_load.return_value = make_env(
            [
                make_unity_object("TextAsset", text),
                make_unity_object("Texture2D", texture),
                unsupported,
            ]
        )

        unpacker = AssetUnpacker(test_config)
        output_dir = test_config.assets_dir / "out"
        extracted = unpacker.unpack_asset(mock_unity_asset, output_dir)

        names = {p.name for p in extracted}
        assert names == {"notes.txt", "skin.png"}
        stats = unpacker.get_extraction_stats()
        assert stats["objects_processed"] == 3
        assert stats["objects_extracted"] == 2
        assert stats["objects_skipped"] == 1

    @patch("wog_dump.core.unpack.UnityPy.load")
    def test_unpack_asset_type_filter(
        self, mock_load: Mock, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        text = Mock(m_Name="notes", m_Script="hello")
        texture = Mock(m_Name="skin", image=Image.new("RGB", (2, 2), (1, 2, 3)))
        mock_load.return_value = make_env(
            [
                make_unity_object("TextAsset", text),
                make_unity_object("Texture2D", texture),
            ]
        )

        unpacker = AssetUnpacker(test_config)
        extracted = unpacker.unpack_asset(
            mock_unity_asset,
            test_config.assets_dir / "filtered",
            extract_types=["Texture2D"],
        )

        assert [p.name for p in extracted] == ["skin.png"]

    @patch("wog_dump.core.unpack.UnityPy.load")
    def test_unpack_asset_failure_wrapped(
        self, mock_load: Mock, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        mock_load.side_effect = RuntimeError("corrupt bundle")
        unpacker = AssetUnpacker(test_config)

        from wog_dump.core.unpack import AssetProcessingError

        with pytest.raises(AssetProcessingError, match="corrupt bundle"):
            unpacker.unpack_asset(mock_unity_asset)

    @patch("wog_dump.core.unpack.UnityPy.load")
    def test_unpack_multiple_assets_sequential(
        self, mock_load: Mock, test_config: WOGConfig, temp_dir: Path
    ) -> None:
        assets = []
        for i in range(3):
            asset = temp_dir / f"weapon{i}.unity3d"
            asset.write_bytes(b"UnityFS")
            assets.append(asset)

        text = Mock(m_Name="notes", m_Script="hello")
        mock_load.return_value = make_env([make_unity_object("TextAsset", text)])

        unpacker = AssetUnpacker(test_config)
        results = unpacker.unpack_multiple_assets(
            assets, output_dir=temp_dir / "unpacked"
        )

        assert set(results) == set(assets)
        assert all(len(files) == 1 for files in results.values())

    @patch("wog_dump.core.unpack.UnityPy.load")
    def test_unpack_multiple_assets_parallel(
        self, mock_load: Mock, test_config: WOGConfig, temp_dir: Path
    ) -> None:
        assets = []
        for i in range(8):  # > 5 triggers the parallel path
            asset = temp_dir / f"weapon{i}.unity3d"
            asset.write_bytes(b"UnityFS")
            assets.append(asset)

        text = Mock(m_Name="notes", m_Script="hello")
        mock_load.return_value = make_env([make_unity_object("TextAsset", text)])

        unpacker = AssetUnpacker(test_config)
        results = unpacker.unpack_multiple_assets(
            assets, output_dir=temp_dir / "unpacked"
        )

        assert set(results) == set(assets)

    @patch("wog_dump.core.unpack.UnityPy.load")
    def test_get_asset_info(
        self, mock_load: Mock, test_config: WOGConfig, mock_unity_asset: Path
    ) -> None:
        texture = Mock(
            m_Name="skin",
            image=Mock(),
            m_Width=64,
            m_Height=64,
            m_TextureFormat=1,
        )
        mock_load.return_value = make_env([make_unity_object("Texture2D", texture)])

        unpacker = AssetUnpacker(test_config)
        info = unpacker.get_asset_info(mock_unity_asset)

        assert info["types"] == {"Texture2D": 1}
        assert info["objects"][0]["name"] == "skin"
        assert info["objects"][0]["dimensions"] == "64x64"
        assert info["summary"]["extractable_objects"] == 1

    def test_get_asset_info_missing_file(self, test_config: WOGConfig) -> None:
        unpacker = AssetUnpacker(test_config)

        with pytest.raises(UnpackError, match="not found"):
            unpacker.get_asset_info(Path("/nonexistent.unity3d"))
