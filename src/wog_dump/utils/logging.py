"""Logging for WOG Dump: stdlib logging with Rich console output.

Verbosity is controlled per destination:

- **Console**: quiet by default (WARNING); ``-v`` shows INFO, ``-d`` shows
  DEBUG. Progress bars and status lines are separate from this.
- **Log file**: one rotating ``wog.log`` that always captures INFO and
  above; set ``WOG_LOG_LEVEL=DEBUG`` in ``.env`` to include debug output.
  The file lives in the per-user data directory next to the database
  (``WOG_LOG_DIR`` overrides) - not in the working directory.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .. import __version__
from ..core.config import default_db_path

LOG_FILE_NAME = "wog.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

_FILE_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def default_log_dir() -> Path:
    """Directory for log files (honors ``WOG_LOG_DIR``).

    Defaults to a ``logs`` folder next to the per-user database, so runs
    from different checkouts share one history instead of littering the
    working directory.
    """
    override = os.environ.get("WOG_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return default_db_path().parent / "logs"


def log_file_level() -> int:
    """File verbosity from ``WOG_LOG_LEVEL`` (default INFO)."""
    raw = os.environ.get("WOG_LOG_LEVEL", "").strip().upper()
    return _FILE_LEVELS.get(raw, logging.INFO)


class PerformanceMonitor:
    """Monitor and track performance metrics."""

    def __init__(self) -> None:
        self.metrics: dict[str, list[float]] = {}
        self.start_times: dict[str, float] = {}

    def start_timer(self, operation: str) -> None:
        """Start timing an operation."""
        self.start_times[operation] = time.perf_counter()

    def stop_timer(self, operation: str) -> float:
        """Stop timing an operation and record the duration."""
        if operation not in self.start_times:
            return 0.0

        duration = time.perf_counter() - self.start_times[operation]

        if operation not in self.metrics:
            self.metrics[operation] = []

        self.metrics[operation].append(duration)
        del self.start_times[operation]
        return duration

    def get_stats(self, operation: str) -> dict[str, float]:
        """Get statistics for an operation."""
        if operation not in self.metrics:
            return {}

        times = self.metrics[operation]
        return {
            "count": len(times),
            "total": sum(times),
            "average": sum(times) / len(times),
            "min": min(times),
            "max": max(times),
        }

    @contextmanager
    def timer(self, operation: str) -> Iterator[None]:
        """Context manager for timing operations."""
        self.start_timer(operation)
        try:
            yield
        finally:
            self.stop_timer(operation)


class WOGLogger:
    """Logger for WOG Dump with Rich console formatting and file history."""

    def __init__(
        self, name: str = "wog_dump", console_level: int | str = logging.WARNING
    ) -> None:
        self.console = Console()
        self.logger = logging.getLogger(name)
        # The node passes everything through; each handler filters for
        # itself, so the log file keeps INFO+ even when the CLI silences
        # the console.
        self.logger.setLevel(logging.DEBUG)
        self.performance_monitor = PerformanceMonitor()
        self._console_handler: RichHandler | None = None
        self._file_handler: RotatingFileHandler | None = None
        self.log_file: Path | None = None
        self._setup_handlers(console_level)

    def _setup_handlers(self, console_level: int | str) -> None:
        """Set up console and rotating-file handlers."""
        self.logger.handlers.clear()

        if isinstance(console_level, str):
            console_level = getattr(logging, console_level.upper(), logging.WARNING)

        rich_handler = RichHandler(
            console=self.console,
            show_path=False,
            show_time=True,
            markup=True,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
        )
        rich_handler.setLevel(console_level)
        self._console_handler = rich_handler

        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        try:
            log_dir = default_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / LOG_FILE_NAME
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError:
            # Read-only filesystem or no permissions: console-only logging
            file_handler = None
            log_file = None

        if file_handler is not None:
            file_handler.setLevel(log_file_level())
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
            self._file_handler = file_handler
            self.log_file = log_file

        self.logger.addHandler(rich_handler)
        self.debug(f"Logger initialized - Log file: {self.log_file or 'console only'}")

    def info(self, message: str) -> None:
        """Log info message."""
        self.logger.info(message)

    def debug(self, message: str) -> None:
        """Log debug message."""
        self.logger.debug(message)

    def warning(self, message: str) -> None:
        """Log warning message."""
        self.logger.warning(message)

    def error(self, message: str) -> None:
        """Log error message."""
        self.logger.error(message)

    def critical(self, message: str) -> None:
        """Log critical message."""
        self.logger.critical(message)

    @property
    def console_level(self) -> int:
        """Current console verbosity."""
        if self._console_handler is not None:
            return self._console_handler.level
        return logging.WARNING

    def is_debug(self) -> bool:
        """Whether the console currently shows DEBUG output (``-d``)."""
        return self.console_level <= logging.DEBUG

    def print_banner(self) -> None:
        """Print enhanced application banner."""
        banner_text = Text.assemble(
            (f"🔫 WOG Dump v{__version__}", "bold cyan"),
            (" | ", "white"),
            ("World of Guns Model Extractor", "cyan"),
        )

        panel = Panel(
            banner_text,
            border_style="cyan",
            padding=(1, 2),
        )

        self.console.print(panel)
        self.debug(f"Banner: WOG Dump v{__version__}")

    def print_status(
        self, message: str, status: str = "info", prefix: str = "WOG DUMP"
    ) -> None:
        """Print status message with enhanced styling."""
        status_styles = {
            "info": ("ℹ️", "blue"),
            "success": ("✅", "green"),
            "warning": ("⚠️", "yellow"),
            "error": ("❌", "red"),
            "processing": ("🔄", "cyan"),
        }

        emoji, color = status_styles.get(status, ("ℹ️", "blue"))

        text = Text.assemble(
            (f"[{prefix}]", f"bold {color}"),
            (" ", "white"),
            (emoji, "white"),
            (" ", "white"),
            (message, color),
        )

        self.console.print(text)
        # Status lines are the operational history of a run - keep them
        # in the log file even when the console is quiet
        self.logger.info(f"[{status}] {message}")

    def create_task_progress(self) -> Progress:
        """Create enhanced progress bar for general tasks."""
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="right"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            "•",
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            TimeRemainingColumn(),
            "•",
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
        )

    def print_table(
        self, title: str, headers: list[str], rows: list[list[str]], style: str = "cyan"
    ) -> None:
        """Print a formatted table with enhanced styling."""
        table = Table(
            title=title,
            show_header=True,
            header_style=f"bold {style}",
            border_style=style,
            show_lines=True,
        )

        for header in headers:
            table.add_column(header, style="white")

        for row in rows:
            # Handle different row lengths gracefully
            padded_row = row + [""] * (len(headers) - len(row))
            table.add_row(*padded_row[: len(headers)])

        self.console.print(table)

    def print_performance_summary(self, operations: list[str] | None = None) -> None:
        """Print performance metrics summary."""
        if operations is None:
            operations = list(self.performance_monitor.metrics.keys())

        if not operations:
            self.console.print("[yellow]No performance metrics available[/yellow]")
            return

        table = Table(
            title="Performance Summary",
            show_header=True,
            header_style="bold magenta",
            border_style="magenta",
        )

        table.add_column("Operation", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Total (s)", justify="right")
        table.add_column("Average (s)", justify="right")
        table.add_column("Min (s)", justify="right")
        table.add_column("Max (s)", justify="right")

        for operation in operations:
            stats = self.performance_monitor.get_stats(operation)
            if stats:
                table.add_row(
                    operation,
                    str(stats["count"]),
                    f"{stats['total']:.2f}",
                    f"{stats['average']:.2f}",
                    f"{stats['min']:.2f}",
                    f"{stats['max']:.2f}",
                )

        self.console.print(table)

    def time_operation(self, operation_name: str) -> AbstractContextManager[None]:
        """Context manager to time operations."""
        return self.performance_monitor.timer(operation_name)

    @contextmanager
    def operation_context(
        self, operation: str, description: str | None = None
    ) -> Iterator[None]:
        """Context manager for tracking operations with progress."""
        if description is None:
            description = operation

        self.print_status(f"Starting {description}...", "processing")

        with self.time_operation(operation):
            start_time = time.perf_counter()
            try:
                yield
                duration = time.perf_counter() - start_time
                self.print_status(
                    f"Completed {description} in {duration:.2f}s", "success"
                )
            except Exception as e:
                duration = time.perf_counter() - start_time
                self.print_status(
                    f"Failed {description} after {duration:.2f}s: {str(e)}", "error"
                )
                raise

    def log_system_info(self) -> None:
        """Log system information for debugging."""
        import platform

        info = [
            f"Platform: {platform.platform()}",
            f"Python: {platform.python_version()}",
        ]

        for line in info:
            self.debug(line)

    def set_level(self, level: int | str) -> None:
        """Set console verbosity (-v INFO, -d DEBUG).

        The log file level is independent and comes from WOG_LOG_LEVEL.
        """
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.WARNING)

        if self._console_handler is not None:
            self._console_handler.setLevel(level)


# Global logger management
class LoggerManager:
    """Singleton logger manager."""

    _instance: WOGLogger | None = None

    @classmethod
    def get_logger(cls, name: str = "wog_dump") -> WOGLogger:
        """Get the global logger instance."""
        if cls._instance is None:
            cls._instance = WOGLogger(name)
        return cls._instance

    @classmethod
    def reset_logger(cls) -> None:
        """Reset the logger instance."""
        cls._instance = None


# Convenience functions for backward compatibility
def get_logger(name: str = "wog_dump") -> WOGLogger:
    """Get the global logger instance."""
    return LoggerManager.get_logger(name)


def set_log_level(level: int | str) -> None:
    """Set the console log level for the global logger."""
    logger = get_logger()
    logger.set_level(level)
