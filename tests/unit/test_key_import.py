"""Unit tests for the key_import module."""

from __future__ import annotations

import bz2
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.key_import import (
    KeyImportError,
    decode_api_body,
    dedupe_keys,
    extract_keys,
    extract_keys_from_flows,
    extract_keys_from_saz,
    merge_keys,
)
from wog_dump.core.storage import DataStorageManager

KEY_AK47 = "46f6a1894df9c632350121ef941bbac9"
KEY_HUDSON = "a" * 32


def make_request(model: str) -> bytes:
    """Build an API request body like the game client sends."""
    compressed = bz2.compress(f"query=3&model={model}&need_details=1".encode())
    return len(compressed).to_bytes(4, "little") + compressed


def make_response(key: str | None = None, result: str = "0") -> bytes:
    """Build an API response body (success carries sync=<key>)."""
    sync_part = f"&sync={key}" if key else ""
    body = f"00000398&query=3&time=1&result={result}{sync_part}"
    compressed = bz2.compress(body.encode())
    return len(compressed).to_bytes(4, "little") + compressed


class TestDecodeApiBody:
    def test_length_prefixed_bz2(self) -> None:
        assert (
            decode_api_body(make_request("ak47")) == "query=3&model=ak47&need_details=1"
        )

    def test_plain_text(self) -> None:
        assert decode_api_body(b"result=0&sync=abc") == "result=0&sync=abc"

    def test_empty(self) -> None:
        assert decode_api_body(b"") is None


class TestFlatFiles:
    def test_request_response_in_one_text_file(self, temp_dir: Path) -> None:
        dump = temp_dir / "capture.txt"
        dump.write_text(
            f"query=3&model=hudson_h9&need_details=1\nresult=0&sync={KEY_HUDSON}\n"
        )

        captured = extract_keys(dump)
        assert len(captured) == 1
        assert captured[0].model == "hudson_h9"
        assert captured[0].key == KEY_HUDSON

    def test_bz2_bodies_with_http_headers(self, temp_dir: Path) -> None:
        dump = temp_dir / "session.txt"
        dump.write_bytes(
            b"PUT /v/query2018.php?soc=steam HTTP/1.1\r\n"
            b"Host: eu1.ultimate-disassembly.com\r\n\r\n"
            + make_request("ak47")
            + b"HTTP/1.1 200 OK\r\nContent-Type: octet-stream\r\n\r\n"
            + make_response(KEY_AK47)
        )

        captured = extract_keys(dump)
        assert [c.model for c in captured] == ["ak47"]

    def test_request_only_yields_nothing(self, temp_dir: Path) -> None:
        dump = temp_dir / "request.txt"
        dump.write_bytes(make_request("ak47"))
        assert extract_keys(dump) == []

    def test_ambiguous_file_yields_nothing(self, temp_dir: Path) -> None:
        dump = temp_dir / "two_sessions.txt"
        dump.write_text(
            f"model=ak47&x=1 result=0&sync={KEY_AK47} "
            f"model=m4a1 result=0&sync={KEY_HUDSON}"
        )
        assert extract_keys(dump) == []

    def test_directory_of_dumps(self, temp_dir: Path) -> None:
        (temp_dir / "01.txt").write_text(f"model=ak47 result=0&sync={KEY_AK47}")
        subdir = temp_dir / "nested"
        subdir.mkdir()
        (subdir / "02.txt").write_text(f"model=uru result=0&sync={KEY_HUDSON}")
        (subdir / "noise.txt").write_text("unrelated traffic")

        captured = extract_keys(temp_dir)
        assert {c.model for c in captured} == {"ak47", "uru"}

    def test_missing_path_raises(self, temp_dir: Path) -> None:
        with pytest.raises(KeyImportError):
            extract_keys(temp_dir / "does_not_exist.flows")


class TestSazArchive:
    def test_extract_pairs(self, temp_dir: Path) -> None:
        saz = temp_dir / "capture.saz"
        with zipfile.ZipFile(saz, "w") as archive:
            archive.writestr(
                "raw/001_c.txt",
                b"PUT /v/query2018.php HTTP/1.1\r\nHost: x\r\n\r\n"
                + make_request("ak47"),
            )
            archive.writestr(
                "raw/001_s.txt",
                b"HTTP/1.1 200 OK\r\n\r\n" + make_response(KEY_AK47),
            )
            # A rejected query must not produce a key
            archive.writestr(
                "raw/002_c.txt",
                b"PUT /v/query2018.php HTTP/1.1\r\nHost: x\r\n\r\n"
                + make_request("hudson_h9"),
            )
            archive.writestr(
                "raw/002_s.txt",
                b"HTTP/1.1 200 OK\r\n\r\n" + make_response(result="1000"),
            )
            archive.writestr("raw/001_m.xml", "<Session />")

        captured = extract_keys_from_saz(saz)
        assert [(c.model, c.key) for c in captured] == [("ak47", KEY_AK47)]

    def test_invalid_archive_raises(self, temp_dir: Path) -> None:
        bad = temp_dir / "bad.saz"
        bad.write_bytes(b"not a zip")
        with pytest.raises(KeyImportError):
            extract_keys_from_saz(bad)


class TestFlowsFile:
    def _write_flow_file(
        self, path: Path, transactions: list[tuple[str, str, bytes, bytes]]
    ) -> None:
        """transactions: (host, url_path, request_body, response_body)."""
        from mitmproxy import http as mitm_http
        from mitmproxy import io as mitm_io

        try:  # mitmproxy >= 12
            from mitmproxy.connection import Client, Server

            client = Client(peername=("127.0.0.1", 1), sockname=("127.0.0.1", 2))
            server = Server(address=("eu1.ultimate-disassembly.com", 443))
        except ImportError:  # mitmproxy < 12
            from mitmproxy.connections import ClientConnection, ServerConnection

            client = ClientConnection.make(("127.0.0.1", 1), ("127.0.0.1", 2))
            server = ServerConnection.make(("eu1.ultimate-disassembly.com", 443))

        with open(path, "wb") as f:
            writer = mitm_io.FlowWriter(f)
            for host, url_path, request_body, response_body in transactions:
                flow = mitm_http.HTTPFlow(client, server, live=False)
                flow.request = mitm_http.Request.make(
                    "PUT", f"https://{host}{url_path}", request_body
                )
                flow.response = mitm_http.Response.make(200, response_body)
                writer.add(flow)

    def test_extract_pairs(self, temp_dir: Path) -> None:
        pytest.importorskip("mitmproxy")
        flows = temp_dir / "capture.flows"
        self._write_flow_file(
            flows,
            [
                (
                    "eu1.ultimate-disassembly.com",
                    "/v/query2018.php?soc=steam",
                    make_request("ak47"),
                    make_response(KEY_AK47),
                ),
                # Rejected query: response carries no sync key
                (
                    "eu1.ultimate-disassembly.com",
                    "/v/query2018.php?soc=steam",
                    make_request("hudson_h9"),
                    make_response(result="1000"),
                ),
                # Unrelated host with matching-looking bodies is skipped
                (
                    "example.com",
                    "/v/query2018.php",
                    make_request("uru"),
                    make_response(KEY_HUDSON),
                ),
            ],
        )

        captured = extract_keys_from_flows(flows)
        assert [(c.model, c.key) for c in captured] == [("ak47", KEY_AK47)]

    def test_missing_mitmproxy_raises(
        self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flows = temp_dir / "capture.flows"
        flows.write_bytes(b"opaque")
        monkeypatch.setitem(sys.modules, "mitmproxy", None)

        with pytest.raises(KeyImportError, match="mitmproxy package"):
            extract_keys_from_flows(flows)


class TestDedupeAndMerge:
    def test_dedupe_last_wins(self) -> None:
        captured = [
            ("ak47", KEY_AK47, "a"),
            ("ak47", KEY_AK47, "b"),  # duplicate capture
            ("uru", KEY_HUDSON, "c"),
        ]
        from wog_dump.core.key_import import CapturedKey

        keys = dedupe_keys([CapturedKey(*c) for c in captured])
        assert keys == {"ak47": KEY_AK47, "uru": KEY_HUDSON}

    def test_merge_preserves_existing_keys(self, test_config: WOGConfig) -> None:
        storage = DataStorageManager(test_config)
        storage.save_keys({"ak74n": "old_key"})

        result = merge_keys(
            {"ak74n": "new_key", "hudson_h9": KEY_HUDSON}, config=test_config
        )

        assert (result.added, result.updated, result.total) == (1, 1, 2)

        stored = DataStorageManager(test_config).get_keys()
        assert stored == {"ak74n": "new_key", "hudson_h9": KEY_HUDSON}

        # The legacy txt file is refreshed with the full merged set
        legacy = test_config.keys_file.read_text().splitlines()
        assert "ak74n new_key" in legacy
        assert f"hudson_h9 {KEY_HUDSON}" in legacy


class TestExtractScript:
    def test_script_runs_on_flat_capture(self, temp_dir: Path) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        capture = temp_dir / "capture.txt"
        capture.write_text(f"model=ak47 result=0&sync={KEY_AK47}")

        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "extract_keys.py"),
                str(capture),
            ],
            capture_output=True,
            text=True,
            cwd=temp_dir,
        )

        assert result.returncode == 0, result.stderr
        assert "ak47" in result.stdout
        assert KEY_AK47 in result.stdout

    def test_script_fails_when_nothing_found(self, temp_dir: Path) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        capture = temp_dir / "empty.txt"
        capture.write_text("nothing useful")

        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "extract_keys.py"),
                str(capture),
            ],
            capture_output=True,
            text=True,
            cwd=temp_dir,
        )

        assert result.returncode == 1
        assert "No model/key pairs found" in result.stdout
