"""Shared CLI error handling for WOG Dump."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

import click

from ..core.auth import AuthError
from ..core.decrypt import DecryptionError
from ..core.download import DownloadError
from ..core.unpack import UnpackError
from ..utils.logging import get_logger
from ..utils.normal_map import NormalMapError


class CLIError(Exception):
    """Base exception for CLI operations."""

    pass


class OperationCancelled(CLIError):
    """Raised when operation is cancelled by user."""

    pass


def print_banner_if_standalone(ctx: click.Context) -> None:
    """Print the banner unless this command was invoked by full-pipeline."""
    logger = (ctx.obj or {}).get("logger") or get_logger()
    if not (ctx.obj and ctx.obj.get("pipeline_nested")):
        logger.print_banner()


@contextmanager
def error_handler(operation_name: str) -> Iterator[None]:
    """Context manager for consistent error handling."""
    logger = get_logger()
    try:
        yield
    except KeyboardInterrupt:
        logger.print_status(f"{operation_name} cancelled by user", "warning")
        raise OperationCancelled() from None
    except (
        AuthError,
        DecryptionError,
        DownloadError,
        UnpackError,
        NormalMapError,
    ) as e:
        logger.print_status(f"{operation_name} failed: {str(e)}", "error")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error in {operation_name}: {e}")
        # ERROR passes the INFO-level file handler: the traceback always
        # lands in the log file, and on the console only with -d
        logger.logger.exception("Full traceback:")
        sys.exit(1)
