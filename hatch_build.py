"""Hatchling build hook: compile gettext catalogs into the artifacts.

The .mo files are gitignored, so a fresh checkout (and CI) must
compile them at build time; without this hook wheels shipped only
.po sources and every non-English locale silently fell back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        subprocess.run(
            [sys.executable, str(root / "scripts" / "compile_locales.py")],
            check=True,
            cwd=root,
        )
        build_data["artifacts"] = ["src/wog_dump/resources/locales/**/*.mo"]