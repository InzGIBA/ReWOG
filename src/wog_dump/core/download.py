"""Asset downloading for WOG Dump."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from ..utils.logging import get_logger
from .config import WOGConfig, get_config
from .http import create_session

# Assets served from a subdirectory of the data server
SUBDIRECTORY_ASSETS = {"spider_gen": "spider"}

# Signatures of loadable Unity bundles
UNITY_SIGNATURES = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"CAB-")

# Files without a known signature are accepted once they are at least this large
MIN_VALID_ASSET_SIZE = 20


class DownloadError(Exception):
    """Raised when a download cannot be completed."""


class ValidationError(DownloadError):
    """Raised when a downloaded file fails validation."""


class DownloadManager:
    """Download manager for WOG assets with batching support."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.session = create_session(self.config)
        self._download_stats = {
            "total_bytes": 0,
            "files_downloaded": 0,
            "files_failed": 0,
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

    def validate_asset(self, asset_path: Path) -> bool:
        """Validate asset file integrity."""
        try:
            if not asset_path.exists() or asset_path.stat().st_size == 0:
                return False

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
        partial asset behind.

        Raises DownloadError on failure.
        """
        asset_path = self.asset_path(asset_name)
        temp_path = asset_path.with_suffix(".tmp")

        try:
            response = self.session.get(
                self.asset_url(asset_name),
                stream=True,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()

            downloaded = 0
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            if validate and not self.validate_asset(temp_path):
                raise ValidationError(
                    f"Downloaded asset failed validation: {asset_name}"
                )

            asset_path.unlink(missing_ok=True)
            temp_path.rename(asset_path)

            self._download_stats["total_bytes"] += downloaded
            self.logger.debug(f"Downloaded {asset_name} ({downloaded:,} bytes)")
            return asset_path

        except DownloadError:
            raise
        except requests.RequestException as e:
            raise DownloadError(f"Network error downloading {asset_name}: {e}") from e
        except OSError as e:
            raise DownloadError(f"Failed to download {asset_name}: {e}") from e
        finally:
            temp_path.unlink(missing_ok=True)

    def download_single_asset(self, asset_name: str, validate: bool = True) -> bool:
        """Download a single asset, returning True on success."""
        try:
            with self.logger.time_operation(f"download_{asset_name}"):
                self._fetch_asset(asset_name, validate=validate)
            self._download_stats["files_downloaded"] += 1
            return True
        except DownloadError as e:
            self.logger.error(str(e))
            self._download_stats["files_failed"] += 1
            return False

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

        with self.logger.time_operation(f"download_{asset_name}"):
            asset_path = self._fetch_asset(asset_name, validate=True)
        self.logger.info(f"Downloaded weapon list asset: {asset_path}")
        return asset_path

    def check_for_updates(self, weapon_list: list[str]) -> list[str]:
        """Check which weapons need updates using parallel processing."""
        if not weapon_list:
            return []

        to_download = []

        with ThreadPoolExecutor(
            max_workers=min(self.config.max_threads, len(weapon_list))
        ) as executor:
            future_to_weapon = {
                executor.submit(self.check_asset_needs_update, weapon): weapon
                for weapon in weapon_list
            }

            for future in as_completed(future_to_weapon):
                weapon = future_to_weapon[future]
                try:
                    if future.result():
                        to_download.append(weapon)
                except Exception as e:
                    self.logger.debug(f"Failed to check update for {weapon}: {e}")
                    # If we can't check, assume it needs update
                    to_download.append(weapon)

        self.logger.info(
            f"Update check: {len(to_download)} of {len(weapon_list)} assets need updates"
        )
        return to_download

    def download_assets(
        self, weapon_list: list[str], check_updates: bool = True
    ) -> tuple[list[str], list[str]]:
        """Download multiple assets and return successful and failed lists."""
        if not weapon_list:
            return [], []

        if check_updates:
            to_download = self.check_for_updates(weapon_list)
            if not to_download:
                self.logger.info("No assets need updates")
                return [], []
        else:
            to_download = weapon_list

        successful = []
        failed = []

        for weapon in to_download:
            try:
                if self.download_single_asset(weapon):
                    successful.append(weapon)
                else:
                    failed.append(weapon)
            except Exception as e:
                self.logger.error(f"Failed to download {weapon}: {e}")
                failed.append(weapon)

        self.logger.info(
            f"Download completed: {len(successful)} successful, {len(failed)} failed"
        )
        return successful, failed

    def download_assets_batched(
        self,
        weapon_list: list[str],
        batch_size: int = 50,
        continue_on_error: bool = True,
    ) -> tuple[list[str], list[str]]:
        """Download assets in batches with error handling."""
        if not weapon_list:
            return [], []

        all_successful = []
        all_failed = []

        for i in range(0, len(weapon_list), batch_size):
            batch = weapon_list[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(weapon_list) + batch_size - 1) // batch_size

            self.logger.info(
                f"Processing batch {batch_num}/{total_batches} ({len(batch)} assets)"
            )

            try:
                successful, failed = self.download_assets(batch, check_updates=True)
                all_successful.extend(successful)
                all_failed.extend(failed)

                if failed and not continue_on_error:
                    self.logger.error(f"Batch {batch_num} had failures, stopping")
                    break

            except Exception as e:
                self.logger.error(f"Batch {batch_num} failed: {e}")
                all_failed.extend(batch)

                if not continue_on_error:
                    break

        self.logger.info(
            f"Batch download completed: {len(all_successful)} successful, {len(all_failed)} failed"
        )
        return all_successful, all_failed

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
        return self._download_stats.copy()

    def __enter__(self) -> DownloadManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.session.close()
