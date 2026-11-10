"""CLI message localization through gettext.

English is the source language: msgids are the English strings. A
missing or unknown locale silently falls back to English, so an
incomplete catalog never breaks a command.

Locale selection order: the ``--locale`` CLI flag wins over
``WOG_LOCALE`` (environment or ``.env``). ``set_locale`` must run
before the first ``t()`` call, which the CLI group callback does.
"""

from __future__ import annotations

import gettext
from importlib import resources
from pathlib import Path

__all__ = ["available_locales", "get_locale", "set_locale", "t", "tn"]

_DOMAIN = "wog_dump"
_translation: gettext.NullTranslations | None = None
_current: str = "en"


def available_locales() -> tuple[str, ...]:
    """Locale codes with a compiled catalog shipped in the package."""
    locales_dir = resources.files("wog_dump") / "resources" / "locales"
    codes: list[str] = []
    try:
        for entry in locales_dir.iterdir():
            mo = entry / "LC_MESSAGES" / f"{_DOMAIN}.mo"
            if mo.is_file():
                codes.append(entry.name)
    except OSError:
        return ()
    return tuple(sorted(codes))


def set_locale(locale: str) -> None:
    """Install the translation catalog for ``locale`` (en stays untranslated)."""
    global _translation, _current
    _current = locale
    if locale == "en":
        _translation = None
        return
    locales_dir = Path(str(resources.files("wog_dump"))) / "resources" / "locales"
    _translation = gettext.translation(
        _DOMAIN, localedir=locales_dir, languages=[locale], fallback=True
    )


def get_locale() -> str:
    """The active locale code (en when nothing was installed)."""
    return _current


def t(msgid: str, **kwargs: object) -> str:
    """Translate one message and format it.

    Keyword arguments substitute ``{name}`` placeholders after
    translation, so catalogs never re-order user data.
    """
    text = _translation.gettext(msgid) if _translation is not None else msgid
    if kwargs:
        text = text.format(**kwargs)
    return text


def tn(msgid: str, msgid_plural: str, n: int, **kwargs: object) -> str:
    """Translate a message with plural forms (catalogs carry the rules).

    ``n`` always substitutes the ``{n}`` placeholder.
    """
    if _translation is not None:
        text = _translation.ngettext(msgid, msgid_plural, n)
    else:
        text = msgid if n == 1 else msgid_plural
    return text.format(n=n, **kwargs)
