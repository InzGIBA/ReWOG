"""Hash/validator-based update detection for downloaded assets.

The size-only check in ``DownloadManager`` stays the fast path for
batch downloads; this module tracks per-asset state (sha256, size,
ETag, Last-Modified) in the local store and compares it against
throttled HEAD responses. When the server sends no validators the
local file is re-hashed against the stored digest, which also catches
local corruption.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import requests

from ..utils.logging import get_logger
from .config import WOGConfig, get_config
from .database import AssetState, WOGDatabase
from .decrypt import AssetDecryptor, DecryptionError
from .download import DownloadManager, compute_asset_sha256
from .http import RateLimiter
from .keys import KeyManager
from .unpack import AssetUnpacker

AssetStatus = Literal["changed", "unchanged", "missing-local", "new-on-server", "error"]

# The weapon-list bundle is refreshed by download-weapons, not by the
# per-weapon update flow
LIST_ASSET = "spider_gen"

_STATUSES: tuple[AssetStatus, ...] = (
    "changed",
    "unchanged",
    "missing-local",
    "new-on-server",
    "error",
)


@dataclass(frozen=True)
class AssetCheck:
    """Outcome of checking one asset against the server."""

    asset: str
    status: AssetStatus
    reason: str = ""
    local_size: int | None = None
    server_size: int | None = None


@dataclass
class UpdateReport:
    """All per-asset checks of one update run."""

    checks: list[AssetCheck] = field(default_factory=list)

    @property
    def changed(self) -> list[str]:
        return [check.asset for check in self.checks if check.status == "changed"]

    @property
    def errors(self) -> list[str]:
        return [check.asset for check in self.checks if check.status == "error"]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {status: 0 for status in _STATUSES}
        for check in self.checks:
            counts[check.status] += 1
        return counts


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of re-downloading and refreshing changed assets."""

    downloaded: list[str]
    failed: list[str]
    missing: int
    decrypted_files: list[Path]
    decrypt_failed: list[str]
    unpacked_files: int


class UpdateChecker:
    """Check downloaded assets for updates and optionally apply them."""

    def __init__(
        self,
        config: WOGConfig | None = None,
        manager: DownloadManager | None = None,
    ) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.manager = manager if manager is not None else DownloadManager(self.config)
        self._rate_limiter = RateLimiter(self.config.request_delay)

    @property
    def db(self) -> WOGDatabase:
        return self.manager.db

    def resolve_weapons(self, weapons: list[str] | None = None) -> list[str]:
        """Assets to check: explicit filter, else everything tracked.

        The default scope unions local files, stored state and the
        weapon list so never-downloaded list entries surface as
        new-on-server.
        """
        if weapons is not None:
            return [name for name in dict.fromkeys(weapons) if name != LIST_ASSET]
        local = {path.stem for path in self.config.assets_dir.glob("*.unity3d")}
        tracked = {state.asset for state in self.db.list_asset_states()}
        listed = set(self.db.get_weapons())
        return sorted((local | tracked | listed) - {LIST_ASSET})

    def check_assets(self, weapons: list[str] | None = None) -> UpdateReport:
        """Check every in-scope asset; backfills state from local files.

        The first run (empty asset_state) hashes existing files instead
        of re-downloading them, so later runs have a baseline.
        """
        names = self.resolve_weapons(weapons)
        local_names = [
            name for name in names if self.manager.asset_path(name).is_file()
        ]
        self.manager.backfill_asset_states(local_names)

        report = UpdateReport()
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Checking assets", total=len(names))
            for name in names:
                report.checks.append(self._check_one(name))
                progress.update(task, advance=1)
        return report

    def _check_one(self, name: str) -> AssetCheck:
        asset_path = self.manager.asset_path(name)
        state = self.db.get_asset_state(name)

        if not asset_path.is_file():
            if state is not None:
                return AssetCheck(
                    name, "missing-local", "downloaded before, local file is gone"
                )
            return AssetCheck(
                name, "new-on-server", "in weapon list, not downloaded yet"
            )

        if state is None:
            state = self.manager.record_asset_state(name)
            if state is None:
                return AssetCheck(name, "error", "could not hash local file")

        try:
            self._rate_limiter.wait()
            response = self.manager.session.head(
                self.manager.asset_url(name), timeout=self.config.request_timeout
            )
            response.raise_for_status()
        except requests.RequestException as e:
            self.logger.debug(f"HEAD failed for {name}: {e}")
            return AssetCheck(name, "error", f"HEAD failed: {e}")

        return self._compare(name, asset_path, state, response)

    def _compare(
        self,
        name: str,
        asset_path: Path,
        state: AssetState,
        response: requests.Response,
    ) -> AssetCheck:
        headers = response.headers
        server_etag = headers.get("ETag")
        server_last_modified = headers.get("Last-Modified")
        server_size = self._content_length(headers)
        local_size = asset_path.stat().st_size

        if server_etag or server_last_modified:
            if server_etag and state.etag and server_etag != state.etag:
                return self._changed(
                    name, state, local_size, server_size, "ETag differs"
                )
            if (
                server_last_modified
                and state.last_modified
                and server_last_modified != state.last_modified
            ):
                return self._changed(
                    name, state, local_size, server_size, "Last-Modified differs"
                )
            if server_size is not None and server_size != state.size:
                return self._changed(
                    name, state, local_size, server_size, "size differs"
                )
            self._refresh_state(state, server_etag, server_last_modified)
            return AssetCheck(
                name, "unchanged", "validators match", local_size, server_size
            )

        # No server validators: the stored hash is the only baseline,
        # and re-hashing also detects local corruption
        local_sha256 = compute_asset_sha256(asset_path, self.config.chunk_size)
        if local_sha256 != state.sha256:
            return self._changed(
                name, state, local_size, server_size, "local hash differs"
            )
        if server_size is not None and server_size != state.size:
            return self._changed(name, state, local_size, server_size, "size differs")
        self._refresh_state(state, None, None)
        return AssetCheck(
            name, "unchanged", "hash and size match", local_size, server_size
        )

    def _changed(
        self,
        name: str,
        state: AssetState,
        local_size: int,
        server_size: int | None,
        reason: str,
    ) -> AssetCheck:
        self.logger.debug(f"{name}: {reason} (stored size {state.size})")
        return AssetCheck(name, "changed", reason, local_size, server_size)

    def _refresh_state(
        self,
        state: AssetState,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        """Stamp checked_at and adopt fresh validators for the next run."""
        self.db.upsert_asset_state(
            AssetState(
                asset=state.asset,
                weapon=state.weapon,
                sha256=state.sha256,
                size=state.size,
                etag=etag or state.etag,
                last_modified=last_modified or state.last_modified,
            )
        )

    @staticmethod
    def _content_length(headers: Mapping[str, str]) -> int | None:
        try:
            size = int(headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return None
        return size or None

    def apply_updates(self, report: UpdateReport) -> ApplyResult:
        """Re-download changed assets, then decrypt/unpack only those.

        Raises DecryptionError when no keys are available; download and
        decrypt failures are reported per asset instead of aborting.
        """
        changed = report.changed
        if not changed:
            return ApplyResult([], [], 0, [], [], 0)

        successful, failed, missing = self.manager.download_assets(
            changed, check_updates=False
        )
        if not successful:
            return ApplyResult(successful, failed, missing, [], [], 0)

        keys = KeyManager(self.config).load_keys()
        if not keys:
            raise DecryptionError(
                "No decryption keys found; run 'decrypt-assets --update-keys' first"
            )

        decryptor = AssetDecryptor(self.config)
        decrypted_files, decrypt_failed = decryptor.decrypt_all_assets(
            keys, weapons=successful
        )

        unpacker = AssetUnpacker(self.config)
        decrypted_paths = [
            self.config.decrypted_dir / f"{name}.unity3d"
            for name in successful
            if name not in decrypt_failed
        ]
        decrypted_paths = [path for path in decrypted_paths if path.is_file()]
        unpacked_files = 0
        if decrypted_paths:
            results = unpacker.unpack_multiple_assets(
                decrypted_paths,
                output_dir=self.config.base_dir / "runtime" / "unpacked",
            )
            unpacked_files = sum(len(files) for files in results.values())

        return ApplyResult(
            successful, failed, missing, decrypted_files, decrypt_failed, unpacked_files
        )
