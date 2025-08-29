"""Enhanced logging system for WOG Dump with structured output and performance monitoring."""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


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
    def timer(self, operation: str):
        """Context manager for timing operations."""
        self.start_timer(operation)
        try:
            yield
        finally:
            self.stop_timer(operation)


class WOGLogger:
    """Enhanced logger for WOG Dump with Rich formatting and performance monitoring."""

    def __init__(self, name: str = "wog_dump", level: int = logging.INFO) -> None:
        self.console = Console()
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.performance_monitor = PerformanceMonitor()

        # Prevent duplicate handlers
        if not self.logger.handlers:
            self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Set up logging handlers with enhanced formatting."""
        # Clear any existing handlers
        self.logger.handlers.clear()

        # Rich console handler with custom formatting
        rich_handler = RichHandler(
            console=self.console,
            show_path=False,
            show_time=True,
            markup=True,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
        )
        rich_handler.setLevel(logging.INFO)

        # Create logs directory and file handler
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"wog_dump_{timestamp}.log"

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)

        # Enhanced formatter with more context
        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)

        # Add handlers
        self.logger.addHandler(rich_handler)
        self.logger.addHandler(file_handler)

        # Log initialization
        self.debug(f"Logger initialized - Log file: {log_file}")

    def info(self, message: str, **kwargs) -> None:
        """Log info message."""
        self.logger.info(message, **kwargs)

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self.logger.debug(message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self.logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self.logger.error(message, **kwargs)

    def critical(self, message: str, **kwargs) -> None:
        """Log critical message."""
        self.logger.critical(message, **kwargs)

    def print_banner(self) -> None:
        """Print enhanced application banner."""
        banner_text = Text.assemble(
            ("🔫 WOG Dump v2.3", "bold cyan"),
            (" | ", "white"),
            ("World of Guns Model Extractor", "cyan"),
        )

        panel = Panel(
            banner_text,
            border_style="cyan",
            padding=(1, 2),
        )

        self.console.print(panel)

    def print_status(self, message: str, status: str = "info", prefix: str = "WOG DUMP") -> None:
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

    def create_download_progress(self) -> Progress:
        """Create enhanced progress bar for downloads."""
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
        )

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
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
        )

    def print_table(self, title: str, headers: list[str], rows: list[list[str]],
                   style: str = "cyan") -> None:
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
            table.add_row(*padded_row[:len(headers)])

        self.console.print(table)

    def print_error_summary(self, errors: list[str], max_display: int = 10) -> None:
        """Print error summary with truncation for large lists."""
        if not errors:
            return

        panel_content = []
        display_errors = errors[:max_display]

        for i, error in enumerate(display_errors, 1):
            panel_content.append(f"{i:2d}. {error}")

        if len(errors) > max_display:
            panel_content.append(f"... and {len(errors) - max_display} more errors")

        error_panel = Panel(
            "\n".join(panel_content),
            title="[bold red]Errors Encountered",
            border_style="red",
            padding=(1, 2),
        )

        self.console.print(error_panel)

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

    def time_operation(self, operation_name: str):
        """Context manager to time operations."""
        return self.performance_monitor.timer(operation_name)

    @contextmanager
    def operation_context(self, operation: str, description: str | None = None):
        """Context manager for tracking operations with progress."""
        if description is None:
            description = operation

        self.print_status(f"Starting {description}...", "processing")

        with self.time_operation(operation):
            start_time = time.perf_counter()
            try:
                yield
                duration = time.perf_counter() - start_time
                self.print_status(f"Completed {description} in {duration:.2f}s", "success")
            except Exception as e:
                duration = time.perf_counter() - start_time
                self.print_status(f"Failed {description} after {duration:.2f}s: {str(e)}", "error")
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
        """Set logging level with validation."""
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)

        self.logger.setLevel(level)

        # Update handler levels
        for handler in self.logger.handlers:
            if isinstance(handler, RichHandler):
                # Console handler should show INFO and above
                handler.setLevel(max(level, logging.INFO))


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
    """Set the log level for the global logger."""
    logger = get_logger()
    logger.set_level(level)
