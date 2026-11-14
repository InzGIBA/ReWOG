"""Localization tests: dictionary completeness and Russian display names."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")

from wog_plugin_web.app import create_app  # noqa: E402
from wog_plugin_web.i18n import STRINGS  # noqa: E402

from wog_dump.core.config import set_config  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "wog_plugin_web" / "static"


class TestDictionary:
    def test_same_keys_en_ru(self) -> None:
        assert set(STRINGS) == {"en", "ru"}
        assert set(STRINGS["en"]) == set(STRINGS["ru"])

    def test_values_are_nonempty_strings(self) -> None:
        for lang, table in STRINGS.items():
            for key, value in table.items():
                assert isinstance(value, str) and value.strip(), (lang, key)

    def test_ui_literal_keys_exist(self) -> None:
        """Every t('key') literal in the shell resolves in both locales."""
        used: set[str] = set()
        for name in ("app.js", "index.html"):
            text = (STATIC_DIR / name).read_text(encoding="utf-8")
            used.update(re.findall(r"\bt\('([A-Za-z0-9_]+)'", text))
        assert used, "no t() calls found"
        missing = used - set(STRINGS["en"])
        assert not missing, f"missing i18n keys: {sorted(missing)}"

    def test_ui_dynamic_keys_exist(self) -> None:
        """Status/flag/stepper tables map to keys through t() at runtime."""
        text = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        dynamic: set[str] = set()
        status_block = re.search(r"STATUS_KEYS: \{(.*?)\}", text, re.S)
        assert status_block
        dynamic.update(re.findall(r"'([A-Za-z0-9_]+)'", status_block.group(1)))
        dynamic.update(re.findall(r"\['[a-z_]+', '([A-Za-z0-9_]+)'\]", text))
        missing = dynamic - set(STRINGS["en"])
        assert not missing, f"missing i18n keys: {sorted(missing)}"


@pytest.fixture
def client(tmp_path: Path) -> Any:
    encrypted = tmp_path / "runtime" / "encrypted"
    encrypted.mkdir(parents=True)
    (encrypted / "ak47.bytes").write_bytes(b"x" * 100)
    set_config(base_dir=tmp_path, db_path=tmp_path / "wog_dump.db")
    app = create_app()
    yield TestClient(app), app.state.token
    app.state.jobs.stop()


class TestShellAndApi:
    def test_shell_injects_both_languages(self, client: Any) -> None:
        tc, token = client
        html = tc.get(f"/t/{token}/").text
        assert "window.WOG_I18N" in html
        assert "__I18N__" not in html
        assert "каталог" in html
        assert "задачи" in html

    def test_api_i18n_serves_tables(self, client: Any) -> None:
        tc, token = client
        payload = tc.get(f"/t/{token}/api/i18n").json()
        assert payload == STRINGS

    def test_language_toggle_present(self, client: Any) -> None:
        tc, token = client
        html = tc.get(f"/t/{token}/").text
        assert "setLang('en')" in html
        assert "setLang('ru')" in html


class TestRussianNames:
    def test_catalog_and_detail_carry_display_name_ru(self, client: Any) -> None:
        from wog_dump.core.database import WOGDatabase

        tc, token = client
        db = WOGDatabase()
        db.save_weapon_meta(
            {
                "ak47": {
                    "full_name": "AK-47",
                    "full_name_ru": "АК-47",
                    "brief_info": "English brief",
                    "brief_info_ru": "Русское описание",
                    "features": "English features",
                    "features_ru": "Русские особенности",
                }
            }
        )
        catalog = tc.get(f"/t/{token}/api/catalog").json()
        entry = next(w for w in catalog["weapons"] if w["name"] == "ak47")
        assert entry["display_name"] == "AK-47"
        assert entry["display_name_ru"] == "АК-47"

        detail = tc.get(f"/t/{token}/api/weapons/ak47").json()
        assert detail["display_name_ru"] == "АК-47"
        assert detail["brief_info_ru"] == "Русское описание"
        assert detail["features_ru"] == "Русские особенности"

    def test_display_name_ru_null_without_metadata(self, client: Any) -> None:
        tc, token = client
        catalog = tc.get(f"/t/{token}/api/catalog").json()
        entry = next(w for w in catalog["weapons"] if w["name"] == "ak47")
        assert entry["display_name_ru"] is None
