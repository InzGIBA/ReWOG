"""Import the plugin from source (no installation) and isolate state."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

try:
    import wog_dump  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from wog_dump.core.config import WOGConfig, reset_config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path) -> Generator[Path, None, None]:
    """Keep every test (including CLI invocations) away from the real
    user database."""
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
    monkeypatch.setenv("WOG_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture(autouse=True)
def scrub_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WOG_AUTH_ID", "WOG_AUTH_SESSION", "WOG_DEVICE_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def cleanup_config() -> Generator[None, None, None]:
    yield
    reset_config()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def test_config(temp_dir: Path) -> WOGConfig:
    reset_config()
    config = WOGConfig(
        base_dir=temp_dir,
        assets_dir=temp_dir / "assets",
        encrypted_dir=temp_dir / "encrypted",
        decrypted_dir=temp_dir / "decrypted",
        data_file=temp_dir / "data.json",
        weapons_file=temp_dir / "weapons.txt",
        keys_file=temp_dir / "keys.txt",
        db_path=temp_dir / "test.db",
        max_threads=2,
    )
    config.assets_dir.mkdir(exist_ok=True)
    config.encrypted_dir.mkdir(exist_ok=True)
    config.decrypted_dir.mkdir(exist_ok=True)
    return config


@pytest.fixture
def cli_config(test_config: WOGConfig, monkeypatch: pytest.MonkeyPatch) -> WOGConfig:
    monkeypatch.setattr("wog_plugin_mitm.cli.get_config", lambda: test_config)
    return test_config
