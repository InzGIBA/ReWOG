"""Unit tests for the preview renderer (task 05)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from wog_dump.core.render import (
    RenderError,
    RenderManager,
    RenderOptions,
    _assemble_gif,
    _turntable_script,
)


def make_png(size: int = 8) -> bytes:
    """One-byte-pixel grayscale PNG, minimal but well-formed."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([row % 256]) * size for row in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class TestRenderOptions:
    def test_defaults(self) -> None:
        options = RenderOptions()
        assert options.angles == 8
        assert options.gif is True

    def test_validation(self) -> None:
        with pytest.raises(RenderError, match="Angles"):
            RenderOptions(angles=0)
        with pytest.raises(RenderError, match="Size"):
            RenderOptions(size=16)
        with pytest.raises(RenderError, match="Elevation"):
            RenderOptions(elevation=120)
        with pytest.raises(RenderError, match="image format"):
            RenderOptions(image_format="tiff")


class TestScriptGeneration:
    def test_placeholders_substituted(self, tmp_path: Path) -> None:
        script = _turntable_script(
            Path("/tmp/ak47.glb"),
            tmp_path,
            RenderOptions(angles=12, size=1600, elevation=20.0, image_format="webp"),
        )
        assert "@@" not in script
        assert "/tmp/ak47.glb" in script
        assert str(tmp_path) in script
        assert 'ANGLE_COUNT = int("12")' in script
        assert 'RENDER_SIZE = int("1600")' in script
        assert 'ELEVATION_DEG = float("20.0")' in script
        assert 'IMAGE_FORMAT = "webp"' in script

    def test_all_angles_rendered(self, tmp_path: Path) -> None:
        script = _turntable_script(
            Path("/tmp/w.glb"), tmp_path, RenderOptions(angles=5)
        )
        assert 'ANGLE_COUNT = int("5")' in script
        assert "for index in range(ANGLE_COUNT):" in script


class TestRenderManager:
    def test_invalid_weapon_name(self) -> None:
        manager = RenderManager()
        with pytest.raises(RenderError, match="Invalid weapon name"):
            manager.render("../escape")

    def test_missing_scene(self, tmp_path: Path) -> None:
        from wog_dump.core.config import WOGConfig

        config = WOGConfig(base_dir=tmp_path)
        manager = RenderManager(config)
        with pytest.raises(RenderError, match="Scene not found"):
            manager.render("ak47")

    def test_missing_blender(self, tmp_path: Path, monkeypatch: Any) -> None:
        from wog_dump.core.config import WOGConfig

        config = WOGConfig(base_dir=tmp_path)
        scenes = tmp_path / "runtime" / "scenes" / "ak47"
        scenes.mkdir(parents=True)
        (scenes / "ak47.glb").write_bytes(b"glb")
        monkeypatch.delenv("WOG_BLENDER_PATH", raising=False)
        monkeypatch.setattr("wog_dump.core.export.shutil.which", lambda name: None)
        manager = RenderManager(config)
        with pytest.raises(RenderError, match="Blender not found"):
            manager.render("ak47")

    def test_render_failure_reports_blender_output(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from wog_dump.core.config import WOGConfig

        config = WOGConfig(base_dir=tmp_path)
        scenes = tmp_path / "runtime" / "scenes" / "ak47"
        scenes.mkdir(parents=True)
        (scenes / "ak47.glb").write_bytes(b"glb")

        class FakeResult:
            returncode = 1
            stderr = "Error: boom"
            stdout = ""

        monkeypatch.setenv("WOG_BLENDER_PATH", str(tmp_path / "blender"))
        (tmp_path / "blender").write_text("")
        captured: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any) -> FakeResult:
            captured["cmd"] = args[0]
            return FakeResult()

        monkeypatch.setattr("wog_dump.core.render.subprocess.run", fake_run)
        manager = RenderManager(config)
        with pytest.raises(RenderError, match="boom"):
            manager.render("ak47")
        assert "--background" in captured["cmd"]

    def test_successful_render_collects_frames_and_gif(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from wog_dump.core.config import WOGConfig

        config = WOGConfig(base_dir=tmp_path)
        scenes = tmp_path / "runtime" / "scenes" / "ak47"
        previews = scenes / "previews"
        previews.mkdir(parents=True)
        (scenes / "ak47.glb").write_bytes(b"glb")

        class FakeResult:
            returncode = 0
            stderr = ""
            stdout = ""

        monkeypatch.setenv("WOG_BLENDER_PATH", str(tmp_path / "blender"))
        (tmp_path / "blender").write_text("")

        def fake_run(*args: Any, **kwargs: Any) -> FakeResult:
            # Emulate Blender writing the frames into the output dir
            for index in range(1, 9):
                (previews / f"view_{index:02d}.png").write_bytes(make_png())
            return FakeResult()

        monkeypatch.setattr("wog_dump.core.render.subprocess.run", fake_run)
        manager = RenderManager(config)
        frames = manager.render("ak47")

        assert len(frames) == 8
        assert frames[0].name == "view_01.png"
        assert (previews / "preview.gif").exists()


class TestGifAssembly:
    def test_assembles_looping_gif(self, tmp_path: Path) -> None:
        frames = []
        for index in range(3):
            frame = tmp_path / f"f{index}.png"
            frame.write_bytes(make_png(index + 10))
            frames.append(frame)

        gif = tmp_path / "preview.gif"
        _assemble_gif(frames, gif)
        data = gif.read_bytes()
        assert data[:6] == b"GIF89a" or data[:6] == b"GIF87a"

    def test_no_frames_no_crash(self, tmp_path: Path) -> None:
        _assemble_gif([], tmp_path / "empty.gif")
        assert not (tmp_path / "empty.gif").exists()
