"""Live tests against the real game servers.

These run only when authentication is configured in .env - either a
captured session (WOG_AUTH_ID + WOG_AUTH_SESSION) or game-account
credentials (WOG_ACCOUNT_EMAIL + WOG_ACCOUNT_PASSWORD). Without .env
they are skipped:

    pytest -m live          # run live tests
    pytest -m "not live"    # everything else (CI-safe)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# The env scrubbing fixture ignores live tests; load the repo .env ourselves
load_dotenv(override=True)

HAS_CAPTURED_SESSION = bool(
    os.environ.get("WOG_AUTH_ID", "").strip()
    and os.environ.get("WOG_AUTH_SESSION", "").strip()
)
HAS_ACCOUNT = bool(
    os.environ.get("WOG_ACCOUNT_EMAIL", "").strip()
    and os.environ.get("WOG_ACCOUNT_PASSWORD", "").strip()
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.integration,
    pytest.mark.skipif(
        not (HAS_CAPTURED_SESSION or HAS_ACCOUNT),
        reason="no credentials configured in .env",
    ),
]


@pytest.fixture
def live_config(tmp_path: Path):
    """Isolated config pointing at a throwaway database."""
    from wog_dump.core.config import WOGConfig

    return WOGConfig(
        base_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        encrypted_dir=tmp_path / "encrypted",
        decrypted_dir=tmp_path / "decrypted",
        data_file=tmp_path / "data.json",
        weapons_file=tmp_path / "weapons.txt",
        keys_file=tmp_path / "keys.txt",
        db_path=tmp_path / "live.db",
    )


class TestLiveKeyFetch:
    def test_fetch_real_key(self, live_config) -> None:
        """Fetch a real sync key for a classic model from the live API."""
        from wog_dump.core.decrypt import KeyManager

        key = KeyManager(live_config).get_key_for_asset("colt_1911")

        assert key is not None
        assert len(key) == 32
        int(key, 16)  # hex


class TestLivePipeline:
    def test_download_decrypt_unpack(self, live_config) -> None:
        """Full real-data pipeline: download an asset, decrypt it, load it."""
        from wog_dump.core.decrypt import AssetDecryptor, KeyManager
        from wog_dump.core.download import DownloadManager

        with DownloadManager(live_config) as downloader:
            assert downloader.download_single_asset("colt_1911")

        key = KeyManager(live_config).get_key_for_asset("colt_1911")
        assert key is not None

        asset_path = live_config.assets_dir / "colt_1911.unity3d"
        decrypted_files = AssetDecryptor(live_config).decrypt_asset(asset_path, key)

        assert decrypted_files, "no TextAssets were decrypted"

        import UnityPy

        for decrypted in decrypted_files:
            env = UnityPy.load(str(decrypted))
            assert env.objects, f"{decrypted} is not a loadable Unity bundle"
