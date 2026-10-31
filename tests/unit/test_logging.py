"""Unit tests for the logging utilities."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from wog_dump.utils.logging import (
    LoggerManager,
    PerformanceMonitor,
    WOGLogger,
    default_log_dir,
    get_logger,
    log_file_level,
    set_log_level,
)


class TestPerformanceMonitor:
    def test_timer_records_duration(self) -> None:
        monitor = PerformanceMonitor()

        with monitor.timer("op"):
            time.sleep(0.01)

        stats = monitor.get_stats("op")
        assert stats["count"] == 1
        assert stats["total"] >= 0.01

    def test_stats_unknown_operation(self) -> None:
        monitor = PerformanceMonitor()

        assert monitor.get_stats("never-run") == {}

    def test_stop_timer_without_start(self) -> None:
        monitor = PerformanceMonitor()

        assert monitor.stop_timer("never-started") == 0.0

    def test_multiple_runs_aggregate(self) -> None:
        monitor = PerformanceMonitor()

        for _ in range(3):
            with monitor.timer("op"):
                pass

        stats = monitor.get_stats("op")
        assert stats["count"] == 3
        assert stats["min"] <= stats["average"] <= stats["max"]


class TestVerbosityConfiguration:
    def test_log_file_level_default_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WOG_LOG_LEVEL", raising=False)

        assert log_file_level() == logging.INFO

    def test_log_file_level_debug_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WOG_LOG_LEVEL", "debug")

        assert log_file_level() == logging.DEBUG

    def test_log_file_level_invalid_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WOG_LOG_LEVEL", "not-a-level")

        assert log_file_level() == logging.INFO

    def test_default_log_dir_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WOG_LOG_DIR", "/tmp/custom-logs")

        assert default_log_dir() == Path("/tmp/custom-logs")

    def test_default_log_dir_next_to_database(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("WOG_LOG_DIR", raising=False)
        monkeypatch.setenv("WOG_DB_PATH", str(tmp_path / "x" / "wog_dump.db"))

        assert default_log_dir() == tmp_path / "x" / "logs"


class TestWOGLogger:
    def test_get_logger_singleton(self) -> None:
        assert get_logger() is get_logger()

    def test_reset_logger_creates_new_instance(self) -> None:
        first = get_logger()
        LoggerManager.reset_logger()
        second = get_logger()

        assert first is not second
        LoggerManager.reset_logger()

    def test_set_level_controls_console_only(self) -> None:
        logger = get_logger()

        set_log_level("DEBUG")
        assert logger.console_level == logging.DEBUG
        assert logger.is_debug() is True

        set_log_level("WARNING")
        assert logger.console_level == logging.WARNING
        assert logger.is_debug() is False

    def test_file_keeps_info_when_console_is_quiet(self) -> None:
        """The log file records INFO+ even with a WARNING-level console."""
        logger = WOGLogger(name="wog_test_file", console_level=logging.WARNING)

        assert logger.log_file is not None
        logger.info("file must capture this info line")
        logger.logger.handlers[0].flush()

        content = logger.log_file.read_text(encoding="utf-8")
        assert "file must capture this info line" in content

    def test_debug_goes_to_file_only_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("WOG_LOG_LEVEL", raising=False)
        logger = WOGLogger(name="wog_test_debug_off", console_level=logging.WARNING)
        logger.debug("debug line while disabled")
        logger.info("info line")
        logger.logger.handlers[0].flush()

        content = logger.log_file.read_text(encoding="utf-8")  # type: ignore[union-attr]
        assert "debug line while disabled" not in content
        assert "info line" in content

        monkeypatch.setenv("WOG_LOG_LEVEL", "DEBUG")
        debug_logger = WOGLogger(
            name="wog_test_debug_on", console_level=logging.WARNING
        )
        debug_logger.debug("debug line while enabled")
        debug_logger.logger.handlers[0].flush()

        content = debug_logger.log_file.read_text(  # type: ignore[union-attr]
            encoding="utf-8"
        )
        assert "debug line while enabled" in content

    def test_single_rotating_log_file(self, tmp_path: Path) -> None:
        """One stable file name instead of a new timestamped file per run."""
        env = pytest.MonkeyPatch()
        env.setenv("WOG_LOG_DIR", str(tmp_path))
        try:
            first = WOGLogger(name="wog_test_rotate", console_level=logging.WARNING)
            assert first.log_file == tmp_path / "wog.log"

            # A "second run" reuses the same file
            second = WOGLogger(name="wog_test_rotate", console_level=logging.WARNING)
            assert second.log_file == tmp_path / "wog.log"
            assert list(tmp_path.iterdir()) == [tmp_path / "wog.log"]
        finally:
            env.undo()

    def test_print_status_lands_in_log_file(self) -> None:
        """Console status lines are kept as the run's history in the file."""
        logger = WOGLogger(name="wog_test_status", console_level=logging.WARNING)

        logger.print_status("Completed asset download in 14.61s", "success")
        logger.logger.handlers[0].flush()

        content = logger.log_file.read_text(encoding="utf-8")  # type: ignore[union-attr]
        assert "[success] Completed asset download in 14.61s" in content

    def test_print_status_outputs_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logger = get_logger()

        logger.print_status("Everything ok", "success")
        output = capsys.readouterr().out

        assert "Everything ok" in output

    def test_print_table_outputs_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        logger = get_logger()

        logger.print_table(
            "Test Table", ["Name", "Value"], [["answer", "42"], ["short-row"]]
        )
        output = capsys.readouterr().out

        assert "Test Table" in output
        assert "answer" in output
        assert "42" in output

    def test_log_levels_route_to_stdlib(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logger = get_logger()

        set_log_level("INFO")
        logger.info("info message goes to console")
        capsys.readouterr()

        set_log_level("WARNING")
        logger.info("info message is now hidden")
        output = capsys.readouterr().out

        assert "hidden" not in output
        set_log_level("WARNING")

    def test_woglogger_direct_construction(self) -> None:
        logger = WOGLogger(name="wog_test_direct", console_level=logging.WARNING)

        assert logger.logger.name == "wog_test_direct"
        assert logger.console_level == logging.WARNING


class TestConsolePresentation:
    """Tests for the redesigned console output."""

    def test_format_duration(self) -> None:
        from wog_dump.utils.logging import format_duration

        assert format_duration(4.16) == "4.2s"
        assert format_duration(57.3) == "57s"
        assert format_duration(62.4) == "1m02s"
        assert format_duration(384.0) == "6m24s"
        assert format_duration(4320.0) == "1h12m"

    def test_step_prints_result_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        logger = get_logger()

        with logger.step("Weapon list") as step:
            step.result("288 weapons")
        output = capsys.readouterr().out

        assert "Weapon list" in output
        assert "288 weapons" in output

    def test_step_failure_prints_error_line(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logger = get_logger()

        with pytest.raises(RuntimeError), logger.step("Broken step"):
            raise RuntimeError("boom")
        output = capsys.readouterr().out

        assert "Broken step" in output

    def test_step_history_lands_in_log_file(self) -> None:
        logger = WOGLogger(name="wog_test_step_file", console_level=logging.WARNING)

        with logger.step("Weapon list") as step:
            step.result("288 weapons")
        logger.logger.handlers[0].flush()

        content = logger.log_file.read_text(encoding="utf-8")  # type: ignore[union-attr]
        assert "[step] Weapon list" in content

    def test_warning_is_one_compact_line(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Console warnings carry no timestamp or level-name padding."""
        logger = get_logger()

        logger.warning("something is off")
        output = capsys.readouterr().out

        assert "something is off" in output
        assert "WARNING" not in output
        assert "wog_dump" not in output

    def test_performance_summary_aggregates_item_metrics(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """download:ak47 / download:m4 collapse into one row per verb."""
        logger = get_logger()
        monitor = logger.performance_monitor
        monitor.metrics.clear()

        for name, durations in {
            "download:ak47": [1.0],
            "download:m4": [2.0],
            "key_fetch:ak47": [0.5],
            "asset_download": [60.0],
        }.items():
            monitor.metrics[name] = list(durations)

        logger.print_performance_summary()
        output = capsys.readouterr().out

        assert "download (2 items)" in output
        assert "key_fetch" in output
        assert "asset_download" in output
        assert "download:ak47" not in output

    def test_performance_summary_empty_prints_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logger = get_logger()
        logger.performance_monitor.metrics.clear()

        logger.print_performance_summary()
        assert capsys.readouterr().out == ""
