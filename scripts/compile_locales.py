"""Compile gettext .po catalogs to .mo files (pure stdlib).

Parses every ``resources/locales/<lang>/LC_MESSAGES/<domain>.po`` in
the package source tree and writes the matching ``.mo`` beside it, so
a git checkout works without a system msgfmt and a wheel ships compiled
catalogs.

Usage: python scripts/compile_locales.py [sources_dir]
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

DOMAIN = "wog_dump"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCALES = REPO_ROOT / "src" / "wog_dump" / "resources" / "locales"

# One entry: keyword lines with quoted (possibly multiline) values
ENTRY_RE = re.compile(
    r'(msgid|msgid_plural|msgstr(?:\[\d+\])?)\s+("(?:[^"\\]|\\.)*"(?:\s*"(?:[^"\\]|\\.)*")*)',
    re.MULTILINE,
)
STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def unquote(text: str) -> str:
    """Concatenate the quoted chunks and decode their escapes."""
    joined = "".join(STRING_RE.findall(text))
    return (
        joined.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def parse_po(text: str) -> dict[str, str]:
    """Parse one .po document into a msgid -> msgstr catalog.

    Plural entries key on the msgid with NUL-separated plural and
    NUL-joined forms, exactly how gettext looks them up at runtime.
    """
    catalog: dict[str, str] = {}
    fields: dict[str, list[str]] = {}
    for match in ENTRY_RE.finditer(text):
        keyword, value = match.group(1), unquote(match.group(2))
        if keyword in fields and keyword == "msgid":
            _store(catalog, fields)
            fields = {}
        fields.setdefault(keyword, []).append(value)
    _store(catalog, fields)
    return catalog


def _store(catalog: dict[str, str], fields: dict[str, list[str]]) -> None:
    msgid = "\x00".join(fields.get("msgid", []))
    if "msgid_plural" in fields:
        plural = "\x00".join(fields["msgid_plural"])
        key = f"{msgid}\x00{plural}"
        forms = []
        index = 0
        while f"msgstr[{index}]" in fields:
            forms.append(fields[f"msgstr[{index}]"][0])
            index += 1
        catalog[key] = "\x00".join(forms)
        return
    catalog[msgid] = fields.get("msgstr", [""])[0]


def compile_mo(catalog: dict[str, str]) -> bytes:
    """Serialize a catalog into the GNU .mo little-endian format.

    Layout: 7-word header, key table, value table, then the NUL-
    terminated key and value byte streams. Table entries are
    (length, absolute offset) pairs.
    """
    keys = sorted(catalog)
    entries: list[tuple[bytes, bytes]] = [
        (key.encode("utf-8"), catalog[key].encode("utf-8")) for key in keys
    ]

    header_size = 7 * 4
    key_table_size = len(entries) * 8
    value_table_offset = header_size + key_table_size
    value_stream_offset = value_table_offset + key_table_size
    key_stream_offset = value_stream_offset + sum(
        len(value) + 1 for _, value in entries
    )

    key_table = b""
    value_table = b""
    key_stream = b""
    value_stream = b""
    for key_bytes, value_bytes in entries:
        key_table += struct.pack(
            "<II", len(key_bytes), key_stream_offset + len(key_stream)
        )
        value_table += struct.pack(
            "<II", len(value_bytes), value_stream_offset + len(value_stream)
        )
        key_stream += key_bytes + b"\x00"
        value_stream += value_bytes + b"\x00"

    header = struct.pack(
        "<7I",
        0x950412DE,  # magic
        0,  # revision
        len(entries),
        header_size,  # key table offset
        value_table_offset,
        0,  # no hash table
        key_stream_offset,
    )
    # Segment order matches the offset math above: value stream first
    return header + key_table + value_table + value_stream + key_stream


def main(argv: list[str]) -> int:
    locales_root = Path(argv[1]) if len(argv) > 1 else DEFAULT_LOCALES
    compiled = 0
    for po_path in sorted(locales_root.glob("*/LC_MESSAGES/*.po")):
        catalog = parse_po(po_path.read_text(encoding="utf-8"))
        mo_path = po_path.with_suffix(".mo")
        mo_path.write_bytes(compile_mo(catalog))
        print(f"{po_path} -> {mo_path} ({len(catalog)} entries)")
        compiled += 1
    if not compiled:
        print(f"no .po catalogs found under {locales_root}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
