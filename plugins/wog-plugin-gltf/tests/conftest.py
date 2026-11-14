"""Make the plugin importable without installation."""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))
