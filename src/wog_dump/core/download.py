"""Asset downloading for WOG Dump.

Downloads are resumable (HTTP Range), rate-limited and run in parallel;
every file lands in place atomically via temp-file + rename.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from rich.progress import Progress, TaskID

from ..utils.logging import get_logger
from .config import WOGConfig, get_config
from .http import RateLimiter, create_session

# Assets served from a subdirectory of the data server
SUBDIRECTORY_ASSETS = {"spider_gen": "spider"}

# Signatures of loadable Unity bundles
UNITY_SIGNATURES = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"CAB-")

# Files without a known signature are accepted once they are at least this large
MIN_VALID_ASSET_SIZE = 20


class DownloadError(Exception):
    """Raised when a download cannot be completed."""


class AssetNotFoundError(DownloadError):
    """Raised when the asset does not exist on the server (HTTP 404).

    Permanent per definition: skipping the asset is correct, no retry or
    batch abort will ever fix it.
    """


class ValidationError(DownloadError):
    """Raised when a downloaded file fails validation."""


class DownloadManager:
    """Download manager for WOG assets with batching support."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.session = create_session(self.config)
        self._rate_limiter = RateLimiter(self.config.request_delay)
        self._stats_lock = threading.Lock()
        self._download_stats = {
            "total_bytes": 0,
            "files_downloaded": 0,
            "files_failed": 0,
            "files_skipped": 0,
        }

    def asset_url(self, asset_name: str) -> str:
        """Build the server URL for an asset."""
        subdirectory = SUBDIRECTORY_ASSETS.get(asset_name)
        base = self.config.data_base_url
        if subdirectory:
            return f"{base}/{subdirectory}/{asset_name}.unity3d"
        return f"{base}/{asset_name}.unity3d"

    def asset_path(self, asset_name: str) -> Path:
        """Local path where an asset is stored."""
        return self.config.assets_dir / f"{asset_name}.unity3d"

    def validate_asset(
        self, asset_path: Path, expected_size: int | None = None
    ) -> bool:
        """Validate asset file integrity.

        When the server-declared size is known, an exact match is required -
        a stronger check than any content heuristic. Otherwise fall back to
        Unity signature detection with a minimum-size floor.
        """
        try:
            if not asset_path.exists() or asset_path.stat().st_size == 0:
                return False

            if expected_size is not None:
                if asset_path.stat().st_size != expected_size:
                    self.logger.warning(
                        f"Asset size mismatch for {asset_path}: expected "
                        f"{expected_size}, got {asset_path.stat().st_size}"
                    )
                    return False
                return True

            with open(asset_path, "rb") as f:
                header = f.read(100)

            if any(sig in header for sig in UNITY_SIGNATURES):
                return True

            if asset_path.stat().st_size >= MIN_VALID_ASSET_SIZE:
                self.logger.debug(
                    f"Asset has unknown format, accepting by size: {asset_path}"
                )
                return True

            self.logger.warning(f"Asset file is too small to be valid: {asset_path}")
            return False

        except OSError as e:
            self.logger.error(f"Asset validation failed for {asset_path}: {e}")
            return False

    def get_asset_size(self, asset_name: str) -> int:
        """Get the size of an asset from the server (0 when unavailable)."""
        try:
            self._rate_limiter.wait()
            response = self.session.head(
                self.asset_url(asset_name), timeout=self.config.request_timeout
            )
            response.raise_for_status()
            return int(response.headers.get("Content-Length", 0))
        except requests.RequestException as e:
            self.logger.debug(f"Failed to get size for {asset_name}: {e}")
            return 0

    def check_asset_needs_update(self, asset_name: str) -> bool:
        """Check whether an asset needs updating based on size comparison."""
        asset_path = self.asset_path(asset_name)

        if not asset_path.exists():
            return True

        server_size = self.get_asset_size(asset_name)
        if server_size == 0:
            # Can't get server size, assume no update needed
            return False

        local_size = asset_path.stat().st_size
        if local_size != server_size:
            self.logger.debug(
                f"{asset_name}: size mismatch (local: {local_size}, server: {server_size})"
            )
            return True

        return False

    def _fetch_asset(self, asset_name: str, validate: bool = True) -> Path:
        """Download an asset to its final location.

        Downloads to a temporary file first, optionally validates it and only
        then moves it into place, so interrupted downloads never leave a
        partial asset behind. When a partial temp file exists and the server
        supports ranges, the download resumes from where it stopped.

        Raises DownloadError on failure.
        """
        asset_path = self.asset_path(asset_name)
        temp_path = asset_path.with_suffix(".tmp")

        self._rate_limiter.wait()

        try:
            resumed_bytes = temp_path.stat().st_size if temp_path.exists() else 0

            headers = {"Range": f"bytes={resumed_bytes}-"} if resumed_bytes else None
            response = self.session.get(
                self.asset_url(asset_name),
                stream=True,
                timeout=self.config.request_timeout,
                headers=headers,
            )
            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                if response.status_code == 404:
                    raise AssetNotFoundError(
                        f"Asset not found on server: {asset_name}"
                    ) from e
                raise

            if resumed_bytes and response.status_code == 206:
                # Server honored the range: append the remaining bytes
                mode = "ab"
                self.logger.debug(f"Resuming {asset_name} at byte {resumed_bytes:,}")
                content_length = int(response.headers.get("Content-Length", 0))
                expected_size = (
                    resumed_bytes + content_length if content_length else None
                )
            else:
                # Fresh download, or the server ignored the range request
                mode = "wb"
                resumed_bytes = 0
                content_length = int(response.headers.get("Content-Length", 0))
                expected_size = content_length or None

            downloaded = resumed_bytes
            with open(temp_path, mode) as f:
                for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            # Transfer integrity: the final file must match the declared size
            if expected_size is not None:
                actual_size = temp_path.stat().st_size
                if actual_size != expected_size:
                    if resumed_bytes:
                        # The server file likely changed since the partial
                        # download; restart once from scratch
                        self.logger.warning(
                            f"Resumed download of {asset_name} does not match the "
                            f"server file, restarting"
                        )
                        temp_path.unlink(missing_ok=True)
                        return self._fetch_asset(asset_name, validate=validate)
                    # Truncated transfer: keep the partial file so the next
                    # attempt can resume instead of starting over
                    raise ValidationError(
                        f"Downloaded size {actual_size} does not match "
                        f"Content-Length {expected_size} for {asset_name}"
                    )

            if validate and not self.validate_asset(temp_path, expected_size):
                # A failed validation means corrupt bytes: no point resuming
                temp_path.unlink(missing_ok=True)
                raise ValidationError(
                    f"Downloaded asset failed validation: {asset_name}"
                )

            asset_path.unlink(missing_ok=True)
            temp_path.rename(asset_path)

            with self._stats_lock:
                self._download_stats["total_bytes"] += downloaded
            self.logger.debug(f"Downloaded {asset_name} ({downloaded:,} bytes)")
            return asset_path

        except DownloadError:
            raise
        except requests.RequestException as e:
            # Keep the partial temp file so the next attempt can resume
            raise DownloadError(f"Network error downloading {asset_name}: {e}") from e
        except OSError as e:
            raise DownloadError(f"Failed to download {asset_name}: {e}") from e

    def _download_with_status(self, asset_name: str, validate: bool = True) -> str:
        """Download one asset; returns "ok", "not_found" or "failed"."""
        try:
            with self.logger.time_operation(f"download:{asset_name}"):
                self._fetch_asset(asset_name, validate=validate)
            with self._stats_lock:
                self._download_stats["files_downloaded"] += 1
            return "ok"
        except AssetNotFoundError as e:
            self.logger.warning(f"Skipping {e}")
            with self._stats_lock:
                self._download_stats["files_skipped"] += 1
            return "not_found"
        except DownloadError as e:
            self.logger.error(str(e))
            with self._stats_lock:
                self._download_stats["files_failed"] += 1
            return "failed"

    def download_single_asset(self, asset_name: str, validate: bool = True) -> bool:
        """Download a single asset, returning True on success."""
        return self._download_with_status(asset_name, validate=validate) == "ok"

    def download_weapon_list(self, force_update: bool = False) -> Path:
        """Download the weapon list asset (spider_gen.unity3d)."""
        asset_name = "spider_gen"
        asset_path = self.asset_path(asset_name)

        if (
            not force_update
            and asset_path.exists()
            and not self.check_asset_needs_update(asset_name)
        ):
            self.logger.debug(f"Weapon list asset is up to date: {asset_path}")
            return asset_path

        with self.logger.time_operation(f"download:{asset_name}"):
            asset_path = self._fetch_asset(asset_name, validate=True)
        self.logger.info(f"Downloaded weapon list asset: {asset_path}")
        return asset_path

    def check_for_updates(
        self, weapon_list: list[str], quiet: bool = False
    ) -> list[str]:
        """Check which weapons need updates using parallel processing.

        ``quiet`` skips the progress bar (used when the caller already
        shows one aggregate bar for the whole batch run).
        """
        if not weapon_list:
            return []

        with ThreadPoolExecutor(
            max_workers=min(self.config.max_threads, len(weapon_list))
        ) as executor:
            future_to_weapon = {
                executor.submit(self.check_asset_needs_update, weapon): weapon
                for weapon in weapon_list
            }

            if quiet:
                to_download = self._collect_update_checks(future_to_weapon)
            else:
                with self.logger.create_task_progress() as progress:
                    task = progress.add_task("Checking updates", total=len(weapon_list))
                    to_download = self._collect_update_checks(
                        future_to_weapon, progress, task
                    )

        self.logger.info(
            f"Update check: {len(to_download)} of {len(weapon_list)} assets need updates"
        )
        return to_download

    def _collect_update_checks(
        self,
        future_to_weapon: dict[Any, str],
        progress: Progress | None = None,
        task: TaskID | None = None,
    ) -> list[str]:
        """Gather update-check results, advancing the bar when present."""
        to_download = []

        for future in as_completed(future_to_weapon):
            weapon = future_to_weapon[future]
            try:
                if future.result():
                    to_download.append(weapon)
            except Exception as e:
                self.logger.debug(f"Failed to check update for {weapon}: {e}")
                # If we can't check, assume it needs update
                to_download.append(weapon)
            if progress is not None and task is not None:
                progress.update(task, advance=1)

        return to_download

    def download_assets(
        self,
        weapon_list: list[str],
        check_updates: bool = True,
        progress: Progress | None = None,
        task: TaskID | None = None,
    ) -> tuple[list[str], list[str], int]:
        """Download multiple assets in parallel.

        Returns ``(successful, failed, missing)``; missing means the
        asset is not on the server (HTTP 404, skipped with a warning)
        and does not count as a failure. ``progress``/``task`` let a
        batch caller drive one aggregate bar across several calls.
        """
        if not weapon_list:
            return [], [], 0

        if check_updates:
            to_download = self.check_for_updates(
                weapon_list, quiet=progress is not None
            )
            if not to_download:
                self.logger.info("No assets need updates")
                if progress is not None and task is not None:
                    progress.update(task, advance=len(weapon_list))
                return [], [], 0
        else:
            to_download = weapon_list

        successful: list[str] = []
        failed: list[str] = []
        skipped = 0
        max_workers = min(self.config.max_threads, len(to_download))

        bar: Progress
        bar_task: TaskID
        owns_bar: bool
        if progress is None or task is None:
            bar = self.logger.create_task_progress()
            bar_task = bar.add_task("Downloading assets", total=len(to_download))
            bar.start()
            owns_bar = True
        else:
            bar = progress
            bar_task = task
            owns_bar = False
            # Up-to-date assets complete their share of the bar at once
            if check_updates:
                bar.update(bar_task, advance=len(weapon_list) - len(to_download))

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_weapon = {
                    executor.submit(self._download_with_status, weapon): weapon
                    for weapon in to_download
                }

                for future in as_completed(future_to_weapon):
                    weapon = future_to_weapon[future]
                    try:
                        status = future.result()
                        if status == "ok":
                            successful.append(weapon)
                        elif status == "not_found":
                            skipped += 1
                        else:
                            failed.append(weapon)
                    except Exception as e:
                        self.logger.error(f"Failed to download {weapon}: {e}")
                        failed.append(weapon)

                    if owns_bar and (failed or skipped):
                        bar.update(
                            bar_task,
                            description=(
                                f"Downloading assets ({len(failed)} failed, "
                                f"{skipped} missing)"
                            ),
                        )
                    bar.update(bar_task, advance=1)
        finally:
            if owns_bar:
                bar.stop()

        summary = f"Download completed: {len(successful)} successful"
        if skipped:
            summary += f", {skipped} missing on server (skipped)"
        if failed:
            summary += f", {len(failed)} failed"
        self.logger.info(summary)
        return successful, failed, skipped

    def download_assets_batched(
        self,
        weapon_list: list[str],
        batch_size: int = 50,
        continue_on_error: bool = True,
    ) -> tuple[list[str], list[str], int]:
        """Download assets in batches under one aggregate progress bar.

        Returns ``(successful, failed, missing)``.
        """
        if not weapon_list:
            return [], [], 0

        all_successful: list[str] = []
        all_failed: list[str] = []
        total_missing = 0

        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Downloading assets", total=len(weapon_list))

            for i in range(0, len(weapon_list), batch_size):
                batch = weapon_list[i : i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(weapon_list) + batch_size - 1) // batch_size

                self.logger.info(
                    f"Processing batch {batch_num}/{total_batches} "
                    f"({len(batch)} assets)"
                )

                try:
                    successful, failed, missing = self.download_assets(
                        batch, check_updates=True, progress=progress, task=task
                    )
                    all_successful.extend(successful)
                    all_failed.extend(failed)
                    total_missing += missing

                    if all_failed or total_missing:
                        progress.update(
                            task,
                            description=(
                                f"Downloading assets ({len(all_failed)} failed, "
                                f"{total_missing} missing)"
                            ),
                        )

                    if failed and not continue_on_error:
                        self.logger.error(f"Batch {batch_num} had failures, stopping")
                        break

                except Exception as e:
                    self.logger.error(f"Batch {batch_num} failed: {e}")
                    all_failed.extend(batch)

                    if not continue_on_error:
                        break

        summary = f"Batch download completed: {len(all_successful)} successful"
        if total_missing:
            summary += f", {total_missing} missing on server"
        if all_failed:
            summary += f", {len(all_failed)} failed"
        self.logger.info(summary)
        return all_successful, all_failed, total_missing

    def validate_cached_assets(
        self, weapon_list: list[str]
    ) -> tuple[list[str], list[str]]:
        """Validate cached assets by checking that files exist and are non-empty."""
        valid_assets = []
        invalid_assets = []

        for weapon in weapon_list:
            asset_path = self.asset_path(weapon)

            if asset_path.exists() and asset_path.stat().st_size > 0:
                valid_assets.append(weapon)
            else:
                invalid_assets.append(weapon)

        self.logger.info(
            f"Asset validation: {len(valid_assets)} valid, {len(invalid_assets)} invalid"
        )
        return valid_assets, invalid_assets

    def get_download_stats(self) -> dict[str, int]:
        """Get download statistics."""
        with self._stats_lock:
            return self._download_stats.copy()

    def __enter__(self) -> DownloadManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.session.close()
