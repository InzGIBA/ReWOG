"""Unit tests for the local SQLite database."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.database import WOGDatabase


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


class TestWeapons:
    def test_save_and_load(
        self, test_db: WOGDatabase, sample_weapon_list: list[str]
    ) -> None:
        test_db.save_weapons(sample_weapon_list)
        assert test_db.get_weapons() == sorted(sample_weapon_list)

    def test_save_replaces(self, test_db: WOGDatabase) -> None:
        test_db.save_weapons(["a", "b"])
        test_db.save_weapons(["c"])
        assert test_db.get_weapons() == ["c"]

    def test_empty_save_is_noop(self, test_db: WOGDatabase) -> None:
        test_db.save_weapons(["a"])
        test_db.save_weapons([])
        assert test_db.get_weapons() == ["a"]


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
