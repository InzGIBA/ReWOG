"""Unit tests for hash/validator-based update detection and the update CLI."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests
from click.testing import CliRunner

from wog_dump.cli.update import update
from wog_dump.core.config import WOGConfig
from wog_dump.core.database import AssetState, WOGDatabase
from wog_dump.core.decrypt import DecryptionError
from wog_dump.core.update import (
    ApplyResult,
    AssetCheck,
    UpdateChecker,
    UpdateReport,
)
from wog_dump.utils.logging import get_logger

BODY = b"UnityFS mock bundle"


@pytest.fixture
def update_config(test_config: WOGConfig) -> WOGConfig:
    test_config.request_delay = 0.0
    return test_config


def _write_asset(config: WOGConfig, name: str, body: bytes = BODY) -> Path:
    path = config.assets_dir / f"{name}.unity3d"
    path.write_bytes(body)
    return path


def _seed_state(
    config: WOGConfig,
    name: str,
    body: bytes = BODY,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    WOGDatabase(config).upsert_asset_state(
        AssetState(
            asset=name,
            weapon=name,
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
            etag=etag,
            last_modified=last_modified,
        )
    )


def _head_response(headers: dict[str, str]) -> Mock:
    response = Mock()
    response.headers = headers
    response.raise_for_status.return_value = None
    return response


def _unchanged_headers(body: bytes = BODY) -> dict[str, str]:
    return {"Content-Length": str(len(body))}


class TestUpdateChecks:
    @patch("requests.Session.get")
    @patch("requests.Session.head")
    def test_first_run_backfills_without_downloading(
        self, mock_head: Mock, mock_get: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        mock_head.return_value = _head_response(_unchanged_headers())

        checker = UpdateChecker(update_config)
        report = checker.check_assets()

        assert [c.status for c in report.checks] == ["unchanged"]
        state = checker.db.get_asset_state("ak74n")
        assert state is not None
        assert state.sha256 == hashlib.sha256(BODY).hexdigest()
        mock_get.assert_not_called()

    @patch("requests.Session.head")
    def test_etag_change_detected(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n", etag='"v1"')
        mock_head.return_value = _head_response(
            {"Content-Length": str(len(BODY)), "ETag": '"v2"'}
        )

        report = UpdateChecker(update_config).check_assets()

        assert report.changed == ["ak74n"]
        assert report.checks[0].reason == "ETag differs"

    @patch("requests.Session.head")
    def test_last_modified_change_detected(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(
            update_config, "ak74n", last_modified="Wed, 01 Jan 2025 00:00:00 GMT"
        )
        mock_head.return_value = _head_response(
            {
                "Content-Length": str(len(BODY)),
                "Last-Modified": "Thu, 02 Jan 2025 00:00:00 GMT",
            }
        )

        report = UpdateChecker(update_config).check_assets()

        assert report.changed == ["ak74n"]
        assert report.checks[0].reason == "Last-Modified differs"

    @patch("requests.Session.head")
    def test_size_change_detected(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n", etag='"v1"')
        mock_head.return_value = _head_response(
            {"Content-Length": str(len(BODY) + 10), "ETag": '"v1"'}
        )

        report = UpdateChecker(update_config).check_assets()

        assert report.changed == ["ak74n"]
        assert report.checks[0].reason == "size differs"

    @patch("requests.Session.head")
    def test_matching_validators_unchanged(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n", etag='"v1"')
        mock_head.return_value = _head_response(
            {"Content-Length": str(len(BODY)), "ETag": '"v1"'}
        )

        checker = UpdateChecker(update_config)
        report = checker.check_assets()

        assert [c.status for c in report.checks] == ["unchanged"]
        state = checker.db.get_asset_state("ak74n")
        assert state is not None
        assert state.checked_at

    @patch("requests.Session.head")
    def test_no_validators_fallback_detects_corruption(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        """Same size, different bytes: only the stored hash can catch it."""
        corrupted = b"UnityFS MOCK BUNDLE"  # same length as BODY
        assert len(corrupted) == len(BODY)
        _write_asset(update_config, "ak74n", corrupted)
        _seed_state(update_config, "ak74n")
        mock_head.return_value = _head_response(_unchanged_headers())

        report = UpdateChecker(update_config).check_assets()

        assert report.changed == ["ak74n"]
        assert report.checks[0].reason == "local hash differs"

    @patch("requests.Session.head")
    def test_no_validators_fallback_unchanged(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n")
        mock_head.return_value = _head_response(_unchanged_headers())

        report = UpdateChecker(update_config).check_assets()

        assert [c.status for c in report.checks] == ["unchanged"]
        assert report.checks[0].reason == "hash and size match"

    @patch("requests.Session.head")
    def test_no_validators_size_change(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n")
        mock_head.return_value = _head_response({"Content-Length": "999"})

        report = UpdateChecker(update_config).check_assets()

        assert report.changed == ["ak74n"]
        assert report.checks[0].reason == "size differs"

    @patch("requests.Session.head")
    def test_missing_local_file(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _seed_state(update_config, "ak74n")

        report = UpdateChecker(update_config).check_assets()

        assert [c.status for c in report.checks] == ["missing-local"]
        mock_head.assert_not_called()

    @patch("requests.Session.head")
    def test_new_on_server_from_weapon_list(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        WOGDatabase(update_config).save_weapons(["newgun"])

        report = UpdateChecker(update_config).check_assets()

        assert [c.status for c in report.checks] == ["new-on-server"]
        mock_head.assert_not_called()

    @patch("requests.Session.head")
    def test_head_failure_is_error(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n")
        mock_head.side_effect = requests.RequestException("boom")

        report = UpdateChecker(update_config).check_assets()

        assert [c.status for c in report.checks] == ["error"]
        assert report.errors == ["ak74n"]

    @patch("requests.Session.head")
    def test_weapons_filter_limits_scope(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _write_asset(update_config, "m4a1")
        mock_head.return_value = _head_response(_unchanged_headers())

        report = UpdateChecker(update_config).check_assets(weapons=["ak74n"])

        assert [c.asset for c in report.checks] == ["ak74n"]
        assert mock_head.call_count == 1
        assert "ak74n" in mock_head.call_args[0][0]

    @patch("requests.Session.head")
    def test_weapon_list_bundle_excluded(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "spider_gen")
        _write_asset(update_config, "ak74n")
        mock_head.return_value = _head_response(_unchanged_headers())

        report = UpdateChecker(update_config).check_assets()

        assert [c.asset for c in report.checks] == ["ak74n"]

    @patch("requests.Session.head")
    def test_report_summary_counts(
        self, mock_head: Mock, update_config: WOGConfig
    ) -> None:
        _write_asset(update_config, "ak74n")
        _seed_state(update_config, "ak74n", etag='"v1"')
        _seed_state(update_config, "gone")
        WOGDatabase(update_config).save_weapons(["newgun"])
        mock_head.return_value = _head_response(
            {"Content-Length": str(len(BODY)), "ETag": '"v2"'}
        )

        report = UpdateChecker(update_config).check_assets()

        assert report.summary() == {
            "changed": 1,
            "unchanged": 0,
            "missing-local": 1,
            "new-on-server": 1,
            "error": 0,
        }


class TestApplyUpdates:
    def _changed_report(self) -> UpdateReport:
        return UpdateReport(checks=[AssetCheck("ak74n", "changed", "ETag differs")])

    def test_apply_refreshes_only_affected_weapons(
        self, update_config: WOGConfig
    ) -> None:
        checker = UpdateChecker(update_config)
        (update_config.decrypted_dir / "ak74n.unity3d").write_bytes(b"decrypted")

        with (
            patch.object(
                checker.manager, "download_assets", return_value=(["ak74n"], [], 0)
            ) as mock_download,
            patch("wog_dump.core.update.KeyManager") as mock_keys,
            patch("wog_dump.core.update.AssetDecryptor") as mock_decryptor,
            patch("wog_dump.core.update.AssetUnpacker") as mock_unpacker,
        ):
            mock_keys.return_value.load_keys.return_value = {
                "ak74n": "k1",
                "m4a1": "k2",
            }
            mock_decryptor.return_value.decrypt_all_assets.return_value = (
                [update_config.decrypted_dir / "ak74n.unity3d"],
                [],
            )
            mock_unpacker.return_value.unpack_multiple_assets.return_value = {
                update_config.decrypted_dir / "ak74n.unity3d": [Path("out.png")]
            }

            result = checker.apply_updates(self._changed_report())

        mock_download.assert_called_once_with(["ak74n"], check_updates=False)
        mock_decryptor.return_value.decrypt_all_assets.assert_called_once_with(
            {"ak74n": "k1", "m4a1": "k2"}, weapons=["ak74n"]
        )
        unpack_paths = mock_unpacker.return_value.unpack_multiple_assets.call_args[0][0]
        assert unpack_paths == [update_config.decrypted_dir / "ak74n.unity3d"]
        assert result.downloaded == ["ak74n"]
        assert result.unpacked_files == 1

    def test_apply_without_changes_is_noop(self, update_config: WOGConfig) -> None:
        checker = UpdateChecker(update_config)
        with patch.object(checker.manager, "download_assets") as mock_download:
            result = checker.apply_updates(UpdateReport())

        mock_download.assert_not_called()
        assert result.downloaded == []

    def test_apply_without_keys_raises(self, update_config: WOGConfig) -> None:
        checker = UpdateChecker(update_config)
        with (
            patch.object(
                checker.manager, "download_assets", return_value=(["ak74n"], [], 0)
            ),
            patch("wog_dump.core.update.KeyManager") as mock_keys,
        ):
            mock_keys.return_value.load_keys.return_value = {}
            with pytest.raises(DecryptionError):
                checker.apply_updates(self._changed_report())

    def test_apply_reports_download_failures(self, update_config: WOGConfig) -> None:
        checker = UpdateChecker(update_config)
        with patch.object(
            checker.manager, "download_assets", return_value=([], ["ak74n"], 0)
        ):
            result = checker.apply_updates(self._changed_report())

        assert result.failed == ["ak74n"]
        assert result.decrypted_files == []


class TestUpdateCommand:
    def _invoke(self, config: WOGConfig, args: list[str], checker: Mock) -> Any:
        with patch("wog_dump.cli.update.UpdateChecker", return_value=checker):
            runner = CliRunner()
            return runner.invoke(
                update,
                args,
                obj={"logger": get_logger(), "config": config},
            )

    def _checker(
        self, report: UpdateReport, apply_result: ApplyResult | None = None
    ) -> Mock:
        checker = Mock()
        checker.check_assets.return_value = report
        checker.apply_updates.return_value = apply_result or ApplyResult(
            [], [], 0, [], [], 0
        )
        return checker

    def test_check_only_default_reports_changes(self, update_config: WOGConfig) -> None:
        report = UpdateReport(
            checks=[AssetCheck("ak74n", "changed", "ETag differs", 10, 20)]
        )
        checker = self._checker(report)

        result = self._invoke(update_config, [], checker)

        assert result.exit_code == 0
        assert "ak74n" in result.output
        assert "changed" in result.output
        checker.check_assets.assert_called_once_with(weapons=None)
        checker.apply_updates.assert_not_called()

    def test_check_only_flag_is_noop(self, update_config: WOGConfig) -> None:
        checker = self._checker(UpdateReport())

        result = self._invoke(update_config, ["--check-only"], checker)

        assert result.exit_code == 0
        checker.apply_updates.assert_not_called()

    def test_apply_calls_apply_updates(self, update_config: WOGConfig) -> None:
        report = UpdateReport(checks=[AssetCheck("ak74n", "changed", "ETag differs")])
        checker = self._checker(
            report, ApplyResult(["ak74n"], [], 0, [Path("f")], [], 3)
        )

        result = self._invoke(update_config, ["--apply"], checker)

        assert result.exit_code == 0
        checker.apply_updates.assert_called_once_with(report)
        assert "Updated 1" in result.output

    def test_apply_failure_exits_nonzero(self, update_config: WOGConfig) -> None:
        report = UpdateReport(checks=[AssetCheck("ak74n", "changed", "ETag differs")])
        checker = self._checker(report, ApplyResult([], ["ak74n"], 0, [], [], 0))

        result = self._invoke(update_config, ["--apply"], checker)

        assert result.exit_code == 1

    def test_check_errors_exit_nonzero(self, update_config: WOGConfig) -> None:
        report = UpdateReport(checks=[AssetCheck("ak74n", "error", "HEAD failed")])
        checker = self._checker(report)

        result = self._invoke(update_config, [], checker)

        assert result.exit_code == 1

    def test_weapons_filter_passed_through(self, update_config: WOGConfig) -> None:
        checker = self._checker(UpdateReport())

        result = self._invoke(update_config, ["--weapons", "ak74n,m4a1"], checker)

        assert result.exit_code == 0
        checker.check_assets.assert_called_once_with(weapons=["ak74n", "m4a1"])

    def test_invalid_weapons_rejected(self, update_config: WOGConfig) -> None:
        checker = self._checker(UpdateReport())

        result = self._invoke(update_config, ["--weapons", "../evil"], checker)

        assert result.exit_code != 0
        checker.check_assets.assert_not_called()

    def test_apply_and_check_only_conflict(self, update_config: WOGConfig) -> None:
        checker = self._checker(UpdateReport())

        result = self._invoke(update_config, ["--apply", "--check-only"], checker)

        assert result.exit_code != 0

    def test_empty_scope_warns(self, update_config: WOGConfig) -> None:
        checker = self._checker(UpdateReport())

        result = self._invoke(update_config, [], checker)

        assert result.exit_code == 0
        assert "No downloaded assets" in result.output
