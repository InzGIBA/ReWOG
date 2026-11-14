"""Import the plugin from source (no installation) and isolate state."""

from __future__ import annotations

import sys
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

from wog_dump.core.config import reset_config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Keep logs, database and config away from real user state."""
    monkeypatch.setenv("WOG_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("WOG_DB_PATH", str(tmp_path / "test_wog_dump.db"))
    yield
    reset_config()
