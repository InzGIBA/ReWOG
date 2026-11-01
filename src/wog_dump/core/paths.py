"""Path-safety helpers for names that originate outside the tool.

Bundle object names (``m_Name``), weapon names from the server-parsed
weapon list and names imported from legacy txt files all end up in
filesystem paths. They are untrusted input: a crafted name such as
``../../evil`` must never steer a write outside the intended output
directory. Everything that builds a path from such a name goes through
this module.
"""

from __future__ import annotations

import re
from pathlib import Path

# A single safe path component: no separators, no NUL, no leading dot,
# not "." or "..". Used for Unity object names (m_Name).
_UNSAFE_COMPONENT_RE = re.compile(r"[/\\\x00]")

# Weapon/asset names additionally use a strict allowlist: the vendor's
# name alphabet is [a-z0-9_-] (see the shipped weapon list and the
# blacklist in core/config.py), so anything else is rejected outright.
WEAPON_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,49}$")


def safe_object_name(name: str) -> str:
    """Validate a bundle-controlled object name as a single path component.

    Raises ValueError for names containing path separators, NUL bytes,
    absolute or traversal structure, or leading/trailing whitespace.
    """
    if not name or _UNSAFE_COMPONENT_RE.search(name):
        raise ValueError(f"Unsafe object name: {name!r}")
    if name != name.strip() or name in (".", "..") or name.startswith("."):
        raise ValueError(f"Unsafe object name: {name!r}")
    if Path(name).is_absolute():
        raise ValueError(f"Unsafe object name: {name!r}")
    return name


def is_safe_weapon_name(name: str) -> bool:
    """Whether a name matches the strict weapon/asset name allowlist."""
    return bool(WEAPON_NAME_RE.fullmatch(name))


def contained_path(directory: Path, filename: str) -> Path:
    """Join a validated filename onto directory, refusing escapes.

    The filename must be a single safe component (see
    ``safe_object_name``); the returned path is guaranteed to sit
    directly inside ``directory``.
    """
    safe_object_name(filename)
    path = directory / filename
    # A single-component name cannot escape, but verify anyway so a
    # future refactor of the validation cannot silently reopen the hole.
    if path.parent != directory:
        raise ValueError(f"Path escapes {directory}: {path}")
    return path
