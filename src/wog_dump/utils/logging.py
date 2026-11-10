"""Logging for WOG Dump: stdlib logging with Rich console output.

Verbosity is controlled per destination:

- **Console**: quiet by default (WARNING); ``-v`` shows INFO, ``-d`` shows
  DEBUG. Console records are one compact line (``⚠ message``) - no
  timestamps or logger names. Pipeline-level feedback uses one aligned
  ``✓ Step — result (duration)`` line per step (``WOGLogger.step``);
  progress bars are transient and disappear when finished, so a run
  leaves behind only step lines, warnings and the final summary.
- **Log file**: one rotating ``wog.log`` that always captures INFO and
  above; set ``WOG_LOG_LEVEL=DEBUG`` in ``.env`` to include debug output.
  The file lives in the per-user data directory next to the database
  (``WOG_LOG_DIR`` overrides) - not in the working directory.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from rich.console import Console
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

# Per-item metrics are recorded as "<verb>:<item>" (e.g. "download:ak47")
# so the timing table can collapse them into one aggregated row per
# verb; top-level operation names never contain a colon.
_ITEM_METRIC_SEPARATOR = ":"


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


def format_duration(seconds: float) -> str:
    """Human-friendly duration: ``4.2s`` / ``57s`` / ``5m04s`` / ``1h07m``."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class ConsoleHandler(logging.Handler):
    """Compact one-line console records: ``⚠ message``.

    Timestamps, logger names and file locations stay in the log file;
    the console only shows a level icon, the message and (for errors)
    the traceback.
    """

    _ICONS = {
        logging.DEBUG: ("dim", "·"),
        logging.INFO: ("cyan", "·"),
        logging.WARNING: ("yellow", "⚠"),
        logging.ERROR: ("red", "✗"),
        logging.CRITICAL: ("bold red", "✗"),
    }

    def __init__(self, console: Console) -> None:
        super().__init__()
        self.console = console
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            style, icon = self._ICONS.get(record.levelno, ("white", "·"))
            message = self.format(record)
            # soft_wrap: only explicit newlines break lines, so paths and
            # command hints stay greppable (the terminal wraps visually)
            self.console.print(
                f"{icon} {message}" if message else icon,
                style=style,
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
        except Exception:  # pragma: no cover - logging must never crash
            self.handleError(record)


class StepResult:
    """Mutable handle passed by ``WOGLogger.step`` to attach a result."""

    def __init__(self) -> None:
        self.detail = ""

    def result(self, detail: str) -> None:
        """Set the short result shown next to the step title."""
        self.detail = detail


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


def _restrict_owner_only(path: str | Path) -> None:
    """Best-effort owner-only permissions on a log artifact."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows FAT or unusual filesystems: nothing to do
        pass


class _OwnerOnlyRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that keeps every rotation owner-only.

    RotatingFileHandler recreates the log file with umask-default
    permissions on every rollover, so a one-time chmod at setup does
    not cover future files; the 0o700 log directory below covers the
    rotated backups.
    """

    def doRollover(self) -> None:
        super().doRollover()
        _restrict_owner_only(self.baseFilename)


class NullProgress:
    """Drop-in progress stand-in that renders nothing.

    Returned by ``create_task_progress`` when progress output is
    disabled (``--no-progress``) or the output is not a terminal, so
    piped runs and CI get clean logs.
    """

    def __enter__(self) -> NullProgress:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def add_task(self, *args: Any, **kwargs: Any) -> int:
        return 0

    def update(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log(self, *args: Any, **kwargs: Any) -> None:
        return None


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
        self.progress_enabled = True
        self._console_handler: ConsoleHandler | None = None
        self._file_handler: RotatingFileHandler | None = None
        self.log_file: Path | None = None
        self._setup_handlers(console_level)

    def _setup_handlers(self, console_level: int | str) -> None:
        """Set up console and rotating-file handlers."""
        self.logger.handlers.clear()

        if isinstance(console_level, str):
            console_level = getattr(logging, console_level.upper(), logging.WARNING)

        console_handler = ConsoleHandler(self.console)
        console_handler.setLevel(console_level)
        self._console_handler = console_handler

        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        try:
            log_dir = default_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / LOG_FILE_NAME
            # The log records the account email and auth id: keep the
            # directory and every file in it owner-only (the directory
            # mode also covers the rotated backups)
            try:
                os.chmod(log_dir, 0o700)
                for existing in log_dir.glob(f"{LOG_FILE_NAME}*"):
                    _restrict_owner_only(existing)
            except OSError:
                pass
            file_handler = _OwnerOnlyRotatingFileHandler(
                log_file,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            _restrict_owner_only(log_file)
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

        self.logger.addHandler(console_handler)
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

    def log_file_only(self, message: str, level: int = logging.ERROR) -> None:
        """Log to the file handler only (e.g. tracebacks the console
        should not drown in)."""
        if self._file_handler is None:
            return
        record = self.logger.makeRecord(
            self.logger.name,
            level,
            "wog_dump",
            0,
            message,
            (),
            sys.exc_info(),
        )
        self._file_handler.handle(record)

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
        """Print the application banner."""
        self.console.print(
            f"🔫 [bold cyan]WOG Dump[/bold cyan] [cyan]v{__version__}[/cyan]"
            " [dim]· World of Guns Model Extractor[/dim]"
        )
        self.debug(f"Banner: WOG Dump v{__version__}")

    def print_status(self, message: str, status: str = "info") -> None:
        """Print a single status line (icon + message)."""
        status_styles = {
            "info": ("ℹ️", "blue"),
            "success": ("✅", "green"),
            "warning": ("⚠️", "yellow"),
            "error": ("❌", "red"),
            "processing": ("🔄", "cyan"),
        }

        emoji, color = status_styles.get(status, ("ℹ️", "blue"))

        text = Text.assemble((f"{emoji} ", "white"), (message, color))

        self.console.print(text, soft_wrap=True)
        # Status lines are the operational history of a run - keep them
        # in the log file even when the console is quiet
        self.logger.info(f"[{status}] {message}")

    @contextmanager
    def step(self, title: str) -> Iterator[StepResult]:
        """One aligned console line per pipeline step.

        While the step runs, its own progress bars are visible (they are
        transient and vanish on completion); when it finishes, a single
        ``✓ Title — detail (duration)`` line remains.
        """
        started = time.perf_counter()
        result = StepResult()
        self.logger.info(f"[step] {title} started")
        try:
            yield result
        except Exception:
            self.console.print(Text(f"✗ {title}"), style="red")
            self.logger.info(
                f"[step] {title} failed after "
                f"{format_duration(time.perf_counter() - started)}"
            )
            raise

        elapsed = time.perf_counter() - started
        self.console.print(
            Text.assemble(
                ("✓ ", "green"),
                (title.ljust(18), "bold"),
                (f"— {result.detail}" if result.detail else "", "green"),
                (f" ({format_duration(elapsed)})", "dim"),
            )
        )
        self.logger.info(f"[step] {title}: {result.detail} in {elapsed:.2f}s")

    def create_task_progress(self) -> Any:
        """Create a progress bar for a task (Rich Progress or NullProgress).

        Bars are transient: once finished they disappear, leaving the
        step lines as the run's summary. Disabled by ``--no-progress``
        (or ``WOG_NO_PROGRESS``) and when the output is not a terminal,
        so piped runs and CI get plain logs. Both flavors share the
        add_task/update/start/stop interface.
        """
        if (
            not self.progress_enabled
            or os.environ.get("WOG_NO_PROGRESS")
            or not self.console.is_terminal
        ):
            return NullProgress()
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
            transient=True,
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

    def print_performance_summary(self, max_rows: int = 12) -> None:
        """Print a compact timing table.

        Per-item metrics (``key_fetch_ak47``, ``download_m4``, ...) are
        aggregated into one row per verb with an item count, so a full
        288-weapon pipeline stays within a single screen.
        """
        grouped: dict[str, list[float]] = {}
        for name, times in self.performance_monitor.metrics.items():
            key = (
                name.split(_ITEM_METRIC_SEPARATOR, 1)[0]
                if _ITEM_METRIC_SEPARATOR in name
                else name
            )
            grouped.setdefault(key, []).extend(times)

        if not grouped:
            return

        rows = sorted(
            ((name, times) for name, times in grouped.items()),
            key=lambda item: sum(item[1]),
            reverse=True,
        )[:max_rows]

        table = Table(
            title="Timings",
            show_header=True,
            header_style="bold magenta",
            border_style="magenta",
        )
        table.add_column("Operation", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Average", justify="right")
        table.add_column("Max", justify="right")

        for name, times in rows:
            label = f"{name} ({len(times)} items)" if len(times) > 1 else name
            table.add_row(
                label,
                str(len(times)),
                format_duration(sum(times)),
                format_duration(sum(times) / len(times)),
                format_duration(max(times)),
            )

        self.console.print(table)

    def time_operation(self, operation_name: str) -> AbstractContextManager[None]:
        """Context manager to time operations."""
        return self.performance_monitor.timer(operation_name)

    @contextmanager
    def operation_context(
        self, operation: str, description: str | None = None
    ) -> Iterator[None]:
        """Time an operation; the history goes to the file only.

        Console feedback is left to progress bars and the command's own
        result line - nested "Starting/Completed" pairs used to bury the
        actual output.
        """
        if description is None:
            description = operation

        self.logger.info(f"[op] Starting {description}")
        with self.time_operation(operation):
            try:
                yield
            except Exception as e:
                self.logger.debug(f"[op] Failed {description}: {e}")
                raise
        self.logger.info(f"[op] Completed {description}")

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
