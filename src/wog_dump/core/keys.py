"""Decryption key management for WOG Dump.

Fetches per-asset ``sync`` keys from the game API (BZ2-over-HTTP, see
``core/api.py``), caches them in memory and persists them in the local
database. Exceptions for the whole decrypt domain live here so both the
key flow and the asset-decrypt flow (``core/decrypt.py``) share one
hierarchy without a circular import.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..utils.logging import get_logger
from .api import ApiNetworkError, WOGApiClient, parse_response_fields
from .auth import AuthError, AuthManager, Identity
from .config import WOGConfig, get_config
from .database import WOGDatabase


class DecryptionError(Exception):
    """Raised when decryption operations fail."""


class AuthenticationError(DecryptionError):
    """Raised when the API rejects authentication."""


class NetworkError(DecryptionError):
    """Raised when key API network operations fail."""


class KeyManager:
    """Decryption key manager backed by the local database."""

    # API response codes mapping (verified against the live query2026 API)
    RESPONSE_CODES = {
        "0": "Success",
        "100": "Authentication error",
        "1000": "Session rejected",
        "404": "Asset not found",
        "429": "Rate limited",
    }

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.db = WOGDatabase(self.config)
        self.client = WOGApiClient(self.config)
        self.auth = AuthManager(self.config, db=self.db, client=self.client)
        self._key_cache: dict[str, str] = {}

    def _build_api_request_data(self, asset_name: str, identity: Identity) -> str:
        """Build the sync-key query for an asset."""
        return (
            "query=3"
            f"&model={asset_name}"
            "&need_details=1"
            f"&session={identity.session}"
            f"&id={identity.auth_id}"
            f"&dev={identity.device_id}"
            f"&mode={self.config.game_mode}"
            f"&{self.client.build_common_params()}"
        )

    def _parse_api_response(self, response_text: str, asset_name: str) -> str | None:
        """Parse a sync-key response; returns the key or None."""
        fields = parse_response_fields(response_text)
        result = fields.get("result", "")

        if result == "0":
            key = fields.get("sync")
            if not key:
                self.logger.warning(
                    f"Success response but no sync key found for {asset_name}"
                )
                return None
            self.logger.debug(f"Successfully retrieved key for {asset_name}")
            return key

        error_msg = self.RESPONSE_CODES.get(
            result, fields.get("errmsg", f"Unknown error (code: {result})")
        )

        if result == "100":
            raise AuthenticationError(
                f"Authentication failed for {asset_name}: {error_msg}"
            )
        if result in ("429", "500", "502", "503", "504"):
            raise NetworkError(f"Server error for {asset_name}: {error_msg}")

        self.logger.warning(f"API error for {asset_name}: {error_msg}")
        return None

    def get_key_for_asset(self, asset_name: str, use_cache: bool = True) -> str | None:
        """Get decryption key for a specific asset with caching.

        Logs into the game account automatically when no session is
        stored, and re-logins once when the stored session is rejected.
        """
        if use_cache and asset_name in self._key_cache:
            self.logger.debug(f"Using cached key for {asset_name}")
            return self._key_cache[asset_name]

        try:
            identity = self.auth.ensure_session()
        except AuthError as e:
            raise AuthenticationError(str(e)) from e

        for attempt in (0, 1):
            params = self._build_api_request_data(asset_name, identity)
            try:
                with self.logger.time_operation(f"key_fetch_{asset_name}"):
                    response_text = self.client.call(params)
                key = self._parse_api_response(response_text, asset_name)
                if key and use_cache:
                    self._key_cache[asset_name] = key
                return key
            except ApiNetworkError as e:
                raise NetworkError(f"Network error for {asset_name}: {e}") from e
            except AuthenticationError:
                if attempt == 1:
                    raise
                # Session rejected: refresh it and retry once
                self.logger.debug(f"Session rejected for {asset_name}, re-logging in")
                try:
                    identity = self.auth.ensure_session(force_relogin=True)
                except AuthError as e:
                    raise AuthenticationError(str(e)) from e
            except Exception as e:
                raise DecryptionError(
                    f"Unexpected error getting key for {asset_name}: {e}"
                ) from e

        return None  # unreachable; kept for type checkers

    def fetch_key(self, weapon: str, max_retries: int = 3) -> str | None:
        """Fetch decryption key for a weapon with retry logic."""
        last_exception: Exception | None = None

        for attempt in range(max_retries):
            try:
                key = self.get_key_for_asset(weapon, use_cache=True)
                if key:
                    return key
                # No key without an exception means the asset has no key
                return None

            except AuthenticationError:
                # Missing/invalid account - retrying will not help
                self.logger.error(f"Authentication error for {weapon} - not retrying")
                return None

            except NetworkError as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = (2**attempt) * 0.5  # Exponential backoff
                    self.logger.debug(
                        f"Retrying {weapon} in {wait_time:.1f}s (attempt {attempt + 1})"
                    )
                    time.sleep(wait_time)

            except Exception as e:
                last_exception = e
                break

        if last_exception:
            self.logger.error(
                f"Failed to fetch key for {weapon} after {max_retries} attempts: {last_exception}"
            )

        return None

    def fetch_keys_parallel(
        self, weapons: list[str], max_workers: int | None = None
    ) -> dict[str, str]:
        """Fetch keys for multiple weapons in parallel with retry logic."""
        if not weapons:
            return {}

        # Fail fast with one clear message instead of one error per weapon
        try:
            self.auth.ensure_session()
        except AuthError as e:
            raise AuthenticationError(str(e)) from e

        keys: dict[str, str] = {}
        failed_weapons: list[str] = []
        max_workers = max_workers or min(self.config.max_threads, len(weapons))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_weapon = {
                executor.submit(self.fetch_key, weapon, 3): weapon for weapon in weapons
            }

            for future in as_completed(future_to_weapon):
                weapon = future_to_weapon[future]
                try:
                    key = future.result()
                    if key:
                        keys[weapon] = key
                    else:
                        failed_weapons.append(weapon)
                        self.logger.warning(f"No key available for {weapon}")
                except Exception as e:
                    self.logger.error(f"Failed to fetch key for {weapon}: {e}")
                    failed_weapons.append(weapon)

        self.logger.info(f"Fetched {len(keys)} keys out of {len(weapons)} weapons")

        if failed_weapons:
            self.logger.warning(
                f"Failed to fetch keys for {len(failed_weapons)} weapons"
            )
            self.logger.debug(f"Failed weapons: {', '.join(failed_weapons[:10])}")

        return keys

    def save_keys(self, keys: dict[str, str]) -> None:
        """Save keys to the local database."""
        if not keys:
            raise ValueError("No keys to save")

        try:
            self.db.save_keys(keys)
            self._key_cache.update(keys)
        except Exception as e:
            raise DecryptionError(f"Failed to save keys: {e}") from e

    def load_keys(self, try_migration: bool = True) -> dict[str, str]:
        """Load keys from the database, importing legacy txt files once."""
        try:
            keys = self.db.get_keys()

            if keys:
                self._key_cache.update(keys)
                return keys

            if try_migration:
                self.logger.info("No keys in the database, trying legacy import")
                if self.db.import_legacy_txt(
                    weapons_file=self.config.weapons_file,
                    keys_file=self.config.keys_file,
                ):
                    keys = self.db.get_keys()
                    if keys:
                        self._key_cache.update(keys)
                        return keys

            self.logger.warning("No decryption keys found")
            return {}

        except Exception as e:
            self.logger.error(f"Failed to load keys: {e}")
            return {}

    def clear_cache(self) -> None:
        """Clear the in-memory key cache."""
        self._key_cache.clear()
        self.logger.debug("Key cache cleared")
