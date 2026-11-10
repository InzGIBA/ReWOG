"""Unit tests for progress plumbing (task 10)."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.download import DownloadManager
from wog_dump.utils.logging import WOGLogger, get_logger
from wog_dump.utils.progress import ByteTracker, make_byte_tracker, progress_allowed


@pytest.fixture(autouse=True)
def clean_progress_env(monkeypatch: Any) -> None:
    monkeypatch.delenv("WOG_NO_PROGRESS", raising=False)


def make_logger(terminal: bool) -> WOGLogger:
    logger = WOGLogger()
    logger.console = __import__("rich.console", fromlist=["Console"]).Console(
        file=io.StringIO(), force_terminal=terminal
    )
    return logger


class TestProgressAllowed:
    def test_enabled_on_terminal(self) -> None:
        logger = make_logger(terminal=True)
        logger.progress_enabled = True
        assert progress_allowed(logger) is True

    def test_disabled_by_flag(self) -> None:
        logger = make_logger(terminal=True)
        logger.progress_enabled = False
        assert progress_allowed(logger) is False

    def test_disabled_by_env(self, monkeypatch: Any) -> None:
        logger = make_logger(terminal=True)
        monkeypatch.setenv("WOG_NO_PROGRESS", "1")
        assert progress_allowed(logger) is False

    def test_disabled_without_terminal(self) -> None:
        logger = make_logger(terminal=False)
        assert progress_allowed(logger) is False


class TestTaskProgress:
    def test_null_when_not_terminal(self) -> None:
        from wog_dump.utils.logging import NullProgress

        logger = make_logger(terminal=False)
        assert isinstance(logger.create_task_progress(), NullProgress)

    def test_null_when_disabled(self) -> None:
        from wog_dump.utils.logging import NullProgress

        logger = make_logger(terminal=True)
        logger.progress_enabled = False
        assert isinstance(logger.create_task_progress(), NullProgress)

    def test_null_via_env(self, monkeypatch: Any) -> None:
        from wog_dump.utils.logging import NullProgress

        logger = make_logger(terminal=True)
        monkeypatch.setenv("WOG_NO_PROGRESS", "1")
        assert isinstance(logger.create_task_progress(), NullProgress)


class TestByteTracker:
    def test_aggregates_deltas_and_totals(self) -> None:
        updates: list[dict[str, Any]] = []

        class FakeProgress:
            def update(self, task_id: int, **kwargs: Any) -> None:
                updates.append(kwargs)

        tracker = ByteTracker(FakeProgress(), 0)
        # Two chunks for one asset, size declared on the first callback
        tracker("ak47", 100, 1000)
        tracker("ak47", 350, 1000)
        # Another asset starts
        tracker("ak74n", 50, 500)

        advances = [u["advance"] for u in updates if "advance" in u]
        assert advances == [100, 250, 50]
        totals = [u["total"] for u in updates if "total" in u]
        assert totals == [1000, 1500]

    def test_repeated_total_not_double_counted(self) -> None:
        updates: list[dict[str, Any]] = []

        class FakeProgress:
            def update(self, task_id: int, **kwargs: Any) -> None:
                updates.append(kwargs)

        tracker = ByteTracker(FakeProgress(), 0)
        tracker("ak47", 100, 1000)
        tracker("ak47", 200, 1000)
        assert [u for u in updates if "total" in u] == [{"total": 1000}]


class TestDownloadByteCallback:
    def test_callback_receives_bytes_and_total(self, test_config: WOGConfig) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "19"}
        mock_response.iter_content.return_value = [b"UnityFS", b" mock bundle"]
        mock_response.raise_for_status.return_value = None

        calls: list[tuple[str, int, int | None]] = []
        with patch("wog_dump.core.download.requests.Session.get") as mock_get:
            mock_get.return_value = mock_response
            with DownloadManager(test_config) as manager:
                success = manager.download_single_asset(
                    "test_asset",
                    byte_progress=lambda name, done, total: calls.append(
                        (name, done, total)
                    ),
                )

        assert success is True
        assert calls == [
            ("test_asset", 7, 19),
            ("test_asset", 19, 19),
        ]


class TestMakeByteTracker:
    def test_none_when_disabled(self) -> None:
        logger = make_logger(terminal=False)
        assert make_byte_tracker(logger) is None

    def test_started_progress_and_tracker(self) -> None:
        logger = make_logger(terminal=True)
        result = make_byte_tracker(logger)
        assert result is not None
        progress, tracker = result
        assert isinstance(tracker, ByteTracker)
        progress.stop()


class TestNoProgressFlag:
    def test_cli_flag_disables_progress(self, tmp_path: Path) -> None:
        from wog_dump.cli.main import cli

        get_logger().progress_enabled = True
        runner = __import__("click.testing", fromlist=["CliRunner"]).CliRunner()
        result = runner.invoke(
            cli, ["--no-progress", "--max-threads", "2", "catalog", "--json"]
        )
        assert get_logger().progress_enabled is False
        assert result.exit_code == 0
