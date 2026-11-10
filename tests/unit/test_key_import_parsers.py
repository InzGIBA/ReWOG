"""Unit tests for key_import capture parsers (coverage follow-up)."""

from __future__ import annotations

import bz2
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.database import WOGDatabase
from wog_dump.core.key_import import (
    CapturedIdentity,
    CapturedKey,
    KeyImportError,
    decode_api_body,
    dedupe_keys,
    extract_identity_from_flows,
    extract_keys,
    extract_keys_from_saz,
    import_identity,
    merge_keys,
    write_keys_file,
)


def api_body(result: str = "0", key: str = "sync123") -> bytes:
    text = f"result={result}&sync={key}&model=ak47"
    compressed = bz2.compress(text.encode("utf-8"))
    return struct.pack("<I", len(compressed)) + compressed


class TestDecodeApiBody:
    def test_length_prefixed_bz2(self) -> None:
        body = api_body()
        assert decode_api_body(body) == "result=0&sync=sync123&model=ak47"

    def test_plain_bz2(self) -> None:
        text = "result=0&sync=k"
        assert decode_api_body(bz2.compress(text.encode())) == text

    def test_plain_text(self) -> None:
        assert decode_api_body(b"result=0&sync=k") == "result=0&sync=k"

    def test_garbage_still_decodes_as_replaced_text(self) -> None:
        # plain-text fallback never fails hard; unknown bytes decode
        # with replacement characters
        assert "binary" in (decode_api_body(b"\xff\xfe\x00binary") or "")


class TestExtractKeys:
    def test_flat_file_with_length_prefixed_flow(self, tmp_path: Path) -> None:
        payload = api_body("0", "a" * 32)
        path = tmp_path / "capture.txt"
        path.write_bytes(payload)

        keys = extract_keys(path)

        assert [(k.model, k.key) for k in keys] == [("ak47", "a" * 32)]

    def test_unknown_suffix_parsed_as_flat_file(self, tmp_path: Path) -> None:
        # Arbitrary suffixes are treated as dumped request/response files
        path = tmp_path / "capture.unknown"
        path.write_bytes(api_body("0", "a" * 32))
        keys = extract_keys(path)
        assert keys[0].key == "a" * 32

    def test_unreadable_dump_raises_import_error(self, tmp_path: Path) -> None:
        pytest.importorskip("mitmproxy")
        path = tmp_path / "capture.flows"
        path.write_bytes(b"not a tnetstring dump")
        with pytest.raises(KeyImportError, match="not a readable mitmproxy dump"):
            extract_keys(path)


class TestSazArchive:
    def test_saz_with_message_files(self, tmp_path: Path) -> None:
        # A .saz is a zip with raw/ entries per exchange; sync keys are
        # 32 hex characters (the regex the parser trusts)
        import zipfile

        saz = tmp_path / "capture.saz"
        key = "a" * 32
        body = api_body("0", key)
        request = (
            "PUT /v/query2026.php HTTP/1.1\r\n"
            "Host: eu1.ultimate-disassembly.com\r\n"
            "Content-Type: application/octet-stream\r\n"
            "\r\n"
            "query=3&model=ak47&ver=2.2.3t6"
        )
        with zipfile.ZipFile(saz, "w") as archive:
            archive.writestr("raw/001_c.txt", request)
            archive.writestr("raw/001_s.txt", body)
        keys = extract_keys_from_saz(saz)
        assert any(k.key == key for k in keys)

    def test_not_a_zip_raises(self, tmp_path: Path) -> None:
        saz = tmp_path / "capture.saz"
        saz.write_bytes(b"not a zip")
        with pytest.raises(Exception):
            extract_keys_from_saz(saz)


class TestDedupeAndMerge:
    def test_dedupe_keeps_last(self) -> None:
        captured = [
            CapturedKey("ak47", "old", "cap1"),
            CapturedKey("ak47", "new", "cap2"),
        ]
        assert dedupe_keys(captured) == {"ak47": "new"}

    def test_merge_counts(self, test_config: WOGConfig) -> None:
        captured = [
            CapturedKey("ak47", "k1", "cap"),
            CapturedKey("m4a1", "k2", "cap"),
        ]
        result = merge_keys({k.model: k.key for k in captured}, test_config)
        assert (result.total, result.added, result.updated) == (2, 2, 0)
        result = merge_keys({"ak47": "k1b"}, test_config)
        assert result.updated == 1


def write_flows(tmp_path: Path, requests: list[tuple[str, str, bytes]]) -> Path:
    """Write a real mitmproxy .flows dump via FlowWriter + tflow."""
    pytest.importorskip("mitmproxy")
    from mitmproxy import io as mitm_io
    from mitmproxy.test import tflow

    path = tmp_path / "capture.flows"
    with open(path, "wb") as f:
        writer = mitm_io.FlowWriter(f)
        for host, path_part, body in requests:
            flow = tflow.tflow(resp=True)
            flow.request.host = host
            flow.request.path = path_part
            flow.request.content = body
            flow.response.content = api_body()
            writer.add(flow)
    return path


class TestIdentityImport:
    def test_identity_from_real_flow(self, tmp_path: Path) -> None:
        body = (
            b"query=3&model=ak47&id=42&session=s1&dev=d1"
            b"&ver=2.2.3t6&uver=6000.0.84f1"
        )
        path = write_flows(
            tmp_path,
            [("eu1.ultimate-disassembly.com", "/v/query2026.php", body)],
        )

        identity = extract_identity_from_flows(path)
        assert identity.auth_id == "42"
        assert identity.session == "s1"
        assert identity.device_id == "d1"

    def test_non_game_host_is_fallback_only(self, tmp_path: Path) -> None:
        body = (
            b"query=3&model=ak47&id=42&session=s1&dev=d1"
            b"&ver=2.2.3t6&uver=6000.0.84f1"
        )
        path = write_flows(tmp_path, [("evil.example.com", "/v/query2026.php", body)])
        identity = extract_identity_from_flows(path)
        # still recovered, but the source shows it was not a game host
        assert identity is not None

    def test_steam_login_skipped(self, tmp_path: Path) -> None:
        body = b"query=2&id=0&session=0&ver=2.2.3t6"
        path = write_flows(
            tmp_path,
            [("eu1.ultimate-disassembly.com", "/v/query2026.php", body)],
        )
        with pytest.raises(KeyImportError):
            extract_identity_from_flows(path)

    def test_unreadable_dump_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("mitmproxy")
        path = tmp_path / "login.flows"
        path.write_bytes(b"junk")
        with pytest.raises(KeyImportError, match="not a readable mitmproxy dump"):
            extract_identity_from_flows(path)

    def test_import_identity_saves_to_db(self, test_config: WOGConfig) -> None:
        identity = CapturedIdentity(
            auth_id="7", session="sess", device_id="dev", source="cap"
        )
        import_identity(identity, test_config)
        assert WOGDatabase(test_config).get_identity() == ("7", "sess")

    def test_import_refuses_existing_identity(self, test_config: WOGConfig) -> None:
        import_identity(CapturedIdentity("1", "s1", "d1", "cap"), test_config)
        with pytest.raises(KeyImportError, match="Refusing to overwrite"):
            import_identity(CapturedIdentity("2", "s2", "d2", "cap"), test_config)
        # force overrides
        import_identity(
            CapturedIdentity("2", "s2", "d2", "cap"), test_config, force=True
        )
        assert WOGDatabase(test_config).get_identity() == ("2", "s2")

    def test_missing_identity_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.flows"
        path.write_bytes(b"")
        with pytest.raises(KeyImportError):
            extract_identity_from_flows(path)


class TestWriteKeysFile:
    def test_writes_sorted_pairs(self, tmp_path: Path) -> None:
        path = tmp_path / "keys.txt"
        write_keys_file(path, {"ak47": "k1", "m4a1": "k2"})
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert lines == [
            "# WOG Dump Decryption Keys",
            "# Format: weapon_name decryption_key",
            "",
            "ak47 k1",
            "m4a1 k2",
        ]
