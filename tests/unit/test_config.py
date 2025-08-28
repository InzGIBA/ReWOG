"""Unit tests for configuration module."""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from wog_dump.core.config import WOGConfig, get_config, reset_config, set_config


class TestWOGConfig:
    """Test WOGConfig class."""
    
    def test_default_config(self, temp_dir: Path) -> None:
        """Test default configuration values."""
        config = WOGConfig(base_dir=temp_dir)
        
        assert config.base_dir == temp_dir
        assert config.assets_dir == temp_dir / "assets"
        assert config.encrypted_dir == temp_dir / "encrypted"
        assert config.decrypted_dir == temp_dir / "decrypted"
        assert config.max_threads == 4
        assert config.game_version == "2.2.1z5"
        assert config.unity_version == "2019.2.18f1"
    
    def test_directory_creation(self, temp_dir: Path) -> None:
        """Test that directories are created automatically."""
        config = WOGConfig(
            base_dir=temp_dir,
            assets_dir=temp_dir / "test_assets",
            encrypted_dir=temp_dir / "test_encrypted", 
            decrypted_dir=temp_dir / "test_decrypted",
        )
        
        assert config.assets_dir.exists()
        assert config.encrypted_dir.exists()
        assert config.decrypted_dir.exists()
    
    def test_xor_binary_path(self, temp_dir: Path) -> None:
        """Test XOR binary path computation."""
        config = WOGConfig(base_dir=temp_dir)
        
        system = platform.system().lower()
        arch = platform.architecture()[0]
        
        expected_path = temp_dir / "bin" / system / arch / "xor"
        if system == "windows":
            expected_path = expected_path.with_suffix(".exe")
        
        assert config.xor_binary_path == expected_path
    
    def test_api_headers(self, temp_dir: Path) -> None:
        """Test API headers generation."""
        config = WOGConfig(base_dir=temp_dir)
        headers = config.get_api_headers()
        
        assert headers['Content-Type'] == 'application/octet-stream'
        assert headers['User-Agent'] == f'UnityPlayer/{config.unity_version} (UnityWebRequest/1.0, libcurl/7.52.0-DEV)'
        assert headers['X-Unity-Version'] == config.unity_version
    
    def test_combined_blacklist(self, temp_dir: Path) -> None:
        """Test combined blacklist functionality."""
        config = WOGConfig(base_dir=temp_dir)
        blacklist = config.get_combined_blacklist()
        
        # Should contain both weapon and texture blacklist items
        assert "hk_g28" in blacklist  # From weapon blacklist
        assert "shooting_01" in blacklist  # From texture blacklist
        assert len(blacklist) > 0
    
    def test_max_threads_validation(self, temp_dir: Path) -> None:
        """Test max_threads validation."""
        # Valid values
        config = WOGConfig(base_dir=temp_dir, max_threads=8)
        assert config.max_threads == 8
        
        # Invalid values should raise validation error
        with pytest.raises(ValueError):
            WOGConfig(base_dir=temp_dir, max_threads=0)
        
        with pytest.raises(ValueError):
            WOGConfig(base_dir=temp_dir, max_threads=20)


class TestConfigManagement:
    """Test global configuration management."""
    
    def test_get_config_default(self) -> None:
        """Test getting default configuration."""
        reset_config()
        config = get_config()
        assert isinstance(config, WOGConfig)
    
    def test_set_config(self, temp_dir: Path) -> None:
        """Test setting configuration parameters."""
        reset_config()
        
        config = set_config(
            base_dir=temp_dir,
            max_threads=8,
        )
        
        assert config.base_dir == temp_dir
        assert config.max_threads == 8
    
    def test_config_singleton(self, temp_dir: Path) -> None:
        """Test that configuration acts as singleton."""
        reset_config()
        
        config1 = get_config()
        config2 = get_config()
        
        assert config1 is config2
    
    def test_reset_config(self, temp_dir: Path) -> None:
        """Test configuration reset."""
        # Set custom config
        set_config(base_dir=temp_dir, max_threads=8)
        config1 = get_config()
        
        # Reset and get new config
        reset_config()
        config2 = get_config()
        
        assert config1 is not config2
        assert config2.max_threads == 4  # Default value