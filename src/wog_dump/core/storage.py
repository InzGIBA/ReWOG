"""Modern JSON-based data storage for WOG Dump with caching support."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .. import __version__
from ..utils.logging import get_logger
from .config import WOGConfig, get_config


class StorageError(Exception):
    """Raised when storage operations fail."""


class CacheMetadata(BaseModel):
    """Cache metadata with timestamps and validation."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = Field(default=__version__)
    source: str = Field(default="wog_dump")
    checksum: str | None = None

    def update_timestamp(self) -> None:
        """Update the last modified timestamp."""
        self.updated_at = datetime.now(timezone.utc)

    def is_expired(self, max_age_hours: int = 24) -> bool:
        """Check if cache is expired based on max age."""
        now = datetime.now(timezone.utc)
        age_hours = (now - self.updated_at).total_seconds() / 3600
        return age_hours > max_age_hours


class WeaponData(BaseModel):
    """Weapon list data with metadata."""

    weapons: list[str] = Field(default_factory=list)
    count: int = Field(default=0)
    filtered: bool = Field(default=True)
    source_asset: str | None = None
    blacklist_applied: bool = Field(default=True)

    @model_validator(mode="after")
    def _sync_count(self) -> WeaponData:
        """Derive count from the weapon list when not provided."""
        if self.count == 0 and self.weapons:
            self.count = len(self.weapons)
        return self


class KeyData(BaseModel):
    """Decryption keys data with metadata."""

    keys: dict[str, str] = Field(default_factory=dict)
    count: int = Field(default=0)
    validation_enabled: bool = Field(default=True)

    @model_validator(mode="after")
    def _sync_count(self) -> KeyData:
        """Derive count from the keys mapping when not provided."""
        if self.count == 0 and self.keys:
            self.count = len(self.keys)
        return self


class WOGDataStore(BaseModel):
    """Unified data store for WOG Dump with caching support."""

    # Data sections
    weapons: WeaponData = Field(default_factory=WeaponData)
    keys: KeyData = Field(default_factory=KeyData)

    # Cache metadata
    cache: CacheMetadata = Field(default_factory=CacheMetadata)

    # Configuration snapshot
    config_snapshot: dict[str, Any] = Field(default_factory=dict)

    def update_cache_metadata(self) -> None:
        """Update cache metadata with current timestamp."""
        self.cache.update_timestamp()

    def is_cache_expired(self, max_age_hours: int = 24) -> bool:
        """Check if any cached data is expired."""
        return self.cache.is_expired(max_age_hours)

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive statistics about stored data."""
        return {
            "weapons": {
                "count": self.weapons.count,
                "filtered": self.weapons.filtered,
                "source_asset": self.weapons.source_asset,
            },
            "keys": {
                "count": self.keys.count,
                "validation_enabled": self.keys.validation_enabled,
            },
            "cache": {
                "created_at": self.cache.created_at.isoformat(),
                "updated_at": self.cache.updated_at.isoformat(),
                "version": self.cache.version,
                "expired": self.is_cache_expired(),
            },
        }


class DataStorageManager:
    """Manager for JSON-based data storage with caching capabilities."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()

        # Use data.json instead of separate txt files
        self.data_file = self.config.base_dir / "runtime" / "data.json"
        self._data: WOGDataStore | None = None

    @property
    def data(self) -> WOGDataStore:
        """Get current data store, loading from file if needed."""
        if self._data is None:
            self.load_data()
            assert self._data is not None
        return self._data

    def load_data(self) -> WOGDataStore:
        """Load data from JSON file with error handling."""
        if not self.data_file.exists():
            self.logger.info("No existing data file found, creating new data store")
            self._data = WOGDataStore()
            return self._data

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data_dict = json.load(f)

            self._data = WOGDataStore(**data_dict)
            self.logger.info(f"Loaded data from {self.data_file}")

            if self._data.is_cache_expired():
                self.logger.info("Cache is expired, consider refreshing data")

            return self._data

        except (json.JSONDecodeError, ValueError) as e:
            self.logger.error(f"Failed to parse data file: {e}")
            self.logger.info("Creating new data store due to parse error")
            self._data = WOGDataStore()
            return self._data

        except OSError as e:
            self.logger.error(f"Failed to load data file: {e}")
            self._data = WOGDataStore()
            return self._data

    def save_data(self, backup: bool = True) -> None:
        """Save data to JSON file with optional backup."""
        if self._data is None:
            raise StorageError("No data to save")

        try:
            # Update cache metadata and store a config snapshot
            self._data.update_cache_metadata()
            self._data.config_snapshot = self.config.get_stats()

            # Create backup if requested and file exists
            if backup and self.data_file.exists():
                backup_path = self.data_file.with_suffix(".json.bak")
                backup_path.write_bytes(self.data_file.read_bytes())
                self.logger.debug(f"Created backup: {backup_path}")

            self.data_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(
                    self._data.model_dump(),
                    f,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )

            self.logger.info(f"Saved data to {self.data_file}")

        except OSError as e:
            raise StorageError(f"Failed to save data: {e}") from e

    def get_weapons(self) -> list[str]:
        """Get weapon list from data store."""
        return self.data.weapons.weapons

    def save_weapons(
        self,
        weapons: list[str],
        source_asset: str | None = None,
        filtered: bool = True,
    ) -> None:
        """Save weapon list to data store."""
        if not weapons:
            raise StorageError("Cannot save empty weapon list")

        self.data.weapons = WeaponData(
            weapons=weapons,
            count=len(weapons),
            filtered=filtered,
            source_asset=source_asset,
            blacklist_applied=filtered,
        )

        self.save_data()
        self.logger.info(f"Saved {len(weapons)} weapons to data store")

    def get_keys(self) -> dict[str, str]:
        """Get decryption keys from data store."""
        return self.data.keys.keys

    def save_keys(self, keys: dict[str, str]) -> None:
        """Save decryption keys to data store."""
        if not keys:
            raise StorageError("Cannot save empty keys")

        self.data.keys = KeyData(
            keys=keys,
            count=len(keys),
            validation_enabled=self.config.enable_validation,
        )

        self.save_data()
        self.logger.info(f"Saved {len(keys)} keys to data store")

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._data = WOGDataStore()
        self.save_data()
        self.logger.info("Cleared all cached data")

    def get_cache_stats(self) -> dict[str, Any]:
        """Get detailed cache statistics."""
        return self.data.get_stats()

    def migrate_from_txt_files(
        self,
        weapons_file: Path | None = None,
        keys_file: Path | None = None,
    ) -> bool:
        """Migrate data from legacy txt files."""
        weapons_file = weapons_file or (
            self.config.base_dir / "runtime" / "weapons.txt"
        )
        keys_file = keys_file or (self.config.base_dir / "runtime" / "keys.txt")

        migrated = False

        # Migrate weapons
        if weapons_file.exists():
            try:
                weapons = []
                with open(weapons_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            weapons.append(line)

                if weapons:
                    self.save_weapons(weapons, source_asset="migrated_from_txt")
                    self.logger.info(
                        f"Migrated {len(weapons)} weapons from {weapons_file}"
                    )
                    migrated = True

            except OSError as e:
                self.logger.error(f"Failed to migrate weapons: {e}")

        # Migrate keys
        if keys_file.exists():
            try:
                keys = {}
                with open(keys_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            parts = line.split(" ", 1)
                            if len(parts) == 2:
                                keys[parts[0]] = parts[1]

                if keys:
                    self.save_keys(keys)
                    self.logger.info(f"Migrated {len(keys)} keys from {keys_file}")
                    migrated = True

            except OSError as e:
                self.logger.error(f"Failed to migrate keys: {e}")

        return migrated


def write_legacy_keys_file(keys_file: Path, keys: dict[str, str]) -> None:
    """Write keys in the legacy txt format (``model key`` per line).

    The JSON data store is the source of truth; this file is kept for
    backward compatibility and for sharing key bundles.
    """
    keys_file.parent.mkdir(parents=True, exist_ok=True)

    with open(keys_file, "w", encoding="utf-8") as f:
        f.write("# WOG Dump Decryption Keys (Legacy Format)\n")
        f.write("# This file is deprecated, use data.json instead\n")
        f.write("# Format: weapon_name decryption_key\n\n")

        for model, key in sorted(keys.items()):
            f.write(f"{model} {key}\n")
