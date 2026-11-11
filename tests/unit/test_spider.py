"""Unit tests for the spider system asset (metadata and previews)."""

from __future__ import annotations

from pathlib import Path

from wog_dump.core.config import WOGConfig
from wog_dump.core.database import WOGDatabase
from wog_dump.core.spider import (
    SPIDER_ASSET,
    SYSTEM_ASSETS,
    category_leaves,
    extract_spider_content,
    parse_weapon_meta,
    previews_dir,
    spider_asset_path,
)

# A trimmed copy of the real "<weapon>" record format (game-server
# content: tab-separated keys, quoted strings, bare numbers)
AK47_RECORD = """\
full_name_ru     \t= "Автомат Калашникова"
short_name_ru    \t= "АК-47"
full_name       \t\t= "AK-47"
short_name       \t= "AK-47"
youtube        \t\t= "3lhsoi0d9PA"
wikipedia       \t= "AK-47"
category       \t\t= "Guns\\Assault Rifles"
registered_order \t= 6
since \t\t\t= 1949
features \t\t= "96 parts, bayonet, 2 fire modes"
num_parts\t\t= 96
unknown_future_field = "whatever"
released = 2026 5 20
flags = test
"""

PROSE_ASSET = """\
The AK-47 (also known as Kalashnikov) is a selective-fire
gas-operated 7.62x39mm assault rifle.
"""


class TestParseWeaponMeta:
    def test_parses_known_fields(self) -> None:
        record = parse_weapon_meta(AK47_RECORD)
        assert record["full_name"] == "AK-47"
        assert record["full_name_ru"] == "Автомат Калашникова"
        assert record["category"] == "Guns\\Assault Rifles"
        assert record["registered_order"] == 6
        assert record["since"] == 1949
        assert record["num_parts"] == 96
        assert record["features"] == "96 parts, bayonet, 2 fire modes"

    def test_drops_unknown_fields(self) -> None:
        assert "unknown_future_field" not in parse_weapon_meta(AK47_RECORD)

    def test_flags_and_released_stay_strings(self) -> None:
        record = parse_weapon_meta(AK47_RECORD)
        assert record["flags"] == "test"
        assert record["released"] == "2026 5 20"

    def test_prose_asset_is_not_a_record(self) -> None:
        assert parse_weapon_meta(PROSE_ASSET) == {}

    def test_malformed_numbers_stay_none(self) -> None:
        record = parse_weapon_meta("since = nineteen-forty-nine\n")
        assert record["since"] is None


class TestCategoryLeaves:
    def test_single_path(self) -> None:
        assert category_leaves("Guns\\Assault Rifles") == ["Assault Rifles"]

    def test_multi_path_with_spaces(self) -> None:
        assert category_leaves("Guns\\Pistols, Guns\\Bullpup") == [
            "Pistols",
            "Bullpup",
        ]

    def test_empty(self) -> None:
        assert category_leaves(None) == []
        assert category_leaves("") == []


class TestSystemPaths:
    def test_asset_path(self, test_config: WOGConfig) -> None:
        assert spider_asset_path(test_config) == (
            test_config.assets_dir / f"{SPIDER_ASSET}.unity3d"
        )

    def test_previews_dir(self, test_config: WOGConfig) -> None:
        assert previews_dir(test_config) == (
            test_config.base_dir / "runtime" / "previews"
        )

    def test_spider_is_a_system_asset(self) -> None:
        assert SPIDER_ASSET in SYSTEM_ASSETS


class TestExtraction:
    def test_missing_bundle_is_not_fatal(self, test_config: WOGConfig) -> None:
        result = extract_spider_content(
            test_config, test_config.assets_dir / "absent.unity3d", ["ak47"]
        )
        assert result.meta_count == 0
        assert result.icons == 0
        assert result.cards == 0

    def test_meta_upsert_and_join(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.save_weapons(["ak47"])
        db.save_weapon_meta(
            {
                "ak47": {
                    "full_name": "AK-47",
                    "category": "Guns\\Assault Rifles",
                    "num_parts": 96,
                    "since": 1949,
                }
            }
        )
        meta = db.get_weapon_meta()
        assert meta["ak47"]["full_name"] == "AK-47"
        assert meta["ak47"]["num_parts"] == 96

    def test_meta_rejects_unsafe_names(self, test_config: WOGConfig) -> None:
        db = WOGDatabase(test_config)
        db.save_weapon_meta({"../evil": {"full_name": "evil"}})
        assert db.get_weapon_meta() == {}

    def test_migrates_columns_into_existing_database(
        self, test_config: WOGConfig
    ) -> None:
        """A 3.2-shaped database (weapon_meta without flags/released)
        opens and gains the new columns without losing its rows."""
        import sqlite3

        legacy_columns = ", ".join(
            f"{name} TEXT"
            for name in WOGDatabase._META_COLUMNS
            if name not in ("flags", "released")
        )
        db_path = test_config.db_path
        with sqlite3.connect(db_path) as raw:
            raw.execute(
                "CREATE TABLE weapon_meta ("
                " name TEXT PRIMARY KEY,"
                f" {legacy_columns},"
                " updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            raw.execute(
                "INSERT INTO weapon_meta (name, full_name) VALUES ('ak47', 'AK-47')"
            )
        reopened = WOGDatabase(test_config)
        reopened.save_weapon_meta({"ak47": {"full_name": "AK-47", "flags": "test"}})
        record = reopened.get_weapon_meta()["ak47"]
        assert record["full_name"] == "AK-47"
        assert record["flags"] == "test"
