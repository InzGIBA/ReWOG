"""Test configuration and fixtures for WOG Dump tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from wog_dump.core.config import WOGConfig, reset_config
from wog_dump.core.database import WOGDatabase


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path) -> Generator[Path, None, None]:
    """Keep every test (including CLI invocations) away from the real user
    database, with a fresh database per test."""
    db_path = tmp_path / "test_wog_dump.db"
    old_value = os.environ.get("WOG_DB_PATH")
    os.environ["WOG_DB_PATH"] = str(db_path)
    yield db_path
    if old_value is None:
        os.environ.pop("WOG_DB_PATH", None)
    else:
        os.environ["WOG_DB_PATH"] = old_value


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep test log files out of the real user data directory."""
    monkeypatch.setenv("WOG_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def test_config(temp_dir: Path) -> WOGConfig:
    """Create a test configuration with an isolated database."""
    reset_config()  # Reset global config

    config = WOGConfig(
        base_dir=temp_dir,
        assets_dir=temp_dir / "assets",
        encrypted_dir=temp_dir / "encrypted",
        decrypted_dir=temp_dir / "decrypted",
        data_file=temp_dir / "data.json",
        weapons_file=temp_dir / "weapons.txt",  # Legacy support
        keys_file=temp_dir / "keys.txt",  # Legacy support
        db_path=temp_dir / "test.db",
        max_threads=2,  # Reduce for testing
    )

    # Create directories
    config.assets_dir.mkdir(exist_ok=True)
    config.encrypted_dir.mkdir(exist_ok=True)
    config.decrypted_dir.mkdir(exist_ok=True)

    return config


@pytest.fixture
def test_db(test_config: WOGConfig) -> WOGDatabase:
    """Create a test database."""
    return WOGDatabase(test_config)


@pytest.fixture
def populated_db(
    test_db: WOGDatabase,
    sample_weapon_list: list[str],
    sample_keys: dict[str, str],
) -> WOGDatabase:
    """Create a database with sample data."""
    test_db.save_weapons(sample_weapon_list)
    test_db.save_keys(sample_keys)
    return test_db


@pytest.fixture
def sample_weapon_list() -> list[str]:
    """Sample weapon list for testing."""
    return [
        "ak74n",
        "m4a1",
        "glock17",
        "desert_eagle",
        "mp5",
    ]


@pytest.fixture
def sample_keys() -> dict[str, str]:
    """Sample decryption keys for testing."""
    return {
        "ak74n": "a" * 32,
        "m4a1": "b" * 32,
        "glock17": "c" * 32,
        "desert_eagle": "d" * 32,
        "mp5": "e" * 32,
    }


@pytest.fixture
def mock_unity_asset(temp_dir: Path) -> Path:
    """Create a mock Unity asset file for testing."""
    asset_path = temp_dir / "test_asset.unity3d"

    # Create a simple mock file (in real usage this would be a Unity asset)
    with open(asset_path, "wb") as f:
        f.write(b"MOCK_UNITY_ASSET_DATA")

    return asset_path


AUTH_ENV_VARS = (
    "WOG_AUTH_ID",
    "WOG_AUTH_SESSION",
    "WOG_DEVICE_ID",
    "WOG_ACCOUNT_EMAIL",
    "WOG_ACCOUNT_PASSWORD",
)


@pytest.fixture(autouse=True)
def scrub_auth_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep credentials from a developer's .env out of regular tests.

    Live tests (marked with @pytest.mark.live) opt out and read .env
    themselves.
    """
    if request.node.get_closest_marker("live"):
        return
    for name in AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def cleanup_config():
    """Auto-cleanup configuration after each test."""
    yield
    reset_config()
