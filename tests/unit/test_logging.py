"""Unit tests for the logging utilities."""

from __future__ import annotations

import logging
import time

import pytest

from wog_dump.utils.logging import (
    LoggerManager,
    PerformanceMonitor,
    WOGLogger,
    get_logger,
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


class TestWOGLogger:
    def test_get_logger_singleton(self) -> None:
        assert get_logger() is get_logger()

    def test_reset_logger_creates_new_instance(self) -> None:
        first = get_logger()
        LoggerManager.reset_logger()
        second = get_logger()

        assert first is not second
        LoggerManager.reset_logger()

    def test_set_level_by_name(self) -> None:
        logger = get_logger()

        set_log_level("DEBUG")
        assert logger.logger.level == logging.DEBUG

        set_log_level("WARNING")
        assert logger.logger.level == logging.WARNING

    def test_console_handler_stays_at_least_info(self) -> None:
        logger = get_logger()

        set_log_level("DEBUG")

        console_handlers = [
            h for h in logger.logger.handlers if type(h).__name__ == "RichHandler"
        ]
        assert console_handlers, "expected a RichHandler to be configured"
        for handler in console_handlers:
            assert handler.level >= logging.INFO

        set_log_level("WARNING")

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
        logger = WOGLogger(name="wog_test_direct", level=logging.WARNING)

        assert logger.logger.name == "wog_test_direct"
        assert logger.logger.level == logging.WARNING
