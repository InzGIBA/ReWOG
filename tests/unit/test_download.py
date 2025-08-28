"""Unit tests for download module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from wog_dump.core.config import WOGConfig
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
    
    @patch('wog_dump.core.download.requests.Session.head')
    def test_get_asset_size_success(self, mock_head: Mock, test_config: WOGConfig) -> None:
        """Test getting asset size successfully."""
        mock_response = Mock()
        mock_response.headers = {"Content-Length": "1024"}
        mock_response.raise_for_status.return_value = None
        mock_head.return_value = mock_response
        
        with DownloadManager(test_config) as manager:
            size = manager.get_asset_size("test_asset")
        
        assert size == 1024
        mock_head.assert_called_once()
    
    @patch('wog_dump.core.download.requests.Session.head')
    def test_get_asset_size_failure(self, mock_head: Mock, test_config: WOGConfig) -> None:
        """Test getting asset size with network error."""
        mock_head.side_effect = requests.RequestException("Network error")
        
        with DownloadManager(test_config) as manager:
            size = manager.get_asset_size("test_asset")
        
        assert size == 0  # Should return 0 on error
    
    def test_check_asset_needs_update_missing_file(self, test_config: WOGConfig) -> None:
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
    
    def test_check_asset_needs_update_different_size(self, test_config: WOGConfig) -> None:
        """Test checking update for file with different size."""
        # Create a test asset file
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        asset_path.write_bytes(b"test data")
        
        with DownloadManager(test_config) as manager:
            # Mock get_asset_size to return different size
            manager.get_asset_size = Mock(return_value=2048)
            needs_update = manager.check_asset_needs_update("test_asset")
        
        assert needs_update is True
    
    @patch('wog_dump.core.download.requests.Session.get')
    def test_download_single_asset_success(self, mock_get: Mock, test_config: WOGConfig) -> None:
        """Test downloading single asset successfully."""
        # Mock response
        mock_response = Mock()
        mock_response.headers = {"Content-Length": "9"}
        mock_response.iter_content.return_value = [b"test", b" data"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")
        
        assert success is True
        
        # Check file was created
        asset_path = test_config.assets_dir / "test_asset.unity3d"
        assert asset_path.exists()
        assert asset_path.read_bytes() == b"test data"
    
    @patch('wog_dump.core.download.requests.Session.get')
    def test_download_single_asset_failure(self, mock_get: Mock, test_config: WOGConfig) -> None:
        """Test downloading single asset with failure."""
        mock_get.side_effect = requests.RequestException("Download failed")
        
        with DownloadManager(test_config) as manager:
            success = manager.download_single_asset("test_asset")
        
        assert success is False
    
    @patch('wog_dump.core.download.requests.Session.head')
    @patch('wog_dump.core.download.requests.Session.get')
    def test_download_weapon_list_up_to_date(self, mock_get: Mock, mock_head: Mock, test_config: WOGConfig) -> None:
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
    
    @patch('wog_dump.core.download.requests.Session.head')
    @patch('wog_dump.core.download.requests.Session.get')
    def test_download_weapon_list_needs_update(self, mock_get: Mock, mock_head: Mock, test_config: WOGConfig) -> None:
        """Test downloading weapon list when update is needed."""
        asset_path = test_config.assets_dir / "spider_gen.unity3d"
        
        # Mock head response
        mock_head_response = Mock()
        mock_head_response.headers = {"Content-Length": "9"}
        mock_head_response.raise_for_status.return_value = None
        mock_head.return_value = mock_head_response
        
        # Mock get response
        mock_get_response = Mock()
        mock_get_response.headers = {"Content-Length": "9"}
        mock_get_response.iter_content.return_value = [b"new", b" data"]
        mock_get_response.raise_for_status.return_value = None
        mock_get.return_value = mock_get_response
        
        with DownloadManager(test_config) as manager:
            result_path = manager.download_weapon_list()
        
        assert result_path == asset_path
        assert result_path.read_bytes() == b"new data"
        mock_get.assert_called_once()
    
    @patch('wog_dump.core.download.requests.Session.get')
    def test_download_weapon_list_failure(self, mock_get: Mock, test_config: WOGConfig) -> None:
        """Test downloading weapon list with failure."""
        mock_get.side_effect = requests.RequestException("Download failed")
        
        with DownloadManager(test_config) as manager:
            with pytest.raises(DownloadError):
                manager.download_weapon_list()
    
    def test_check_for_updates_parallel(self, test_config: WOGConfig, sample_weapon_list: list[str]) -> None:
        """Test checking for updates in parallel."""
        with DownloadManager(test_config) as manager:
            # Mock the check method to avoid network calls
            manager.check_asset_needs_update = Mock(return_value=True)
            
            to_download = manager.check_for_updates(sample_weapon_list)
        
        assert len(to_download) == len(sample_weapon_list)
        assert to_download == sample_weapon_list
    
    def test_download_assets_no_updates(self, test_config: WOGConfig, sample_weapon_list: list[str]) -> None:
        """Test downloading assets when no updates are needed."""
        with DownloadManager(test_config) as manager:
            # Mock check_for_updates to return empty list
            manager.check_for_updates = Mock(return_value=[])
            
            successful, failed = manager.download_assets(sample_weapon_list)
        
        assert successful == []
        assert failed == []