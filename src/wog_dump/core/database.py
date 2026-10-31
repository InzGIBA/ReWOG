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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as db:
            db.executescript(_SCHEMA)
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            # Windows FAT or unusual filesystems: nothing to do
            pass

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
        """Replace the stored weapon list."""
        if not weapons:
            return
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM weapons")
            db.executemany(
                "INSERT INTO weapons (name) VALUES (?)",
                [(name,) for name in weapons],
            )
        self.logger.info(f"Saved {len(weapons)} weapons to {self.db_path}")

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
        self, new_keys: dict[str, str], source: str | None = None
    ) -> tuple[int, int]:
        """Merge keys into the store; returns (added, updated). Existing keys are kept."""
        if not new_keys:
            return (0, 0)

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
                "updated_at = datetime('now')",
                [(model, key, source) for model, key in new_keys.items()],
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
                "session": identity_row[1] if identity_row else None,
                "updated_at": identity_row[2] if identity_row else None,
            },
            "account": {
                "email": self.get_setting(SETTINGS_EMAIL),
                "has_device_id": self.get_setting(SETTINGS_DEVICE_ID) is not None,
            },
        }

    # ---------------------------------------------------------- maintenance

    def clear_cached_data(self) -> None:
        """Delete weapons and keys (credentials and identity are kept)."""
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM weapons")
            db.execute("DELETE FROM keys")
        self.logger.info(f"Cleared cached weapons and keys in {self.db_path}")

    def import_legacy_txt(
        self, weapons_file: Path | None = None, keys_file: Path | None = None
    ) -> bool:
        """One-time import from the legacy runtime txt files.

        Returns True when at least one file was imported.
        """
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
                self.save_weapons(weapons)
                self.logger.info(f"Imported {len(weapons)} weapons from {weapons_file}")
                imported = True

        if keys_file.exists():
            keys: dict[str, str] = {}
            for line in keys_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    keys[parts[0]] = parts[1]
            if keys:
                self.merge_keys(keys, source="legacy_import")
                self.logger.info(f"Imported {len(keys)} keys from {keys_file}")
                imported = True

        return imported
