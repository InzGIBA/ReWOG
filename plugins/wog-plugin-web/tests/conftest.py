"""Make the plugin importable without installation; isolate core state."""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import wog_dump  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from wog_dump.core.config import reset_config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Keep tests away from the real user database and log directory."""
    monkeypatch.setenv("WOG_DB_PATH", str(tmp_path / "test_wog_dump.db"))
    monkeypatch.setenv("WOG_LOG_DIR", str(tmp_path / "logs"))
    yield
    reset_config()
