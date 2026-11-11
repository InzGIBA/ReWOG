"""CLI coverage follow-up: decrypt, download, unpack, scene, serve commands.

Every command runs through the full cli group with its dependencies
mocked at the core boundary; nothing touches the network or disk
outside tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from wog_dump.cli.main import cli
from wog_dump.core.config import set_config


@pytest.fixture(autouse=True)
def isolated_base(tmp_path: Path) -> Path:
    # WOGConfig's path validator creates the runtime layout itself
    set_config(base_dir=tmp_path)
    return tmp_path


def make_scene(tmp_path: Path, weapon: str = "ak47") -> None:
    scene_dir = tmp_path / "runtime" / "scenes" / weapon
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / f"{weapon}.glb").write_bytes(b"glb")


class TestDecryptCommand:
    def _patch_pipeline(self, decrypted: int = 3, failed: int = 0) -> dict[str, Any]:
        mocks: dict[str, Any] = {}
        decryptor = Mock()
        decryptor.decrypt_all_assets.return_value = (
            [Path(f"/tmp/f{i}.unity3d") for i in range(decrypted)],
            [f"w{i}" for i in range(failed)],
        )
        mocks["decryptor"] = decryptor
        key_manager = Mock()
        key_manager.load_keys.return_value = {"ak47": "k"}
        mocks["keys"] = key_manager
        return mocks

    def test_no_keys_message(self, isolated_base: Path) -> None:
        with (
            patch("wog_dump.cli.decrypt.KeyManager") as km,
            patch("wog_dump.cli.decrypt._load_weapon_list") as load,
        ):
            km.return_value.load_keys.return_value = {}
            load.return_value = ["ak47"]
            runner = CliRunner()
            result = runner.invoke(cli, ["decrypt-assets"])
        assert result.exit_code == 1
        assert "No decryption keys" in result.output

    def test_decrypt_success(self, isolated_base: Path) -> None:
        files = [isolated_base / "f1.unity3d", isolated_base / "f2.unity3d"]
        for f in files:
            f.write_bytes(b"data")
        with (
            patch("wog_dump.cli.decrypt.KeyManager") as km,
            patch("wog_dump.cli.decrypt._load_weapon_list") as load,
            patch("wog_dump.cli.decrypt.AssetDecryptor") as dec,
        ):
            km.return_value.load_keys.return_value = {"ak47": "k"}
            load.return_value = ["ak47"]
            dec.return_value.decrypt_all_assets.return_value = (files, [])
            runner = CliRunner()
            result = runner.invoke(cli, ["decrypt-assets"])
        assert result.exit_code == 0
        assert "Decrypted 2 files" in result.output

    def test_decrypt_failure_strict_mode(self, isolated_base: Path) -> None:
        with (
            patch("wog_dump.cli.decrypt.KeyManager") as km,
            patch("wog_dump.cli.decrypt._load_weapon_list") as load,
            patch("wog_dump.cli.decrypt.AssetDecryptor") as dec,
        ):
            km.return_value.load_keys.return_value = {"ak47": "k"}
            load.return_value = ["ak47"]
            dec.return_value.decrypt_all_assets.return_value = ([], ["ak47"])
            runner = CliRunner()
            result = runner.invoke(cli, ["--strict-mode", "decrypt-assets"])
        assert result.exit_code == 1

    def test_validate_reports_failures(self, isolated_base: Path) -> None:
        bad = isolated_base / "empty.unity3d"
        bad.write_bytes(b"")  # empty: fails validation
        good = isolated_base / "good.unity3d"
        good.write_bytes(b"data")
        with (
            patch("wog_dump.cli.decrypt.KeyManager") as km,
            patch("wog_dump.cli.decrypt._load_weapon_list") as load,
            patch("wog_dump.cli.decrypt.AssetDecryptor") as dec,
            patch("wog_dump.cli.decrypt.UnityPy") as unity,
        ):
            km.return_value.load_keys.return_value = {"ak47": "k"}
            load.return_value = None
            dec.return_value.decrypt_all_assets.return_value = ([good, bad], [])
            unity.load.return_value.objects = []
            runner = CliRunner()
            result = runner.invoke(cli, ["decrypt-assets", "--validate"])
        assert "Validation failed for 2" in result.output


class TestDownloadCommand:
    def test_download_weapons_success(self, isolated_base: Path) -> None:
        from wog_dump.cli.download import download_weapons

        runner = CliRunner()
        with (
            patch("wog_dump.cli.download.DownloadManager") as dm,
            patch("wog_dump.cli.download.WeaponListProcessor") as proc,
        ):
            dm.return_value.__enter__.return_value.download_weapon_list.return_value = (
                isolated_base / "spider.unity3d"
            )
            dm.return_value.__enter__.return_value.validate_asset.return_value = True
            proc.return_value.process_weapon_list_asset.return_value = [
                "ak47",
                "m4a1",
            ]
            result = runner.invoke(cli, ["download-weapons"])
        assert result.exit_code == 0, result.output
        assert "2" in result.output

    def test_download_weapons_validation_failure(self, isolated_base: Path) -> None:
        runner = CliRunner()
        with (
            patch("wog_dump.cli.download.DownloadManager") as dm,
            patch("wog_dump.cli.download.WeaponListProcessor"),
        ):
            dm.return_value.__enter__.return_value.download_weapon_list.return_value = (
                isolated_base / "spider.unity3d"
            )
            dm.return_value.__enter__.return_value.validate_asset.return_value = False
            result = runner.invoke(cli, ["download-weapons", "--validate"])
        assert result.exit_code == 1
        assert "validation" in result.output

    def test_download_assets_check_only(self, isolated_base: Path) -> None:
        runner = CliRunner()
        with (
            patch("wog_dump.cli.download.DownloadManager") as dm,
            patch(
                "wog_dump.cli.download._load_weapon_list", return_value=["ak47", "m4a1"]
            ),
        ):
            dm.return_value.__enter__.return_value.check_for_updates.return_value = [
                "ak47",
                "m4a1",
            ]
            result = runner.invoke(cli, ["download-assets", "--check-only"])
        assert result.exit_code == 0, result.output
        assert "needing updates" in result.output

    def test_download_assets_batches(self, isolated_base: Path) -> None:
        runner = CliRunner()
        with (
            patch("wog_dump.cli.download.DownloadManager") as dm,
            patch("wog_dump.cli.download._load_weapon_list", return_value=["ak47"]),
        ):
            instance = dm.return_value.__enter__.return_value
            instance.download_assets_batched.return_value = (["ak47"], [], 1)
            result = runner.invoke(cli, ["download-assets"])
        assert result.exit_code == 0, result.output
        assert "Downloaded 1 assets" in result.output

    def test_download_assets_byte_bar_disabled_when_piped(
        self, isolated_base: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("WOG_NO_PROGRESS", "1")
        runner = CliRunner()
        with (
            patch("wog_dump.cli.download.DownloadManager") as dm,
            patch("wog_dump.cli.download._load_weapon_list", return_value=["ak47"]),
        ):
            dm.return_value.__enter__.return_value.download_assets_batched.return_value = (
                [],
                [],
                0,
            )
            result = runner.invoke(cli, ["--no-progress", "download-assets"])
        assert result.exit_code == 0, result.output


class TestUnpackCommand:
    def test_unpack_success(self, isolated_base: Path) -> None:
        decrypted = isolated_base / "runtime" / "decrypted"
        decrypted.mkdir(parents=True, exist_ok=True)
        (decrypted / "ak47.unity3d").write_bytes(b"bundle")
        runner = CliRunner()
        with patch("wog_dump.cli.unpack.AssetUnpacker") as unpacker:
            unpacker.return_value.unpack_multiple_assets.return_value = {
                "ak47": [Path("/tmp/mesh.obj")]
            }
            result = runner.invoke(cli, ["unpack-assets"])
        assert result.exit_code == 0, result.output
        assert "1 files" in result.output

    def test_list_extractors_shows_builtin(self, isolated_base: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["unpack-assets", "--list-extractors"])
        assert result.exit_code == 0
        assert "Texture2D" in result.output

    def test_no_assets_message(self, isolated_base: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["unpack-assets"])
        assert result.exit_code == 1
        assert "No assets found" in result.output


class TestSceneCommandFormats:
    def test_format_parsing_rejects_unknown(self, isolated_base: Path) -> None:
        runner = CliRunner()
        # the error handler converts the usage error into exit 1
        result = runner.invoke(cli, ["build-scene", "ak47", "--format", "obj"])
        assert result.exit_code == 1
        assert "unknown format" in result.output

    def test_build_scene_mocked(self, isolated_base: Path) -> None:
        make_scene(isolated_base)
        runner = CliRunner()
        with (
            patch("wog_dump.cli.scene.SceneBuilder") as builder,
            patch("wog_dump.core.scene.convert_format") as convert,
        ):
            glb = isolated_base / "runtime" / "scenes" / "ak47" / "ak47.glb"
            builder.return_value.build_scene.return_value = glb
            convert.return_value = [isolated_base / "ak47.stl"]
            result = runner.invoke(cli, ["build-scene", "ak47", "--format", "stl"])
        assert result.exit_code == 0, result.output
        kwargs = builder.return_value.build_scene.call_args.kwargs
        assert kwargs["export_formats"] == frozenset({"stl"})
        assert kwargs["export_options"].units == "m"

    def test_build_scene_per_weapon_output_dir(self, isolated_base: Path) -> None:
        make_scene(isolated_base, "m4a1")
        runner = CliRunner()
        with patch("wog_dump.cli.scene.SceneBuilder") as builder:
            glb = isolated_base / "runtime" / "scenes" / "m4a1-field-strip" / "m4a1.glb"
            builder.return_value.build_scene.return_value = glb
            result = runner.invoke(
                cli,
                [
                    "build-scene",
                    "m4a1",
                    "--state",
                    "field-strip",
                    "--output-dir",
                    str(isolated_base),
                ],
            )
        assert result.exit_code == 0, result.output
        passed = builder.return_value.build_scene.call_args.kwargs["output_dir"]
        assert passed.name == "m4a1-field-strip"


class TestServeCommand:
    def test_serve_without_extra(self, isolated_base: Path, monkeypatch: Any) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "uvicorn":
                raise ImportError("no uvicorn")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--no-browser"])
        assert result.exit_code == 1
        assert "web extra" in result.output

    def test_serve_starts_uvicorn(self, isolated_base: Path, monkeypatch: Any) -> None:
        import sys

        fake_uvicorn = Mock()
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--no-browser", "--port", "9001"])
        assert result.exit_code == 0, result.output
        fake_uvicorn.run.assert_called_once()
        call_args = fake_uvicorn.run.call_args
        assert call_args.kwargs["port"] == 9001
        assert call_args.kwargs["host"] == "127.0.0.1"


class TestOpenBrowserQuietly:
    def test_returns_bool_and_keeps_stdio_intact(self, capsys: Any) -> None:
        from wog_dump.cli.serve import open_browser_quietly

        result = open_browser_quietly("http://127.0.0.1:1/nowhere")
        assert isinstance(result, bool)
        # The fd juggling must not leak: a later print still lands on stdout
        print("still-here")
        assert "still-here" in capsys.readouterr().out

    def test_no_desktop_helper_noise_on_stdout(self, capsys: Any) -> None:
        from wog_dump.cli.serve import open_browser_quietly

        open_browser_quietly("http://127.0.0.1:1/nowhere")
        captured = capsys.readouterr()
        assert "Operation not supported" not in captured.out + captured.err


class TestInfoCommand:
    def test_info_tables_render(self, isolated_base: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "Configuration" in result.output

    def test_info_protocol_probe_json(self, isolated_base: Path) -> None:
        from wog_dump.core.probe import ProbeResult

        runner = CliRunner()
        with patch("wog_dump.core.probe.ProtocolProbe") as probe:
            probe.return_value.run.return_value = [
                ProbeResult("API endpoint", "ok", "up")
            ]
            result = runner.invoke(cli, ["info", "--protocol-probe", "--probe-json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["schema"] == "wog-dump.probe/1"
        assert payload["verdict"] == "ok"

    def test_info_protocol_probe_fail_exits_2(self, isolated_base: Path) -> None:
        from wog_dump.core.probe import ProbeResult

        runner = CliRunner()
        with patch("wog_dump.core.probe.ProtocolProbe") as probe_cls:
            probe_cls.return_value.run.return_value = [
                ProbeResult("API endpoint", "fail", "down")
            ]
            result = runner.invoke(cli, ["info", "--protocol-probe"])
        assert result.exit_code == 2
