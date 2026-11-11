"""Catalog client for the WOG Dump addon.

Deliberately bpy-free: this module imports and tests outside Blender.
The UI layer (``__init__.py``) owns everything that touches bpy.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

CATALOG_SCHEMA = "wog-dump.catalog/3"


class WogClientError(Exception):
    """Raised when the wog-dump CLI or its output is unusable."""


@dataclass
class CatalogEntry:
    name: str
    has_scene: bool


def run_wog(
    wog_path: str, args: list[str], base_dir: str | None = None, timeout: int = 600
) -> tuple[int, str, str]:
    """Run one wog-dump command; returns (code, stdout, stderr)."""
    result = subprocess.run(
        [wog_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=base_dir or None,
    )
    return result.returncode, result.stdout, result.stderr


def parse_catalog(stdout: str) -> list[CatalogEntry]:
    """Parse ``wog-dump catalog --json`` output.

    Raises WogClientError on broken JSON or a schema mismatch: the
    addon never guesses against an unknown format, it tells the user
    to update the toolkit.
    """
    start = stdout.find("{")
    if start < 0:
        raise WogClientError("wog-dump catalog produced no JSON output")
    try:
        payload = json.loads(stdout[start:])
    except json.JSONDecodeError as e:
        raise WogClientError(f"catalog JSON is broken: {e}") from e
    if payload.get("schema") != CATALOG_SCHEMA:
        raise WogClientError(
            f"catalog schema {payload.get('schema')!r} is not "
            f"{CATALOG_SCHEMA!r}: update the wog-dump toolkit"
        )
    entries = []
    for weapon in payload.get("weapons", []):
        entries.append(
            CatalogEntry(name=str(weapon["name"]), has_scene=bool(weapon["scene"]))
        )
    return entries


def load_catalog(wog_path: str, base_dir: str | None = None) -> list[CatalogEntry]:
    code, stdout, stderr = run_wog(wog_path, ["catalog", "--json"], base_dir)
    if code != 0:
        detail = (stderr or stdout).strip().splitlines()[-1:] or ["no output"]
        raise WogClientError(f"wog-dump catalog failed: {detail[0]}")
    return parse_catalog(stdout)


def scene_glb_path(base_dir: str | None, weapon: str) -> Path:
    """Expected GLB path for one weapon in the runtime layout."""
    base = Path(base_dir) if base_dir else Path.cwd()
    return base / "runtime" / "scenes" / weapon / f"{weapon}.glb"
