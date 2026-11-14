"""Unit tests for GLB optimization and texture size/dedup handling."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from wog_dump.core import optimize
from wog_dump.core.scene import SceneBuilder, SceneStats


@pytest.fixture
def builder(tmp_path: Path) -> SceneBuilder:
    instance = SceneBuilder()
    instance.config = instance.config.model_copy(update={"base_dir": tmp_path})
    return instance


def fake_texture(name: str, size: int = 64) -> Any:
    return SimpleNamespace(
        m_Name=name, image=Image.new("RGBA", (size, size), (10, 20, 30, 255))
    )


class TestFindGltfpack:
    def test_env_path_wins(self, tmp_path: Path, monkeypatch: Any) -> None:
        binary = tmp_path / "gltfpack"
        binary.write_text("#!/bin/sh\n")
        monkeypatch.setenv("WOG_GLTFPACK_PATH", str(binary))
        assert optimize.find_gltfpack() == binary

    def test_env_path_missing_falls_back_to_none(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("WOG_GLTFPACK_PATH", str(tmp_path / "absent"))
        monkeypatch.setattr(optimize.shutil, "which", lambda name: None)
        assert optimize.find_gltfpack() is None

    def test_path_lookup(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("WOG_GLTFPACK_PATH", raising=False)
        monkeypatch.setattr(optimize.shutil, "which", lambda name: "/usr/bin/gltfpack")
        assert optimize.find_gltfpack() == Path("/usr/bin/gltfpack")


class TestOptimizeGlb:
    def test_missing_binary_keeps_file(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(optimize, "find_gltfpack", lambda: None)
        glb = tmp_path / "scene.glb"
        glb.write_bytes(b"original")
        logger = MagicMock()
        assert optimize.optimize_glb(glb, logger) is False
        assert glb.read_bytes() == b"original"
        logger.warning.assert_called_once()

    def test_success_replaces_file(self, tmp_path: Path, monkeypatch: Any) -> None:
        glb = tmp_path / "scene.glb"
        glb.write_bytes(b"original")

        def run(args: list[str], **kwargs: Any) -> Any:
            Path(args[args.index("-o") + 1]).write_bytes(b"compressed")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(optimize, "find_gltfpack", lambda: Path("/bin/gltfpack"))
        monkeypatch.setattr(optimize.subprocess, "run", run)
        assert optimize.optimize_glb(glb, MagicMock()) is True
        assert glb.read_bytes() == b"compressed"
        assert not (tmp_path / "scene.glb.opt.tmp").exists()

    def test_failure_keeps_original(self, tmp_path: Path, monkeypatch: Any) -> None:
        glb = tmp_path / "scene.glb"
        glb.write_bytes(b"original")
        monkeypatch.setattr(optimize, "find_gltfpack", lambda: Path("/bin/gltfpack"))
        monkeypatch.setattr(
            optimize.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=1, stderr="boom"),
        )
        assert optimize.optimize_glb(glb, MagicMock()) is False
        assert glb.read_bytes() == b"original"

    def test_timeout_keeps_original(self, tmp_path: Path, monkeypatch: Any) -> None:
        glb = tmp_path / "scene.glb"
        glb.write_bytes(b"original")
        monkeypatch.setattr(optimize, "find_gltfpack", lambda: Path("/bin/gltfpack"))

        def run(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="gltfpack", timeout=1)

        monkeypatch.setattr(optimize.subprocess, "run", run)
        assert optimize.optimize_glb(glb, MagicMock()) is False
        assert glb.read_bytes() == b"original"


class TestTextureSize:
    def test_downscale_limits_largest_side(self) -> None:
        texture = fake_texture("big", size=256)
        data = SceneBuilder._texture_png(texture, max_size=64)
        image = Image.open(io.BytesIO(data))
        assert max(image.size) == 64

    def test_small_texture_untouched(self) -> None:
        texture = fake_texture("small", size=32)
        data = SceneBuilder._texture_png(texture, max_size=64)
        assert Image.open(io.BytesIO(data)).size == (32, 32)

    def test_embed_respects_builder_texture_size(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        from wog_dump.core.glb import GLBWriter

        builder._texture_size = 32
        writer = GLBWriter()
        stats = SceneStats(weapon="test")
        index = builder._embed_texture(
            writer,
            1,
            fake_texture("atlas", size=128),
            "plain",
            tmp_path / "textures",
            stats,
            {},
        )
        assert index == 0
        written = Image.open(tmp_path / "textures" / "atlas.png")
        assert max(written.size) == 32


class TestTextureDedup:
    def test_identical_textures_share_store_file(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        from wog_dump.core.glb import GLBWriter

        stats = SceneStats(weapon="test")
        for scene in ("a", "b"):
            builder._embed_texture(
                GLBWriter(),
                1,
                fake_texture("shared"),
                "plain",
                tmp_path / scene / "textures",
                stats,
                {},
            )
        store = tmp_path / "runtime" / "texture-store"
        stored = list(store.glob("*.png"))
        assert len(stored) == 1
        link_a = tmp_path / "a" / "textures" / "shared.png"
        link_b = tmp_path / "b" / "textures" / "shared.png"
        assert link_a.stat().st_ino == stored[0].stat().st_ino
        assert link_b.stat().st_ino == stored[0].stat().st_ino

    def test_symlinked_store_refused(
        self, builder: SceneBuilder, tmp_path: Path
    ) -> None:
        from wog_dump.core.glb import GLBWriter
        from wog_dump.core.scene import SceneExportError

        real = tmp_path / "elsewhere"
        real.mkdir()
        store = tmp_path / "runtime" / "texture-store"
        store.parent.mkdir(parents=True)
        store.symlink_to(real)
        with pytest.raises(SceneExportError, match="texture store"):
            builder._embed_texture(
                GLBWriter(),
                1,
                fake_texture("x"),
                "plain",
                tmp_path / "textures",
                SceneStats(weapon="test"),
                {},
            )


class TestCliFlags:
    def test_new_flags_documented(self) -> None:
        from click.testing import CliRunner

        from wog_dump.cli.scene import build_scene

        result = CliRunner().invoke(build_scene, ["--help"])
        assert result.exit_code == 0
        assert "--texture-size" in result.output
        assert "--optimize" in result.output
