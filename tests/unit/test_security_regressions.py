"""Security regression tests for the audit findings (run-1).

Each test pins one confirmed finding from the security audit:
traversal via bundle object names, symlink-following writes, ambient-env
transport trust, legacy-import re-arming and the resume prefix gap.
Reproductions are offline with dummy data only.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from wog_dump.core.config import WOGConfig
from wog_dump.core.database import WOGDatabase
from wog_dump.core.decrypt import AssetDecryptor
from wog_dump.core.download import DownloadError, DownloadManager
from wog_dump.core.http import create_session
from wog_dump.core.scene import SceneExportError
from wog_dump.core.unpack import AssetUnpacker, WeaponListProcessor

# --------------------------------------------------------------------------
# bundle-object-name-path-traversal (medium)
# --------------------------------------------------------------------------


def _text_asset(name: str, script: bytes = b"PAYLOAD") -> Mock:
    asset = Mock()
    asset.m_Name = name
    asset.m_Script = script
    return asset


def _env_with(*objects: Mock) -> Mock:
    env = Mock()
    wrapped = []
    for obj in objects:
        holder = Mock()
        holder.type.name = "TextAsset"
        holder.read.return_value = obj
        wrapped.append(holder)
    env.objects = wrapped
    return env


class TestBundleNameTraversal:
    def test_decrypt_skips_traversal_names(self, test_config: WOGConfig) -> None:
        """A ../-named TextAsset writes nothing outside the output dirs."""
        decryptor = AssetDecryptor(test_config)
        asset_file = test_config.assets_dir / "bundle.unity3d"
        asset_file.write_bytes(b"UnityFS")

        sentinel = test_config.encrypted_dir.parent / "escaped.bytes"
        sentinel.parent.mkdir(parents=True, exist_ok=True)

        with patch(
            "wog_dump.core.decrypt.UnityPy.load",
            return_value=_env_with(_text_asset("../../escaped")),
        ):
            files = decryptor.decrypt_asset(asset_file, key="k")

        assert files == []
        assert not sentinel.exists()
        assert not (test_config.encrypted_dir / "../../escaped.bytes").exists()

    def test_decrypt_rejects_absolute_names(
        self, test_config: WOGConfig, tmp_path: Path
    ) -> None:
        decryptor = AssetDecryptor(test_config)
        asset_file = test_config.assets_dir / "bundle.unity3d"
        asset_file.write_bytes(b"UnityFS")

        with patch(
            "wog_dump.core.decrypt.UnityPy.load",
            return_value=_env_with(_text_asset(str(tmp_path / "abs"))),
        ):
            files = decryptor.decrypt_asset(asset_file, key="k")

        assert files == []
        assert not (tmp_path / "abs.bytes").exists()

    def test_unpack_extractor_skips_traversal_names(
        self, test_config: WOGConfig
    ) -> None:
        unpacker = AssetUnpacker(test_config)
        asset_file = test_config.assets_dir / "bundle.unity3d"
        asset_file.write_bytes(b"UnityFS")

        with patch(
            "wog_dump.core.unpack.UnityPy.load",
            return_value=_env_with(_text_asset("../../../u_evil")),
        ):
            files = unpacker.unpack_asset(asset_file)

        assert files == []
        # Nothing two levels above the intended output dir
        assert not (asset_file.parent.parent / "u_evil.txt").exists()

    def test_weapon_parser_rejects_traversal_banners(
        self, test_config: WOGConfig
    ) -> None:
        processor = WeaponListProcessor(test_config)
        content = "ak74.png\n../_evil.png\nx_?query#frag.png\n../../deeper/_e.png\n"
        assert processor._parse_weapon_names(content) == ["ak74"]

    def test_scene_rejects_traversal_weapon_name(self, test_config: WOGConfig) -> None:
        with pytest.raises(SceneExportError, match="Invalid weapon name"):
            from wog_dump.core.scene import SceneBuilder

            SceneBuilder(test_config).build_scene("../scene_evil")


# --------------------------------------------------------------------------
# download.asset-name-path-traversal (medium)
# --------------------------------------------------------------------------


class TestAssetNameTraversal:
    def test_download_rejects_traversal_name(self, test_config: WOGConfig) -> None:
        victim = test_config.assets_dir.parent / "outside_pwn.unity3d"
        victim.write_bytes(b"PRE-EXISTING-VICTIM")

        with DownloadManager(test_config) as manager:
            with pytest.raises(DownloadError, match="Invalid asset name"):
                manager._fetch_asset("../outside_pwn")

        assert victim.read_bytes() == b"PRE-EXISTING-VICTIM"

    def test_download_rejects_absolute_name(
        self, test_config: WOGConfig, tmp_path: Path
    ) -> None:
        with DownloadManager(test_config) as manager:
            with pytest.raises(DownloadError, match="Invalid asset name"):
                manager._fetch_asset(str(tmp_path / "abs"))

    def test_asset_url_rejects_metacharacters(self, test_config: WOGConfig) -> None:
        with DownloadManager(test_config) as manager:
            with pytest.raises(DownloadError):
                manager.asset_url("q?x=1")


# --------------------------------------------------------------------------
# download.tmp-symlink-follow (medium)
# --------------------------------------------------------------------------


class TestTmpSymlink:
    @patch("wog_dump.core.download.requests.Session.get")
    def test_planted_tmp_symlink_is_discarded(
        self, mock_get: Mock, test_config: WOGConfig, tmp_path: Path
    ) -> None:
        victim = tmp_path / "victim.bashrc"
        victim.write_text("# original shell config\n")

        temp_path = test_config.assets_dir / "ak74.tmp"
        temp_path.symlink_to(victim)

        body = b"UnityFS attacker-bytes"
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Length": str(len(body))}
        response.iter_content.return_value = [body]
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        with DownloadManager(test_config) as manager:
            assert manager.download_single_asset("ak74") is True

        # The victim was NOT written through; the tool downloaded fresh
        # into a regular file
        assert victim.read_text() == "# original shell config\n"
        assert stat.S_ISREG((test_config.assets_dir / "ak74.unity3d").stat().st_mode)


# --------------------------------------------------------------------------
# download.resume-prefix-unbound (medium)
# --------------------------------------------------------------------------


class TestResumePrefixBound:
    @patch("wog_dump.core.download.requests.Session.get")
    def test_representation_change_restarts(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A stale prefix + same-length republish yields the NEW body.

        The recorded validator is replayed as If-Range; the mock answers
        200 (representation changed), so the old prefix must be
        discarded, not appended to.
        """
        old = b"OLD-REPR-" + b"a" * 15
        new = b"NEW-REPR-" + b"b" * 15

        temp_path = test_config.assets_dir / "rep.unity3d.tmp"
        temp_path.write_bytes(old[:12])
        meta_path = test_config.assets_dir / "rep.unity3d.tmp.meta"
        meta_path.write_text('{"validator": "etag-old"}', encoding="utf-8")

        response = Mock()
        response.status_code = 200  # If-Range says: changed
        response.headers = {"Content-Length": str(len(new)), "ETag": '"etag-new"'}
        response.iter_content.return_value = iter([new])
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        with DownloadManager(test_config) as manager:
            assert manager.download_single_asset("rep") is True

        final = test_config.assets_dir / "rep.unity3d"
        assert final.read_bytes() == new  # never old[:12] + new[12:]


# --------------------------------------------------------------------------
# http.trustenv-proxy-ca-injection (medium)
# --------------------------------------------------------------------------


class TestTransportTrustPinned:
    def test_create_session_pins_trust_env(self, test_config: WOGConfig) -> None:
        session = create_session(test_config)
        assert session.trust_env is False

    def test_planted_env_does_not_reach_prepared_request(
        self, test_config: WOGConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = create_session(test_config)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/nonexistent/evil-ca.pem")

        request = session.prepare_request(
            requests.Request("PUT", "https://eu1.example/api", data=b"session=DUMMY")
        )
        settings = session.merge_environment_settings(request.url, {}, None, None, None)
        assert settings["proxies"] == {}
        assert settings["verify"] is True


# --------------------------------------------------------------------------
# env.cwd-dotenv-credential-store-trust (medium): .env allowlist
# --------------------------------------------------------------------------


class TestDotenvAllowlist:
    def test_only_wog_namespace_is_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wog_dump.cli.main import load_env_file

        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("WOG_STRICT_MODE", raising=False)

        (tmp_path / ".env").write_text(
            "HTTPS_PROXY=http://127.0.0.1:9\n"
            "REQUESTS_CA_BUNDLE=/nonexistent/evil.pem\n"
            "NETRC=/nonexistent/evil.netrc\n"
            "WOG_STRICT_MODE=true\n"
            "SOME_OTHER_VAR=1\n",
            encoding="utf-8",
        )

        applied = load_env_file(tmp_path / ".env")

        assert applied == ["WOG_STRICT_MODE"]
        assert os.environ.get("HTTPS_PROXY") is None
        assert os.environ.get("REQUESTS_CA_BUNDLE") is None
        assert os.environ.get("NETRC") is None
        assert os.environ.get("WOG_STRICT_MODE") == "true"

    def test_ambient_wins_over_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wog_dump.cli.main import load_env_file

        monkeypatch.setenv("WOG_STRICT_MODE", "false")
        (tmp_path / ".env").write_text("WOG_STRICT_MODE=true\n", encoding="utf-8")

        assert load_env_file(tmp_path / ".env") == []
        assert os.environ["WOG_STRICT_MODE"] == "false"

    def test_db_symlink_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real.db"
        real.write_bytes(b"sqlite")
        link = tmp_path / "link.db"
        link.symlink_to(real)

        config = WOGConfig(db_path=link)
        with pytest.raises(Exception, match="symlink"):
            WOGDatabase(config)


# --------------------------------------------------------------------------
# legacy-import-rearm-clear-migrate-override (low)
# --------------------------------------------------------------------------


class TestLegacyImportRearm:
    def test_migrate_is_once_and_force_reimports(
        self, test_db: WOGDatabase, tmp_path: Path
    ) -> None:
        weapons_file = tmp_path / "weapons.txt"
        keys_file = tmp_path / "keys.txt"
        weapons_file.write_text("ak74\n")
        keys_file.write_text("ak74 " + "a" * 32 + "\n")

        assert test_db.import_legacy_txt(weapons_file, keys_file) is True

        # Second run without force is a no-op even after a clear
        test_db.clear_cached_data()
        assert test_db.import_legacy_txt(weapons_file, keys_file) is False
        assert test_db.get_weapons() == []
        assert test_db.get_keys() == {}

        # --force re-imports explicitly
        assert test_db.import_legacy_txt(weapons_file, keys_file, force=True) is True
        assert test_db.get_weapons() == ["ak74"]

    def test_legacy_keys_do_not_override_api_keys(
        self, test_db: WOGDatabase, tmp_path: Path
    ) -> None:
        test_db.save_keys({"ak74": "0" * 32}, source="api")

        keys_file = tmp_path / "keys.txt"
        keys_file.write_text("ak74 " + "f" * 32 + "\n")

        assert test_db.import_legacy_txt(None, keys_file, force=True) is True

        # The fresh api-sourced key survived the legacy import
        keys = test_db.get_keys()
        assert keys["ak74"] == "0" * 32

    def test_clear_is_terminal(self, test_db: WOGDatabase, tmp_path: Path) -> None:
        """After cache --clear, a load_keys() must not resurrect the files."""
        weapons_file = tmp_path / "weapons.txt"
        keys_file = tmp_path / "keys.txt"
        weapons_file.write_text("ak74\n")
        keys_file.write_text("ak74 " + "a" * 32 + "\n")
        assert test_db.import_legacy_txt(weapons_file, keys_file) is True

        test_db.clear_cached_data()

        # load_keys' migration path consults import_legacy_txt, which now
        # refuses because the once-marker survived the clear
        assert test_db.import_legacy_txt(weapons_file, keys_file) is False
        assert test_db.get_keys() == {}


# --------------------------------------------------------------------------
# log.account-identity-world-readable (low)
# --------------------------------------------------------------------------


class TestLogPermissions:
    def test_log_dir_and_file_owner_only(self, tmp_path: Path) -> None:
        from wog_dump.utils.logging import WOGLogger

        log_dir = tmp_path / "logs"
        logger = WOGLogger.__new__(WOGLogger)  # avoid the global singleton
        logger.console = None  # not used by _setup_handlers path under test
        logger.logger = __import__("logging").getLogger("test_wog_log_perms")
        logger.performance_monitor = None
        logger._console_handler = None
        logger._file_handler = None
        logger.log_file = None

        import logging as _logging

        _logging.getLogger("test_wog_log_perms").handlers.clear()

        old_level = logger.logger.level
        logger.logger.setLevel(_logging.DEBUG)
        try:
            with (
                patch("wog_dump.utils.logging.default_log_dir", return_value=log_dir),
                patch(
                    "wog_dump.utils.logging.log_file_level", return_value=_logging.INFO
                ),
            ):
                logger._setup_handlers(_logging.WARNING)
        finally:
            logger.logger.setLevel(old_level)

        assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(logger.log_file.stat().st_mode) == 0o600


# --------------------------------------------------------------------------
# keys.result-1000-no-relogin (rejected as a finding; documented intent)
# --------------------------------------------------------------------------


class TestResult1000Relogin:
    def test_session_rejected_triggers_relogin_path(
        self, test_config: WOGConfig
    ) -> None:
        """result=1000 raises AuthenticationError so the retry re-logs in."""
        from wog_dump.core.api import WOGApiClient
        from wog_dump.core.auth import AuthManager
        from wog_dump.core.database import WOGDatabase
        from wog_dump.core.keys import AuthenticationError, KeyManager

        db = WOGDatabase(test_config)
        db.save_identity("777", "stale")
        db.set_setting("device_id", "d" * 40)

        client = Mock(spec=WOGApiClient)
        client.build_common_params.return_value = "ver=1&time=2"
        client.call.return_value = (
            "00001109&query=3&model=ak47&result=1000&errmsg=session+expired"
        )

        auth = AuthManager(test_config, db=db, client=client)
        manager = KeyManager.__new__(KeyManager)
        manager.config = test_config
        manager.logger = db.logger
        manager.db = db
        manager.client = client
        manager.auth = auth
        manager._key_cache = {}

        with pytest.raises(AuthenticationError, match="1000"):
            manager._parse_api_response(client.call.return_value, "ak47")
