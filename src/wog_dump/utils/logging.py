"""Structured logging system for WOG Dump using Rich for beautiful console output."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.table import Table


class WOGLogger:
    """Enhanced logger for WOG Dump with Rich formatting."""
    
    def __init__(self, name: str = "wog_dump", level: int = logging.INFO) -> None:
        self.console = Console()
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        
        # Prevent duplicate handlers
        if not self.logger.handlers:
            self._setup_handlers()
    
    def _setup_handlers(self) -> None:
        """Set up logging handlers with Rich formatting."""
        # Rich console handler
        rich_handler = RichHandler(
            console=self.console,
            show_path=False,
            show_time=True,
            markup=True,
        )
        rich_handler.setLevel(logging.INFO)

        timestamp_str = datetime.now().strftime("%Y%m%d")  
        
        # File handler for debug logs
        _file = f"{timestamp_str}.log"
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)  # Create logs directory if it doesn't exist
        log_file = log_dir / _file
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        
        # Formatters
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        
        # Add handlers
        self.logger.addHandler(rich_handler)
        self.logger.addHandler(file_handler)
    
    def info(self, message: str, **kwargs: object) -> None:
        """Log info message."""
        self.logger.info(message, **kwargs)
    
    def debug(self, message: str, **kwargs: object) -> None:
        """Log debug message."""
        self.logger.debug(message, **kwargs)
    
    def warning(self, message: str, **kwargs: object) -> None:
        """Log warning message."""
        self.logger.warning(message, **kwargs)
    
    def error(self, message: str, **kwargs: object) -> None:
        """Log error message."""
        self.logger.error(message, **kwargs)
    
    def critical(self, message: str, **kwargs: object) -> None:
        """Log critical message."""
        self.logger.critical(message, **kwargs)
    
    def print_banner(self) -> None:
        """Print application banner."""
        table = Table.grid(padding=1)
        table.add_column(style="cyan", justify="center")
        table.add_row("🔫 WOG Dump v2.3 / World of Guns Model Dumper")
        
        self.console.print(table)
        self.console.print()
    
    def print_status(self, message: str, status: str = "info") -> None:
        """Print status message with appropriate styling."""
        style_map = {
            "info": "blue",
            "success": "green", 
            "warning": "yellow",
            "error": "red",
        }
        style = style_map.get(status, "blue")
        self.console.print(f"[{style}][WOG DUMP][/{style}] {message}")
    
    def create_download_progress(self) -> Progress:
        """Create a progress bar for downloads."""
        return Progress(
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
        )
    
    def create_task_progress(self) -> Progress:
        """Create a progress bar for general tasks."""
        return Progress(
            TextColumn("[bold blue]{task.description}", justify="right"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            "•",
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            TimeElapsedColumn(),
            console=self.console,
        )
    
    def print_table(self, title: str, headers: list[str], rows: list[list[str]]) -> None:
        """Print a formatted table."""
        table = Table(title=title, show_header=True, header_style="bold magenta")
        
        for header in headers:
            table.add_column(header)
        
        for row in rows:
            table.add_row(*row)
        
        self.console.print(table)
    
    def print_error_summary(self, errors: list[str]) -> None:
        """Print error summary."""
        if not errors:
            return
            
        self.console.print("\n[red]Errors encountered:[/red]")
        for i, error in enumerate(errors, 1):
            self.console.print(f"  {i}. {error}")


# Global logger instance
_logger: WOGLogger | None = None


def get_logger(name: str = "wog_dump") -> WOGLogger:
    """Get the global logger instance."""
    global _logger
    if _logger is None:
        _logger = WOGLogger(name)
    return _logger


def set_log_level(level: int) -> None:
    """Set the log level for the global logger."""
    logger = get_logger()
    logger.logger.setLevel(level)
    for handler in logger.logger.handlers:
        if isinstance(handler, RichHandler):
            handler.setLevel(level)