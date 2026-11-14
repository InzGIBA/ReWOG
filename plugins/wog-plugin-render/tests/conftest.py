"""Import the plugin from source (no installation) and isolate logs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

try:
    import wog_dump  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep test log files out of the real user data directory."""
    monkeypatch.setenv("WOG_LOG_DIR", str(tmp_path / "logs"))
