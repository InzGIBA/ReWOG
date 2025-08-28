"""Test configuration and fixtures for WOG Dump tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from wog_dump.core.config import WOGConfig, reset_config


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def test_config(temp_dir: Path) -> WOGConfig:
    """Create a test configuration."""
    reset_config()  # Reset global config
    
    config = WOGConfig(
        base_dir=temp_dir,
        assets_dir=temp_dir / "assets",
        encrypted_dir=temp_dir / "encrypted",
        decrypted_dir=temp_dir / "decrypted",
        weapons_file=temp_dir / "weapons.txt",
        keys_file=temp_dir / "keys.txt",
        max_threads=2,  # Reduce for testing
    )
    
    # Create directories
    config.assets_dir.mkdir(exist_ok=True)
    config.encrypted_dir.mkdir(exist_ok=True)
    config.decrypted_dir.mkdir(exist_ok=True)
    
    return config


@pytest.fixture
def sample_weapon_list() -> list[str]:
    """Sample weapon list for testing."""
    return [
        "ak74",
        "m4a1", 
        "glock17",
        "desert_eagle",
        "mp5",
    ]


@pytest.fixture
def sample_keys() -> dict[str, str]:
    """Sample decryption keys for testing."""
    return {
        "ak74": "sample_key_1",
        "m4a1": "sample_key_2",
        "glock17": "sample_key_3",
        "desert_eagle": "sample_key_4",
        "mp5": "sample_key_5",
    }


@pytest.fixture
def mock_unity_asset(temp_dir: Path) -> Path:
    """Create a mock Unity asset file for testing."""
    asset_path = temp_dir / "test_asset.unity3d"
    
    # Create a simple mock file (in real usage this would be a Unity asset)
    with open(asset_path, "wb") as f:
        f.write(b"MOCK_UNITY_ASSET_DATA")
    
    return asset_path


@pytest.fixture(autouse=True)
def cleanup_config():
    """Auto-cleanup configuration after each test."""
    yield
    reset_config()