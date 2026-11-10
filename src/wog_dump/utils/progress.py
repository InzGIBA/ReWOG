"""Progress helpers shared by the CLI layer.

``ByteTracker`` turns the per-chunk ``byte_progress`` callbacks of the
downloader into one aggregate bytes bar. Rich's Progress is
thread-safe, so worker threads update it directly.
"""

from __future__ import annotations

import os
from typing import Any

from .logging import WOGLogger

__all__ = ["ByteTracker", "make_byte_tracker"]


def progress_allowed(logger: WOGLogger) -> bool:
    """Whether progress output should render at all."""
    return (
        logger.progress_enabled
        and not os.environ.get("WOG_NO_PROGRESS")
        and logger.console.is_terminal
    )


class ByteTracker:
    """Aggregate byte accounting for parallel downloads.

    The downloader reports absolute ``downloaded`` counts per asset and
    the total when the server declared one; this tracker converts them
    into deltas on one aggregate Rich task and grows the bar's total as
    sizes become known.
    """

    def __init__(self, progress: Any, task_id: int) -> None:
        self._progress = progress
        self._task_id = task_id
        self._last: dict[str, int] = {}
        self._declared: dict[str, int] = {}
        self._expected_total = 0

    def __call__(self, asset_name: str, downloaded: int, total: int | None) -> None:
        if total is not None:
            self.declare_total(asset_name, total)
        delta = downloaded - self._last.get(asset_name, 0)
        self._last[asset_name] = downloaded
        if delta > 0:
            self._progress.update(self._task_id, advance=delta)

    def declare_total(self, asset_name: str, total: int) -> None:
        """Record one asset's declared size in the aggregate total."""
        previous = self._declared.get(asset_name)
        if previous == total:
            return
        self._declared[asset_name] = total
        if previous is None:
            self._expected_total += total
        else:
            self._expected_total += total - previous
        self._progress.update(self._task_id, total=self._expected_total)


def make_byte_tracker(logger: WOGLogger) -> Any | None:
    """Build a started bytes Progress plus its tracker, or None.

    Returns ``(progress, tracker)``; the caller stops the progress when
    the downloads finish.
    """
    if not progress_allowed(logger):
        return None
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]download", justify="right"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=logger.console,
        expand=True,
        transient=True,
    )
    task_id = progress.add_task("download", total=None)
    progress.start()
    return progress, ByteTracker(progress, task_id)
