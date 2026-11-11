"""Local SQLite storage for WOG Dump.

Keys, the weapon list, the generated device identity and the game
account credentials live in a per-user database outside the repository:
``~/.local/share/wog_dump/wog_dump.db`` on Linux,
``%APPDATA%\\wog_dump\\wog_dump.db`` on Windows,
``~/Library/Application Support/wog_dump/wog_dump.db`` on macOS.

Override the location with the ``WOG_DB_PATH`` environment variable or
the ``db_path`` config field (useful for tests and portable setups).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from .config import WOGConfig, get_config
from .paths import is_safe_weapon_name

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    auth_id    TEXT NOT NULL,
    session    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weapons (
    name     TEXT PRIMARY KEY,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS weapon_meta (
    name             TEXT PRIMARY KEY,
    full_name        TEXT,
    full_name_ru     TEXT,
    short_name       TEXT,
    short_name_ru    TEXT,
    category         TEXT,
    num_parts        INTEGER,
    num_acts         INTEGER,
    since            INTEGER,
    features         TEXT,
    features_ru      TEXT,
    brief_info       TEXT,
    brief_info_ru    TEXT,
    youtube          TEXT,
    wikipedia        TEXT,
    imfdb            TEXT,
    registered_order INTEGER,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keys (
    model      TEXT PRIMARY KEY,
    sync_key   TEXT NOT NULL,
    source     TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Account credentials and identity are per-user secrets: restrict the file
# to the owner where the platform supports it
SETTINGS_EMAIL = "account_email"
SETTINGS_PASSWORD = "account_password"
SETTINGS_DEVICE_ID = "device_id"
# Home shard of the logged-in account, learned from a result=777 login
# redirect (see AuthManager.login)
SETTINGS_API_BASE_URL = "api_base_url"

# One-time marker for the legacy runtime txt import (see import_legacy_txt)
SETTINGS_LEGACY_IMPORT_DONE = "legacy_import_done"


class DatabaseError(Exception):
    """Raised when the local store cannot be opened safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WOGDatabase:
    """SQLite-backed store for weapons, keys, identity and settings."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.db_path = Path(self.config.db_path)
        # Key fetching runs in a thread pool; serialize all access.
        # RLock: methods may call each other while holding the lock.
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        # A symlinked store path would redirect every credential write to
        # the symlink target, so refuse it outright.
        if self.db_path.is_symlink():
            raise DatabaseError(
                f"Refusing to open symlinked database path: {self.db_path}"
            )
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock, self._connect() as db:
            db.executescript(_SCHEMA)
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        try:
            os.chmod(self.db_path, 0o600)
        except OSError as e:
            # Windows FAT or unusual filesystems: report, do not fail
            self.logger.warning(
                f"Could not restrict permissions on {self.db_path}: {e}"
            )

    # ------------------------------------------------------------- settings

    def get_setting(self, name: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, name: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO settings (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, value),
            )

    def delete_setting(self, name: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM settings WHERE name = ?", (name,))

    # ------------------------------------------------------------- identity

    def get_identity(self) -> tuple[str, str] | None:
        """Stored (auth_id, session) from the last successful login."""
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT auth_id, session FROM identity WHERE id = 1"
            ).fetchone()
        return (row[0], row[1]) if row else None

    def save_identity(self, auth_id: str, session: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO identity (id, auth_id, session, updated_at) "
                "VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "auth_id = excluded.auth_id, session = excluded.session, "
                "updated_at = excluded.updated_at",
                (str(auth_id), str(session), _now()),
            )

    def clear_identity(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM identity")

    # -------------------------------------------------------------- weapons

    def get_weapons(self) -> list[str]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT name FROM weapons ORDER BY name").fetchall()
        return [row[0] for row in rows]

    def save_weapons(self, weapons: list[str], source: str | None = None) -> None:
        """Replace the stored weapon list (names must be path-safe)."""
        if not weapons:
            return
        safe_names = [name for name in weapons if is_safe_weapon_name(name)]
        rejected = len(weapons) - len(safe_names)
        if rejected:
            self.logger.warning(
                f"Rejected {rejected} weapon name(s) with unsafe characters"
            )
        if not safe_names:
            return
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM weapons")
            db.executemany(
                "INSERT INTO weapons (name) VALUES (?)",
                [(name,) for name in safe_names],
            )
        self.logger.info(f"Saved {len(safe_names)} weapons to {self.db_path}")

    def merge_weapons(self, weapons: list[str]) -> int:
        """Add weapon names that are not stored yet; returns the added count."""
        safe_names = [name for name in weapons if is_safe_weapon_name(name)]
        if not safe_names:
            return 0
        with self._lock, self._connect() as db:
            existing = {
                row[0] for row in db.execute("SELECT name FROM weapons").fetchall()
            }
            new_names = [name for name in safe_names if name not in existing]
            if new_names:
                db.executemany(
                    "INSERT INTO weapons (name) VALUES (?)",
                    [(name,) for name in new_names],
                )
        return len(new_names)

    # ---------------------------------------------------------- weapon meta

    _META_COLUMNS = (
        "full_name",
        "full_name_ru",
        "short_name",
        "short_name_ru",
        "category",
        "num_parts",
        "num_acts",
        "since",
        "features",
        "features_ru",
        "brief_info",
        "brief_info_ru",
        "youtube",
        "wikipedia",
        "imfdb",
        "registered_order",
    )

    def save_weapon_meta(
        self, records: dict[str, dict[str, Any]]
    ) -> None:
        """Upsert per-weapon metadata parsed from the spider bundle.

        Unknown names are rejected like weapon names — the records end
        up joined against catalog rows in the UI.
        """
        if not records:
            return
        safe = {name: rec for name, rec in records.items() if is_safe_weapon_name(name)}
        if len(safe) != len(records):
            self.logger.warning(
                f"Rejected {len(records) - len(safe)} metadata record(s) "
                "with unsafe names"
            )
        if not safe:
            return
        columns = self._META_COLUMNS
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns)
        with self._lock, self._connect() as db:
            db.executemany(
                "INSERT INTO weapon_meta (name, "
                + ", ".join(columns)
                + ") VALUES (?"
                + ", ?" * len(columns)
                + ") ON CONFLICT(name) DO UPDATE SET "
                + updates
                + ", updated_at = datetime('now')",
                [
                    (name, *(record.get(column) for column in columns))
                    for name, record in safe.items()
                ],
            )
        self.logger.info(f"Saved {len(safe)} weapon metadata records")

    def get_weapon_meta(self) -> dict[str, dict[str, Any]]:
        """All stored metadata records keyed by weapon name."""
        columns = ", ".join(self._META_COLUMNS)
        with self._lock, self._connect() as db:
            rows = db.execute(
                f"SELECT name, {columns} FROM weapon_meta"  # noqa: S608
            ).fetchall()
        return {
            row[0]: dict(zip(self._META_COLUMNS, row[1:]))
            for row in rows
        }

    # ----------------------------------------------------------------- keys

    def get_keys(self) -> dict[str, str]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT model, sync_key FROM keys").fetchall()
        return dict(rows)

    def get_key(self, model: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT sync_key FROM keys WHERE model = ?", (model,)
            ).fetchone()
        return row[0] if row else None

    def save_keys(self, keys: dict[str, str], source: str | None = None) -> None:
        """Replace the whole key set."""
        if not keys:
            return
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM keys")
            db.executemany(
                "INSERT INTO keys (model, sync_key, source) VALUES (?, ?, ?)",
                [(model, key, source) for model, key in keys.items()],
            )
        self.logger.info(f"Saved {len(keys)} keys to {self.db_path}")

    def merge_keys(
        self,
        new_keys: dict[str, str],
        source: str | None = None,
        overwrite: bool = True,
    ) -> tuple[int, int]:
        """Merge keys into the store; returns (added, updated). Existing keys are kept.

        With ``overwrite=False`` an existing row is only replaced when it
        itself came from the same (or no) source - a legacy txt import
        must never silently overwrite fresher server-fetched keys.
        """
        if not new_keys:
            return (0, 0)

        conflict_guard = (
            "" if overwrite else " WHERE keys.source IS NULL OR keys.source = ?"
        )
        rows: list[tuple[Any, ...]] = [
            (model, key, source) for model, key in new_keys.items()
        ]
        if not overwrite:
            rows = [(model, key, source, source) for model, key in new_keys.items()]

        with self._lock, self._connect() as db:
            existing = dict(db.execute("SELECT model, sync_key FROM keys").fetchall())
            added = sum(1 for model in new_keys if model not in existing)
            updated = sum(
                1
                for model, key in new_keys.items()
                if model in existing and existing[model] != key
            )
            db.executemany(
                "INSERT INTO keys (model, sync_key, source) VALUES (?, ?, ?) "
                "ON CONFLICT(model) DO UPDATE SET "
                "sync_key = excluded.sync_key, source = excluded.source, "
                f"updated_at = datetime('now'){conflict_guard}",
                rows,
            )
        self.logger.info(
            f"Merged keys into {self.db_path}: {added} new, {updated} updated"
        )
        return (added, updated)

    # ---------------------------------------------------------------- stats

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as db:
            weapons_count = db.execute("SELECT COUNT(*) FROM weapons").fetchone()[0]
            keys_count = db.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
            identity_row = db.execute(
                "SELECT auth_id, session, updated_at FROM identity WHERE id = 1"
            ).fetchone()

        return {
            "database": str(self.db_path),
            "weapons": {"count": weapons_count},
            "keys": {"count": keys_count},
            "identity": {
                "auth_id": identity_row[0] if identity_row else None,
                # The session token is a live credential: never hand it to
                # presentation code, only report whether one exists.
                "has_session": bool(identity_row[1]) if identity_row else False,
                "updated_at": identity_row[2] if identity_row else None,
            },
            "account": {
                "email": self.get_setting(SETTINGS_EMAIL),
                "has_device_id": self.get_setting(SETTINGS_DEVICE_ID) is not None,
            },
        }

    # ---------------------------------------------------------- maintenance

    def clear_cached_data(self) -> None:
        """Delete weapons and keys (credentials and identity are kept).

        The legacy-import marker is kept as well, so a cleared store
        stays cleared instead of being silently repopulated from the
        working directory's runtime txt files on the next load.
        """
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM weapons")
            db.execute("DELETE FROM keys")
        self.logger.info(f"Cleared cached weapons and keys in {self.db_path}")

    def import_legacy_txt(
        self,
        weapons_file: Path | None = None,
        keys_file: Path | None = None,
        force: bool = False,
    ) -> bool:
        """One-time import from the legacy runtime txt files.

        The import runs at most once per database (override with
        ``force``). The files come from the working directory and are
        treated as untrusted: weapon names must pass the strict name
        allowlist, and keys never overwrite values from a fresher source.

        Returns True when at least one file was imported.
        """
        if not force and self.get_setting(SETTINGS_LEGACY_IMPORT_DONE):
            self.logger.info(
                "Legacy files already imported once; skipping "
                "(pass force=True to import again)"
            )
            return False

        weapons_file = weapons_file or (
            self.config.base_dir / "runtime" / "weapons.txt"
        )
        keys_file = keys_file or (self.config.base_dir / "runtime" / "keys.txt")
        imported = False

        if weapons_file.exists():
            weapons = [
                line.strip()
                for line in weapons_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if weapons:
                added = self.merge_weapons(weapons)
                self.logger.info(
                    f"Imported {added} new weapons from {weapons_file} "
                    f"({len(weapons) - added} already present or rejected)"
                )
                imported = True

        if keys_file.exists():
            keys: dict[str, str] = {}
            for line in keys_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and is_safe_weapon_name(parts[0]):
                    keys[parts[0]] = parts[1]
            if keys:
                self.merge_keys(keys, source="legacy_import", overwrite=False)
                self.logger.info(f"Imported {len(keys)} keys from {keys_file}")
                imported = True

        if imported:
            self.set_setting(SETTINGS_LEGACY_IMPORT_DONE, "1")

        return imported
