"""Unit tests for download module."""

from __future__ import annotations

import hashlib
from unittest.mock import Mock, patch

import pytest
import requests

from wog_dump.core.config import WOGConfig
from wog_dump.core.database import AssetState
from wog_dump.core.download import DownloadError, DownloadManager


class TestDownloadManager:
    """Test DownloadManager class."""

    def test_init(self, test_config: WOGConfig) -> None:
        """Test DownloadManager initialization."""
        with DownloadManager(test_config) as manager:
            assert manager.config == test_config
            assert isinstance(manager.session, requests.Session)

    def test_context_manager(self, test_config: WOGConfig) -> None:
        """Test context manager functionality."""
        with DownloadManager(test_config) as manager:
            assert manager.session is not None

        # Session should be closed after context exit
        # Note: We can't easily test if session is closed, but no exceptions should occur

    @patch("wog_dump.core.download.requests.Session.head")
    def test_get_asset_size_success(
        self, mock_head: Mock, test_config: WOGConfig
    ) -> None:
        """Test getting asset size successfully."""
        mock_response = Mock()
        mock_response.headers = {"Content-Length": "1024"}
        mock_response.raise_for_status.return_value = None
        mock_head.return_value = mock_response

        with DownloadManager(test_config) as manager:
            size = manager.get_asset_size("test_asset")

        assert size == 1024
        mock_head.assert_called_once()

    @patch("wog_dump.core.download.requests.Session.head")
    def test_get_asset_size_failure(
        self, mock_head: Mock, test_config: WOGConfig
    ) -> None:
        """Test getting asset size with network error."""
        mock_head.side_effect = requests.RequestException("Network error")

        with DownloadManager(test_config) as manager:
            size = manager.get_asset_size("test_asset")

        assert size == 0  # Should return 0 on error

    def test_check_asset_needs_update_missing_file(
        self, test_config: WOGConfig
    ) -> None:
        """Test checking update for missing file."""
        with DownloadManager(test_config) as manager:
            # Mock get_asset_size to avoid network call
            manager.get_asset_size = Mock(return_value=1024)
            needs_update = manager.check_asset_needs_update("nonexistent_asset")

        assert needs_update is True

    def test_check_asset_needs_update_same_size(self, test_config: WOGConfig) -> None:
        """Test checking update for file with same size."""
        # Create a test asset file
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        asset_path.write_bytes(b"test data")

        with DownloadManager(test_config) as manager:
            # Mock get_asset_size to return same size
            manager.get_asset_size = Mock(return_value=len(b"test data"))
            needs_update = manager.check_asset_needs_update("test_asset")

        assert needs_update is False

    def test_check_asset_needs_update_different_size(
        self, test_config: WOGConfig
    ) -> None:
        """Test checking update for file with different size."""
        # Create a test asset file
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        asset_path.write_bytes(b"test data")

        with DownloadManager(test_config) as manager:
            # Mock get_asset_size to return different size
            manager.get_asset_size = Mock(return_value=2048)
            needs_update = manager.check_asset_needs_update("test_asset")

        assert needs_update is True

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_single_asset_success(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """Test downloading single asset successfully."""
        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "19"}
        mock_response.iter_content.return_value = [b"UnityFS", b" mock bundle"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is True

        # Check file was created
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        assert asset_path.exists()
        assert asset_path.read_bytes() == b"UnityFS mock bundle"

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_single_asset_size_mismatch(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A truncated download (size != Content-Length) fails but stays resumable."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.iter_content.return_value = [b"UnityFS", b" partial"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is False
        # No final asset; the partial temp file is kept so the next run resumes
        assert not (test_config.assets_dir / "test_asset.unity3d").exists()
        assert (test_config.assets_dir / "test_asset.tmp").exists()

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_single_asset_failure(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """Test downloading single asset with failure."""
        mock_get.side_effect = requests.RequestException("Download failed")

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is False

    @patch("wog_dump.core.download.requests.Session.head")
    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_weapon_list_up_to_date(
        self, mock_get: Mock, mock_head: Mock, test_config: WOGConfig
    ) -> None:
        """Test downloading weapon list when it's up to date."""
        # Create existing file
        asset_path = test_config.assets_dir / "spider_gen.unity3d"
        test_data = b"existing data"
        asset_path.write_bytes(test_data)

        # Mock head response to return same size
        mock_head_response = Mock()
        mock_head_response.headers = {"Content-Length": str(len(test_data))}
        mock_head_response.raise_for_status.return_value = None
        mock_head.return_value = mock_head_response

        with DownloadManager(test_config) as manager:
            result_path = manager.download_weapon_list()

        assert result_path == asset_path
        assert result_path.read_bytes() == test_data
        mock_get.assert_not_called()  # Should not download

    @patch("wog_dump.core.download.requests.Session.head")
    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_weapon_list_needs_update(
        self, mock_get: Mock, mock_head: Mock, test_config: WOGConfig
    ) -> None:
        """Test downloading weapon list when update is needed."""
        asset_path = test_config.assets_dir / "spider_gen.unity3d"

        # Mock head response
        mock_head_response = Mock()
        mock_head_response.headers = {"Content-Length": "9"}
        mock_head_response.raise_for_status.return_value = None
        mock_head.return_value = mock_head_response

        # Mock get response
        mock_get_response = Mock()
        mock_get_response.headers = {"Content-Length": "16"}
        mock_get_response.iter_content.return_value = [b"UnityFS", b" new data"]
        mock_get_response.raise_for_status.return_value = None
        mock_get.return_value = mock_get_response

        with DownloadManager(test_config) as manager:
            result_path = manager.download_weapon_list()

        assert result_path == asset_path
        assert result_path.read_bytes() == b"UnityFS new data"
        mock_get.assert_called_once()

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_weapon_list_failure(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """Test downloading weapon list with failure."""
        mock_get.side_effect = requests.RequestException("Download failed")

        with DownloadManager(test_config) as manager:
            with pytest.raises(DownloadError):
                manager.download_weapon_list()

    def test_check_for_updates_parallel(
        self, test_config: WOGConfig, sample_weapon_list: list[str]
    ) -> None:
        """Test checking for updates in parallel."""
        with DownloadManager(test_config) as manager:
            # Mock the check method to avoid network calls
            manager.check_asset_needs_update = Mock(return_value=True)

            to_download = manager.check_for_updates(sample_weapon_list)

        assert len(to_download) == len(sample_weapon_list)
        # Check that all weapons are present regardless of order (parallel processing)
        assert set(to_download) == set(sample_weapon_list)

    def test_download_assets_no_updates(
        self, test_config: WOGConfig, sample_weapon_list: list[str]
    ) -> None:
        """Test downloading assets when no updates are needed."""
        with DownloadManager(test_config) as manager:
            # Mock check_for_updates to return empty list
            manager.check_for_updates = Mock(return_value=[])

            successful, failed, missing = manager.download_assets(sample_weapon_list)

        assert successful == []
        assert failed == []

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_resumes_partial_file(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A verified partial download resumes via Range + If-Range."""
        # Partial temp file from a previous interrupted run: 8 bytes of 22,
        # plus the recorded validator of the response that produced it
        temp_path = test_config.assets_dir / "test_asset.tmp"
        temp_path.write_bytes(b"UnityFS\x00")
        meta_path = test_config.assets_dir / "test_asset.tmp.meta"
        meta_path.write_text('{"validator": "etag-v1"}', encoding="utf-8")

        remaining = b"rest of bundle"  # 14 bytes -> total 22
        mock_response = Mock()
        mock_response.status_code = 206
        mock_response.headers = {"Content-Length": str(len(remaining))}
        mock_response.iter_content.return_value = [remaining]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is True

        # The Range header asked for the missing bytes, and If-Range
        # bound the resume to the recorded representation
        _, kwargs = mock_get.call_args
        assert kwargs["headers"] == {"Range": "bytes=8-", "If-Range": "etag-v1"}

        # The final file is prefix + remainder, and no temp/meta remains
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        assert asset_path.read_bytes() == b"UnityFS\x00" + remaining
        assert not temp_path.exists()
        assert not meta_path.exists()

    @patch("wog_dump.core.download.requests.Session.get")
    def test_unverifiable_partial_restarts(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A partial temp file with no recorded validator is never resumed.

        Its bytes cannot be proven to belong to the server's current
        representation (they may be foreign or predate a republish), so
        the download restarts from scratch.
        """
        temp_path = test_config.assets_dir / "test_asset.tmp"
        temp_path.write_bytes(b"UnityFS\x00")

        full_body = b"UnityFS fresh full body"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": str(len(full_body))}
        mock_response.iter_content.return_value = [full_body]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is True

        # No Range request: the unverifiable prefix was discarded
        _, kwargs = mock_get.call_args
        assert kwargs["headers"] is None

        asset_path = test_config.assets_dir / "test_asset.unity3d"
        assert asset_path.read_bytes() == full_body

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_resume_ignored_by_server(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A server answering 200 (no range support) triggers a full redownload."""
        temp_path = test_config.assets_dir / "test_asset.tmp"
        temp_path.write_bytes(b"stale partial")
        meta_path = test_config.assets_dir / "test_asset.tmp.meta"
        meta_path.write_text('{"validator": "etag-v1"}', encoding="utf-8")

        full_body = b"UnityFS fresh full body"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": str(len(full_body))}
        mock_response.iter_content.return_value = [full_body]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is True
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        assert asset_path.read_bytes() == full_body

    @patch("wog_dump.core.download.requests.Session.get")
    def test_network_error_keeps_partial_for_resume(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A network failure mid-download leaves the partial file resumable."""
        temp_path = test_config.assets_dir / "test_asset.tmp"
        temp_path.write_bytes(b"UnityFS\x00")
        meta_path = test_config.assets_dir / "test_asset.tmp.meta"
        meta_path.write_text('{"validator": "etag-v1"}', encoding="utf-8")

        mock_get.side_effect = requests.RequestException("connection reset")

        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")

        assert success is False
        # Partial data and its validator survive for the next attempt
        assert temp_path.read_bytes() == b"UnityFS\x00"
        assert meta_path.exists()

    def test_validate_asset_with_expected_size(self, test_config: WOGConfig) -> None:
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        asset_path.write_bytes(b"UnityFS bundle data")

        with DownloadManager(test_config) as manager:
            assert manager.validate_asset(asset_path, expected_size=19) is True
            assert manager.validate_asset(asset_path, expected_size=999) is False

    def test_validate_asset_signature_fallback(self, test_config: WOGConfig) -> None:
        with DownloadManager(test_config) as manager:
            good = test_config.assets_dir / "good.unity3d"
            good.write_bytes(b"UnityFS signed")
            assert manager.validate_asset(good) is True

            tiny = test_config.assets_dir / "tiny.unity3d"
            tiny.write_bytes(b"ab")
            assert manager.validate_asset(tiny) is False

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_assets_parallel(
        self, mock_get: Mock, test_config: WOGConfig, sample_weapon_list: list[str]
    ) -> None:
        """download_assets downloads the pending assets in parallel."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "19"}
        mock_response.iter_content.return_value = [b"UnityFS", b" mock bundle"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            manager.check_for_updates = Mock(return_value=list(sample_weapon_list))
            successful, failed, missing = manager.download_assets(sample_weapon_list)

        assert set(successful) == set(sample_weapon_list)
        assert failed == []
        assert mock_get.call_count == len(sample_weapon_list)
        assert manager.get_download_stats()["files_downloaded"] == len(
            sample_weapon_list
        )

    @patch("wog_dump.core.download.requests.Session.get")
    def test_missing_asset_skipped_not_failed(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """A 404 asset is skipped with a warning instead of failing the batch."""
        not_found_response = Mock()
        not_found_response.status_code = 404
        not_found_response.headers = {}
        not_found_response.iter_content.return_value = []
        not_found_response.raise_for_status.side_effect = requests.HTTPError(
            "404 Client Error: Not Found"
        )

        ok_response = Mock()
        ok_response.status_code = 200
        ok_response.headers = {"Content-Length": "19"}
        ok_response.iter_content.return_value = [b"UnityFS", b" mock bundle"]
        ok_response.raise_for_status.return_value = None

        def get_response(url: str, **kwargs: object) -> Mock:
            if "missing" in url:
                return not_found_response
            return ok_response

        mock_get.side_effect = get_response

        with DownloadManager(test_config) as manager:
            successful, failed, missing = manager.download_assets(
                ["ak74n", "missing_gun"], check_updates=False
            )

        assert successful == ["ak74n"]
        assert failed == []  # 404 is not a failure that should stop batches
        assert missing == 1
        stats = manager.get_download_stats()
        assert stats["files_skipped"] == 1
        assert stats["files_failed"] == 0

    def test_asset_not_found_error_is_download_error(
        self, test_config: WOGConfig
    ) -> None:
        from wog_dump.core.download import AssetNotFoundError, DownloadError

        assert issubclass(AssetNotFoundError, DownloadError)


class TestAssetStateRecording:
    """Hash/validator state recorded after downloads and on backfill."""

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_records_sha256_and_validators(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        body = b"UnityFS mock bundle"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Length": str(len(body)),
            "ETag": '"v1"',
            "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        }
        mock_response.iter_content.return_value = [body]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            assert manager.download_single_asset("test_asset") is True
            state = manager.db.get_asset_state("test_asset")

        assert state is not None
        assert state.asset == "test_asset"
        assert state.weapon == "test_asset"
        assert state.sha256 == hashlib.sha256(body).hexdigest()
        assert state.size == len(body)
        assert state.etag == '"v1"'
        assert state.last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"
        assert state.checked_at

    @patch("wog_dump.core.download.requests.Session.get")
    def test_download_hashes_streaming_across_chunks(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        """The digest covers the whole file even when it spans chunks."""
        test_config.chunk_size = 1024
        body = b"UnityFS" + b"x" * 5000
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": str(len(body))}
        mock_response.iter_content.return_value = [
            body[i : i + 1024] for i in range(0, len(body), 1024)
        ]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with DownloadManager(test_config) as manager:
            assert manager.download_single_asset("test_asset") is True
            state = manager.db.get_asset_state("test_asset")

        assert state is not None
        assert state.sha256 == hashlib.sha256(body).hexdigest()

    @patch("wog_dump.core.download.requests.Session.get")
    def test_failed_download_records_no_state(
        self, mock_get: Mock, test_config: WOGConfig
    ) -> None:
        mock_get.side_effect = requests.RequestException("Download failed")

        with DownloadManager(test_config) as manager:
            assert manager.download_single_asset("test_asset") is False
            assert manager.db.get_asset_state("test_asset") is None

    def test_record_asset_state_backfills_existing_file(
        self, test_config: WOGConfig
    ) -> None:
        body = b"UnityFS cached bundle"
        (test_config.assets_dir / "ak74n.unity3d").write_bytes(body)

        with DownloadManager(test_config) as manager:
            state = manager.record_asset_state("ak74n")
            assert state is not None
            assert state.sha256 == hashlib.sha256(body).hexdigest()
            assert state.size == len(body)
            assert state.etag is None
            # A missing file is not an error, just nothing to record
            assert manager.record_asset_state("missing_gun") is None

    def test_record_asset_state_keeps_stored_validators(
        self, test_config: WOGConfig
    ) -> None:
        (test_config.assets_dir / "ak74n.unity3d").write_bytes(b"UnityFS data")

        with DownloadManager(test_config) as manager:
            manager.db.upsert_asset_state(
                AssetState(
                    asset="ak74n",
                    weapon="ak74n",
                    sha256="0" * 64,
                    size=1,
                    etag='"old"',
                    last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
                )
            )
            state = manager.record_asset_state("ak74n")

        assert state is not None
        assert state.etag == '"old"'
        assert state.last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"

    def test_backfill_asset_states_scans_assets_dir(
        self, test_config: WOGConfig
    ) -> None:
        (test_config.assets_dir / "ak74n.unity3d").write_bytes(b"UnityFS one")
        (test_config.assets_dir / "m4a1.unity3d").write_bytes(b"UnityFS two")

        with DownloadManager(test_config) as manager:
            recorded = manager.backfill_asset_states()
            assert sorted(recorded) == ["ak74n", "m4a1"]
            assert manager.db.get_asset_state("ak74n") is not None
            assert manager.db.get_asset_state("m4a1") is not None
            # Already-recorded files are left untouched on the next pass
            assert manager.backfill_asset_states() == []

    def test_backfill_does_not_overwrite_existing_state(
        self, test_config: WOGConfig
    ) -> None:
        (test_config.assets_dir / "ak74n.unity3d").write_bytes(b"UnityFS one")

        with DownloadManager(test_config) as manager:
            manager.db.upsert_asset_state(
                AssetState(asset="ak74n", weapon="ak74n", sha256="f" * 64, size=9)
            )
            assert manager.backfill_asset_states(["ak74n"]) == []
            state = manager.db.get_asset_state("ak74n")

        assert state is not None
        assert state.sha256 == "f" * 64
