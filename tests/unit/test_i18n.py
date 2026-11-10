"""Unit tests for CLI localization (task 08)."""

from __future__ import annotations

import sys
from pathlib import Path

from click.testing import CliRunner

from wog_dump.utils import i18n
from wog_dump.utils.i18n import available_locales, get_locale, set_locale, t, tn

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "compile_locales.py"


def setup_function(function) -> None:
    # Every test starts from the untranslated default
    set_locale("en")


def teardown_function(function) -> None:
    set_locale("en")


class TestFallback:
    def test_english_returns_msgid(self) -> None:
        assert t("No previews rendered") == "No previews rendered"

    def test_unknown_locale_falls_back_to_english(self) -> None:
        set_locale("zz")
        assert t("No previews rendered") == "No previews rendered"
        assert get_locale() == "zz"

    def test_format_kwargs(self) -> None:
        message = t("{operation} failed: {error}", operation="render", error="boom")
        assert message == "render failed: boom"

    def test_plural_english_rule(self) -> None:
        assert tn("{n} weapon shown", "{n} weapons shown", 1) == "1 weapon shown"
        assert tn("{n} weapon shown", "{n} weapons shown", 3) == "3 weapons shown"


class TestRussianCatalog:
    def test_catalog_shipped(self) -> None:
        assert "ru" in available_locales()

    def test_basic_translation(self) -> None:
        set_locale("ru")
        assert t("No previews rendered") == "Ни одного превью не отрендерено"

    def test_message_formatting_after_translation(self) -> None:
        set_locale("ru")
        message = t("{operation} failed: {error}", operation="Рендер", error="бум")
        assert message == "Рендер завершилось с ошибкой: бум"

    def test_ru_plural_forms(self) -> None:
        set_locale("ru")
        # one -> form 0, 3 -> form 1, 5 -> form 2, 21 -> form 0
        assert tn("{n} weapon shown", "{n} weapons shown", 1).endswith("оружие")
        assert tn("{n} weapon shown", "{n} weapons shown", 3).endswith("оружия")
        assert tn("{n} weapon shown", "{n} weapons shown", 5).endswith("оружий")
        assert tn("{n} weapon shown", "{n} weapons shown", 21).endswith("оружие")


class TestCompiler:
    def test_shipped_mo_is_fresh(self) -> None:
        """The committed .mo must match the .po it claims to come from."""
        locales = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "wog_dump"
            / "resources"
            / "locales"
        )
        sys.path.insert(0, str(SCRIPTS.parent))
        try:
            import compile_locales  # type: ignore[import-not-found]
        finally:
            sys.path.remove(str(SCRIPTS.parent))
            sys.modules.pop("compile_locales", None)

        for po_path in locales.glob("*/LC_MESSAGES/*.po"):
            expected = compile_locales.compile_mo(
                compile_locales.parse_po(po_path.read_text(encoding="utf-8"))
            )
            assert (
                po_path.with_suffix(".mo").read_bytes() == expected
            ), f"{po_path} changed: re-run scripts/compile_locales.py"


class TestCliLocale:
    def test_ru_flag_localizes_messages(self, tmp_path: Path) -> None:
        from wog_dump.cli.main import cli
        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--locale", "ru", "render"])
        assert result.exit_code != 0
        assert "Оружие не указано" in result.output

    def test_default_keeps_english(self, tmp_path: Path) -> None:
        from wog_dump.cli.main import cli
        from wog_dump.core.config import set_config

        set_config(base_dir=tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["render"])
        assert result.exit_code != 0
        assert "No weapons requested" in result.output
