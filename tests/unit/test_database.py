"""Unit tests for the local SQLite database."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import raw_store
from wog_dump.core.config import WOGConfig
from wog_dump.core.database import AssetState, WOGDatabase


class TestDatabaseBasics:
    def test_creates_schema_and_file(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        assert test_config.db_path.exists()
        assert db.get_weapons() == []
        assert db.get_keys() == {}

    def test_restricts_file_permissions(self, test_config: WOGConfig) -> None:
        if os.name != "posix":
            pytest.skip("permission bits are POSIX-only")
        WOGDatabase(test_config)
        mode = stat.S_IMODE(test_config.db_path.stat().st_mode)
        assert mode == 0o600

    def test_statement_groups_close_their_connection(
        self, test_db: WOGDatabase
    ) -> None:
        """Every statement group closes its connection once it ends.

        ``with connection`` alone only commits. The handle then lives on
        until the garbage collector gets to it, and on Windows an open
        handle keeps the store file locked against deletion or rename.
        """
        opened: list[sqlite3.Connection] = []
        connect = WOGDatabase._connect

        def recording_connect(db: WOGDatabase) -> sqlite3.Connection:
            connection = connect(db)
            opened.append(connection)
            return connection

        with patch.object(WOGDatabase, "_connect", recording_connect):
            test_db.set_setting("device_id", "d" * 40)
            assert test_db.get_setting("device_id") == "d" * 40
            # A statement that fails leaves on the same path (rolled back)
            with pytest.raises(sqlite3.ProgrammingError):
                test_db.set_setting("broken", object())  # type: ignore[arg-type]

        assert len(opened) == 3
        for connection in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
        # Nothing holds the store open, so no collector run is needed
        test_db.db_path.unlink()


class TestWeapons:
    def test_save_and_load(
        self, test_db: WOGDatabase, sample_weapon_list: list[str]
    ) -> None:
        test_db.save_weapons(sample_weapon_list)
        assert test_db.get_weapons() == sorted(sample_weapon_list)

    def test_save_replaces(self, test_db: WOGDatabase) -> None:
        test_db.save_weapons(["ak", "bg"])
        test_db.save_weapons(["cz"])
        assert test_db.get_weapons() == ["cz"]

    def test_empty_save_is_noop(self, test_db: WOGDatabase) -> None:
        test_db.save_weapons(["ak"])
        test_db.save_weapons([])
        assert test_db.get_weapons() == ["ak"]


class TestKeys:
    def test_save_and_load(
        self, test_db: WOGDatabase, sample_keys: dict[str, str]
    ) -> None:
        test_db.save_keys(sample_keys)
        assert test_db.get_keys() == sample_keys

    def test_get_key(self, test_db: WOGDatabase, sample_keys: dict[str, str]) -> None:
        test_db.save_keys(sample_keys)
        assert test_db.get_key("mp5") == sample_keys["mp5"]
        assert test_db.get_key("unknown") is None

    def test_merge_keeps_existing(
        self, test_db: WOGDatabase, sample_keys: dict[str, str]
    ) -> None:
        test_db.save_keys({"ak74n": "old" * 10 + "aa"})
        added, updated = test_db.merge_keys(
            {"ak74n": "new" * 10 + "bb", "hudson_h9": "f" * 32}
        )

        assert (added, updated) == (1, 1)
        stored = test_db.get_keys()
        assert stored["hudson_h9"] == "f" * 32
        assert stored["ak74n"] == "new" * 10 + "bb"

    def test_merge_empty(self, test_db: WOGDatabase) -> None:
        assert test_db.merge_keys({}) == (0, 0)


class TestIdentityAndSettings:
    def test_settings_roundtrip(self, test_db: WOGDatabase) -> None:
        assert test_db.get_setting("name") is None
        test_db.set_setting("name", "value")
        assert test_db.get_setting("name") == "value"
        test_db.set_setting("name", "changed")
        assert test_db.get_setting("name") == "changed"
        test_db.delete_setting("name")
        assert test_db.get_setting("name") is None

    def test_identity_roundtrip(self, test_db: WOGDatabase) -> None:
        assert test_db.get_identity() is None
        test_db.save_identity("123", "456")
        assert test_db.get_identity() == ("123", "456")
        test_db.clear_identity()
        assert test_db.get_identity() is None


class TestStatsAndMaintenance:
    def test_stats(
        self, populated_db: WOGDatabase, sample_keys: dict[str, str]
    ) -> None:
        stats = populated_db.get_stats()
        assert stats["weapons"]["count"] == 5
        assert stats["keys"]["count"] == 5
        assert stats["identity"]["auth_id"] is None
        assert stats["account"]["email"] is None

    def test_clear_cached_data_keeps_credentials(
        self, populated_db: WOGDatabase
    ) -> None:
        populated_db.set_setting("account_email", "user@example.com")
        populated_db.save_identity("1", "2")

        populated_db.clear_cached_data()

        assert populated_db.get_weapons() == []
        assert populated_db.get_keys() == {}
        assert populated_db.get_setting("account_email") == "user@example.com"
        assert populated_db.get_identity() == ("1", "2")


class TestLegacyImport:
    def test_import_from_txt(self, test_db: WOGDatabase, temp_dir: Path) -> None:
        weapons_file = temp_dir / "weapons.txt"
        keys_file = temp_dir / "keys.txt"
        weapons_file.write_text("# comment\nak74n\nm4a1\n")
        keys_file.write_text(
            "# comment\nak74n " + "a" * 32 + "\nm4a1 " + "b" * 32 + "\n"
        )

        assert test_db.import_legacy_txt(weapons_file, keys_file) is True
        assert test_db.get_weapons() == ["ak74n", "m4a1"]
        assert test_db.get_keys() == {"ak74n": "a" * 32, "m4a1": "b" * 32}

    def test_import_no_files(self, test_db: WOGDatabase, temp_dir: Path) -> None:
        assert (
            test_db.import_legacy_txt(temp_dir / "nope.txt", temp_dir / "nope2.txt")
            is False
        )


class TestAssetState:
    def test_upsert_get_roundtrip(self, test_db: WOGDatabase) -> None:
        state = AssetState(
            asset="ak74n",
            weapon="ak74n",
            sha256="a" * 64,
            size=1234,
            etag='"v1"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )
        test_db.upsert_asset_state(state)

        loaded = test_db.get_asset_state("ak74n")
        assert loaded is not None
        assert loaded.asset == state.asset
        assert loaded.weapon == state.weapon
        assert loaded.sha256 == state.sha256
        assert loaded.size == state.size
        assert loaded.etag == state.etag
        assert loaded.last_modified == state.last_modified
        assert loaded.checked_at

    def test_upsert_updates_existing(self, test_db: WOGDatabase) -> None:
        test_db.upsert_asset_state(
            AssetState(asset="ak74n", weapon="ak74n", sha256="a" * 64, size=1)
        )
        test_db.upsert_asset_state(
            AssetState(
                asset="ak74n", weapon="ak74n", sha256="b" * 64, size=2, etag='"v2"'
            )
        )

        loaded = test_db.get_asset_state("ak74n")
        assert loaded is not None
        assert loaded.sha256 == "b" * 64
        assert loaded.size == 2
        assert loaded.etag == '"v2"'

    def test_get_missing_returns_none(self, test_db: WOGDatabase) -> None:
        assert test_db.get_asset_state("nope") is None

    def test_list_sorted_and_filtered(self, test_db: WOGDatabase) -> None:
        for name in ("m4a1", "ak74n"):
            test_db.upsert_asset_state(
                AssetState(asset=name, weapon=name, sha256="c" * 64, size=1)
            )

        assert [s.asset for s in test_db.list_asset_states()] == ["ak74n", "m4a1"]
        filtered = test_db.list_asset_states(weapons=["m4a1"])
        assert [s.asset for s in filtered] == ["m4a1"]
        assert test_db.list_asset_states(weapons=[]) == []

    def test_unsafe_names_rejected(self, test_db: WOGDatabase) -> None:
        with pytest.raises(ValueError):
            test_db.upsert_asset_state(
                AssetState(asset="../evil", weapon="ak74n", sha256="d" * 64, size=1)
            )

    def test_migration_creates_table_in_legacy_store(
        self, test_config: WOGConfig
    ) -> None:
        """A store created before asset_state gains it without data loss."""
        with raw_store(test_config.db_path) as conn:
            conn.executescript(
                "CREATE TABLE weapons (name TEXT PRIMARY KEY, added_at TEXT);"
                "INSERT INTO weapons (name, added_at) VALUES ('ak74n', 'now');"
            )

        db = WOGDatabase(test_config)
        assert db.get_weapons() == ["ak74n"]
        db.upsert_asset_state(
            AssetState(asset="ak74n", weapon="ak74n", sha256="e" * 64, size=5)
        )
        assert db.get_asset_state("ak74n") is not None

    def test_migration_adds_missing_columns(self, test_config: WOGConfig) -> None:
        """An early asset_state table gains later columns additively."""
        with raw_store(test_config.db_path) as conn:
            conn.executescript(
                "CREATE TABLE asset_state (asset TEXT PRIMARY KEY);"
                "INSERT INTO asset_state (asset) VALUES ('legacy');"
            )

        db = WOGDatabase(test_config)
        db.upsert_asset_state(
            AssetState(asset="ak74n", weapon="ak74n", sha256="f" * 64, size=7)
        )
        assert db.get_asset_state("ak74n") is not None
        # The pre-existing row survived the migration
        assert db.get_asset_state("legacy") is not None
