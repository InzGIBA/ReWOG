"""Tests for the import-keys CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from wog_plugin_mitm.cli import import_keys
from wog_plugin_mitm.key_import import CapturedIdentity

from wog_dump.core.config import WOGConfig
from wog_dump.core.database import WOGDatabase

KEY = "a" * 32


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_merge_prints_and_persists(
    runner: CliRunner, cli_config: WOGConfig, temp_dir: Path
) -> None:
    capture = temp_dir / "capture.txt"
    capture.write_text(f"model=ak47 result=0&sync={KEY}")

    result = runner.invoke(import_keys, [str(capture), "--merge"])

    assert result.exit_code == 0, result.output
    assert "ak47" in result.output
    assert KEY in result.output
    assert "1 new" in result.output
    assert WOGDatabase(cli_config).get_keys() == {"ak47": KEY}


def test_output_modes_and_exit_codes(
    runner: CliRunner, cli_config: WOGConfig, temp_dir: Path
) -> None:
    capture = temp_dir / "capture.txt"
    capture.write_text(f"model=ak47 result=0&sync={KEY}")

    result = runner.invoke(import_keys, [str(capture), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"ak47": KEY}

    out = temp_dir / "keys.txt"
    result = runner.invoke(import_keys, [str(capture), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert f"ak47 {KEY}" in out.read_text(encoding="utf-8")

    result = runner.invoke(import_keys, [str(temp_dir / "missing.flows")])
    assert result.exit_code == 2
    assert "error:" in result.output

    empty = temp_dir / "empty.txt"
    empty.write_text("nothing useful")
    result = runner.invoke(import_keys, [str(empty)])
    assert result.exit_code == 1
    assert "No model/key pairs found" in result.output


def test_identity_import_with_mocked_flows(
    runner: CliRunner,
    cli_config: WOGConfig,
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = temp_dir / "capture.txt"
    capture.write_text(f"model=ak47 result=0&sync={KEY}")
    identity = CapturedIdentity(
        auth_id="42",
        session="sess",
        device_id="dev1",
        source="https://eu1.ultimate-disassembly.com/v/query2026.php",
    )
    monkeypatch.setattr(
        "wog_plugin_mitm.cli.extract_identity_from_flows", lambda path: identity
    )

    result = runner.invoke(import_keys, [str(capture), "--identity", "--merge"])

    assert result.exit_code == 0, result.output
    assert "WOG_AUTH_ID=42" in result.output
    assert "WOG_AUTH_SESSION=sess" in result.output
    assert "WOG_DEVICE_ID=dev1" in result.output
    db = WOGDatabase(cli_config)
    assert db.get_identity() == ("42", "sess")
    assert db.get_setting("device_id") == "dev1"
