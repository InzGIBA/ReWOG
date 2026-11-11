"""Weapon catalog: pipeline status per weapon from the database and disk.

``build_catalog`` reads the local state (database plus one directory
listing per runtime stage) and returns plain entries; the CLI layer
owns presentation. The JSON schema version lives in
``CATALOG_SCHEMA`` and any field change bumps it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import WOGConfig, get_config
from .database import WOGDatabase
from .spider import SYSTEM_ASSETS, ICON_SUFFIX, category_leaves, previews_dir

__all__ = ["CATALOG_SCHEMA", "WeaponEntry", "build_catalog"]

CATALOG_SCHEMA = "wog-dump.catalog/3"

# Scene directories for disassembly states ("<weapon>-<state>") are
# variants, not catalog rows
STATE_DIR_SUFFIXES = ("-field-strip", "-exploded", "-parts")

# Pipeline stages, ordered; --sort status orders by how far a weapon got
STAGES = ("has_key", "downloaded", "decrypted", "unpacked", "scene")


@dataclass
class WeaponEntry:
    """One weapon's pipeline status."""

    name: str
    has_key: bool = False
    downloaded: bool = False
    decrypted: bool = False
    unpacked: bool = False
    scene: bool = False
    previews: bool = False
    size_bytes: int = 0
    # Spider bundle metadata (all optional: present once the bundle was
    # downloaded and parsed)
    display_name: str | None = None
    categories: tuple[str, ...] = ()
    num_parts: int | None = None
    since: int | None = None
    icon: bool = False
    # Server-side test marker: the game hides the model, the asset
    # bundle is not deployed yet, so downloads will 404
    test: bool = False

    @property
    def stages_passed(self) -> int:
        return sum(getattr(self, stage) for stage in STAGES)


def build_catalog(config: WOGConfig | None = None) -> list[WeaponEntry]:
    """Collect per-weapon status from the database and runtime folders."""
    config = config or get_config()

    encrypted = _list_files(config.encrypted_dir, ".bytes")
    decrypted = _list_files(config.decrypted_dir, ".unity3d")
    unpacked = _list_dirs(config.base_dir / "runtime" / "unpacked")
    scenes = _list_dirs(config.base_dir / "runtime" / "scenes")
    previews = _list_dirs(config.base_dir / "runtime" / "scenes", suffix="previews")
    icons = _list_files(previews_dir(config), ICON_SUFFIX)

    names: set[str] = set(encrypted) | set(decrypted)
    names.update(unpacked)
    names.update(
        name
        for name in scenes
        if not any(name.endswith(suffix) for suffix in STATE_DIR_SUFFIXES)
    )
    meta: dict[str, dict] = {}
    try:
        db = WOGDatabase(config)
        names.update(db.get_weapons())
        keys = set(db.get_keys())
        meta = db.get_weapon_meta()
    except Exception:
        keys = set()
    # System assets (the spider catalog bundle) are infrastructure, not
    # catalog rows; keep them out of every listing
    names -= SYSTEM_ASSETS

    entries = []
    for name in sorted(names):
        state_dir = config.base_dir / "runtime" / "scenes" / name
        record = meta.get(name, {})
        entries.append(
            WeaponEntry(
                name=name,
                has_key=name in keys,
                downloaded=name in encrypted,
                decrypted=name in decrypted,
                unpacked=name in unpacked,
                scene=(state_dir / f"{name}.glb").exists(),
                previews=name in previews,
                size_bytes=encrypted.get(name, 0) + decrypted.get(name, 0),
                display_name=record.get("full_name"),
                categories=tuple(category_leaves(record.get("category"))),
                num_parts=record.get("num_parts"),
                since=record.get("since"),
                icon=name in icons,
                test="test" in (record.get("flags") or "").split(","),
            )
        )
    return entries


def _list_files(directory: Path, suffix: str) -> dict[str, int]:
    """One scandir pass: name -> file size."""
    result: dict[str, int] = {}
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                if entry.is_file() and entry.name.endswith(suffix):
                    result[Path(entry.name).stem] = entry.stat().st_size
    except OSError:
        return {}
    return result


def _list_dirs(directory: Path, suffix: str | None = None) -> set[str]:
    """One scandir pass over subdirectory names."""
    result: set[str] = set()
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                if not entry.is_dir():
                    continue
                if suffix is not None:
                    if (Path(entry.path) / suffix).is_dir():
                        result.add(entry.name)
                else:
                    result.add(entry.name)
    except OSError:
        return set()
    return result
