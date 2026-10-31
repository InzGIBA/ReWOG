"""Asset decryption for WOG Dump with XOR decryption and key management."""

from __future__ import annotations

import bz2
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
import UnityPy

from ..core.config import WOGConfig, get_config
from ..core.http import create_session
from ..core.storage import DataStorageManager, StorageError, write_legacy_keys_file
from ..utils.logging import get_logger

# Appended to every server-provided key before hashing
KEY_SALT = "World of Guns: Gun Disassembly"


class DecryptionError(Exception):
    """Raised when decryption operations fail."""


class AuthenticationError(DecryptionError):
    """Raised when the key API rejects authentication."""


class NetworkError(DecryptionError):
    """Raised when key API network operations fail."""


class KeyManager:
    """Decryption key manager with caching and retry support."""

    # API response codes mapping
    RESPONSE_CODES = {
        0: "Success",
        100: "Authentication error",
        1000: "Server error",
        404: "Asset not found",
        429: "Rate limited",
    }

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.storage = DataStorageManager(self.config)
        self.session = create_session(self.config)
        self._key_cache: dict[str, str] = {}

    def _build_api_request_data(self, asset_name: str) -> str:
        """Build API request data string."""
        return (
            f"query=3&"
            f"model={asset_name}&"
            f"need_details=1&"
            f"session={self.config.auth_session}&"
            f"id={self.config.auth_id}&"
            f"dev={self.config.device_id}&"
            f"mode={self.config.game_mode}&"
            f"ver={self.config.game_version}&"
            f"uver={self.config.unity_version}&"
            f"time={int(time.time())}"
        )

    def _compress_request_data(self, data: str) -> bytes:
        """Compress request data using BZ2 with a 4-byte length prefix."""
        compressed = bz2.compress(data.encode())
        return len(compressed).to_bytes(4, "little") + compressed

    def _decompress_response(self, response_data: bytes) -> str:
        """Decompress API response data (4-byte length prefix + BZ2)."""
        if len(response_data) < 4:
            raise NetworkError("Invalid response length")

        try:
            return bz2.decompress(response_data[4:]).decode("utf-8")
        except (OSError, EOFError, UnicodeDecodeError) as e:
            raise NetworkError(f"Failed to decompress response: {e}") from e

    def _parse_api_response(self, response_text: str, asset_name: str) -> str | None:
        """Parse API response and extract the sync key."""
        result_match = re.search(r"result=(\d+)", response_text)
        result_code = int(result_match.group(1)) if result_match else -1

        if result_code == 0:
            if "sync=" not in response_text:
                self.logger.warning(
                    f"Success response but no sync key found for {asset_name}"
                )
                return None
            key = response_text.split("sync=")[1].split("&")[0]
            self.logger.debug(f"Successfully retrieved key for {asset_name}")
            return key

        error_msg = self.RESPONSE_CODES.get(
            result_code, f"Unknown error (code: {result_code})"
        )

        if result_code == 100:
            raise AuthenticationError(
                f"Authentication failed for {asset_name}: {error_msg}"
            )
        if result_code in (429, 500, 502, 503, 504):
            raise NetworkError(f"Server error for {asset_name}: {error_msg}")

        self.logger.warning(f"API error for {asset_name}: {error_msg}")
        return None

    def get_key_for_asset(self, asset_name: str, use_cache: bool = True) -> str | None:
        """Get decryption key for a specific asset with caching."""
        if use_cache and asset_name in self._key_cache:
            self.logger.debug(f"Using cached key for {asset_name}")
            return self._key_cache[asset_name]

        try:
            request_data = self._build_api_request_data(asset_name)
            payload = self._compress_request_data(request_data)

            headers = self.config.get_api_headers()
            headers["Content-Length"] = str(len(payload))

            with self.logger.time_operation(f"key_fetch_{asset_name}"):
                response = self.session.put(
                    f"{self.config.api_base_url}?soc=steam",
                    data=payload,
                    headers=headers,
                    timeout=self.config.request_timeout,
                )
                response.raise_for_status()

            response_text = self._decompress_response(response.content)
            key = self._parse_api_response(response_text, asset_name)

            if key and use_cache:
                self._key_cache[asset_name] = key

            return key

        except (AuthenticationError, NetworkError):
            raise
        except requests.RequestException as e:
            raise NetworkError(f"Network error for {asset_name}: {e}") from e
        except Exception as e:
            raise DecryptionError(
                f"Unexpected error getting key for {asset_name}: {e}"
            ) from e

    def fetch_key(self, weapon: str, max_retries: int = 3) -> str | None:
        """Fetch decryption key for a weapon with retry logic."""
        last_exception: Exception | None = None

        for attempt in range(max_retries):
            try:
                key = self.get_key_for_asset(weapon, use_cache=True)
                if key:
                    return key
                # No key returned without an exception means the asset has no key
                return None

            except AuthenticationError:
                # Retrying will not help with authentication errors
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

    def save_keys(self, keys: dict[str, str], backup: bool = True) -> None:
        """Save keys to JSON storage with optional legacy txt backup."""
        if not keys:
            raise ValueError("No keys to save")

        try:
            self.storage.save_keys(keys)

            # Maintain the legacy txt format for backward compatibility
            if backup and self.config.keys_file:
                self._save_legacy_format(keys)

            self._key_cache.update(keys)
            self.logger.info(f"Saved {len(keys)} keys to JSON storage")

        except Exception as e:
            raise DecryptionError(f"Failed to save keys: {e}") from e

    def _save_legacy_format(self, keys: dict[str, str]) -> None:
        """Save keys in the legacy txt format for backward compatibility."""
        try:
            keys_file = self.config.keys_file

            # Keep the previous version as .bak
            if keys_file.exists():
                backup_path = keys_file.with_suffix(".bak")
                backup_path.write_bytes(keys_file.read_bytes())
                self.logger.debug(f"Created legacy backup: {backup_path}")

            write_legacy_keys_file(keys_file, keys)
            self.logger.debug(f"Saved legacy format to {keys_file}")

        except OSError as e:
            self.logger.warning(f"Failed to save legacy format: {e}")

    def load_keys(self, try_migration: bool = True) -> dict[str, str]:
        """Load keys from JSON storage, migrating from legacy files if needed."""
        try:
            keys = self.storage.get_keys()

            if keys:
                self.logger.info(f"Loaded {len(keys)} keys from JSON storage")
                self._key_cache.update(keys)
                return keys

            if try_migration:
                self.logger.info("No keys found in JSON storage, attempting migration")
                self.storage.migrate_from_txt_files(
                    weapons_file=self.config.weapons_file,
                    keys_file=self.config.keys_file,
                )
                keys = self.storage.get_keys()
                if keys:
                    self.logger.info(f"Successfully migrated {len(keys)} keys")
                    self._key_cache.update(keys)
                    return keys

            self.logger.warning(
                "No decryption keys found in JSON storage or legacy files"
            )
            return {}

        except StorageError as e:
            self.logger.error(f"Failed to load keys from storage: {e}")
            return {}
        except Exception as e:
            self.logger.error(f"Failed to load keys: {e}")
            return {}

    def clear_cache(self) -> None:
        """Clear the in-memory key cache."""
        self._key_cache.clear()
        self.logger.debug("Key cache cleared")


class AssetDecryptor:
    """Asset decryptor with XOR decryption of embedded Unity assets."""

    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.key_manager = KeyManager(self.config)
        self._decryption_stats = {
            "total_bytes": 0,
            "files_processed": 0,
            "files_failed": 0,
        }

    def generate_decryption_key(self, base_key: str) -> str:
        """Generate the MD5 hex digest used as the XOR key."""
        if not base_key:
            raise ValueError("Base key cannot be empty")

        return hashlib.md5((base_key + KEY_SALT).encode("utf-8")).hexdigest()

    @staticmethod
    def xor_bytes(data: bytes, key_bytes: bytes, offset: int = 0) -> bytes:
        """XOR data with a repeating key, starting at key position offset.

        Uses big-integer XOR so the whole buffer is processed in C instead of
        a per-byte Python loop.
        """
        if not key_bytes:
            raise ValueError("Decryption key cannot be empty")

        key_len = len(key_bytes)
        start = offset % key_len
        expanded = (key_bytes * ((len(data) + start) // key_len + 1))[
            start : start + len(data)
        ]
        return (int.from_bytes(data, "big") ^ int.from_bytes(expanded, "big")).to_bytes(
            len(data), "big"
        )

    def decrypt_file(self, input_path: Path, key: str, output_path: Path) -> bool:
        """XOR-decrypt a file in chunks with a continuous key stream."""
        try:
            key_bytes = self.generate_decryption_key(key).encode("utf-8")

            with open(input_path, "rb") as infile, open(output_path, "wb") as outfile:
                chunk_size = self.config.chunk_size
                bytes_processed = 0

                while True:
                    chunk = infile.read(chunk_size)
                    if not chunk:
                        break

                    decrypted_chunk = self.xor_bytes(
                        chunk, key_bytes, offset=bytes_processed
                    )
                    outfile.write(decrypted_chunk)
                    bytes_processed += len(chunk)

            if self.config.enable_validation and output_path.stat().st_size == 0:
                self.logger.warning(f"Decrypted file is empty: {output_path}")
                return False

            return True

        except OSError as e:
            self.logger.error(f"XOR decryption failed for {input_path}: {e}")
            return False

    def decrypt_asset(self, asset_path: Path, key: str) -> list[Path]:
        """Decrypt a Unity asset and extract its encrypted content.

        The asset contains TextAsset objects holding encrypted bundles; each
        is written to the encrypted directory and decrypted into the
        decrypted directory.
        """
        if not asset_path.exists():
            raise FileNotFoundError(f"Asset file not found: {asset_path}")

        decrypted_files = []

        try:
            with self.logger.time_operation(f"decrypt_{asset_path.stem}"):
                env = UnityPy.load(str(asset_path))

                for obj in env.objects:
                    if obj.type.name != "TextAsset":
                        continue

                    data = obj.read()

                    if not data.m_Name:
                        self.logger.debug(
                            f"Skipping unnamed TextAsset in {asset_path.name}"
                        )
                        continue

                    encrypted_path = self.config.encrypted_dir / f"{data.m_Name}.bytes"
                    decrypted_path = (
                        self.config.decrypted_dir / f"{data.m_Name}.unity3d"
                    )

                    if self._is_already_processed(encrypted_path, decrypted_path, data):
                        decrypted_files.append(decrypted_path)
                        continue

                    self._write_encrypted_data(encrypted_path, data)

                    if self.decrypt_file(encrypted_path, key, decrypted_path):
                        decrypted_files.append(decrypted_path)
                        self.logger.debug(f"Successfully decrypted: {data.m_Name}")
                    else:
                        self.logger.error(f"Failed to decrypt: {data.m_Name}")

        except DecryptionError:
            raise
        except Exception as e:
            raise DecryptionError(f"Failed to process asset {asset_path}: {e}") from e

        return decrypted_files

    def _is_already_processed(
        self, encrypted_path: Path, decrypted_path: Path, data: Any
    ) -> bool:
        """Check if file is already processed and up to date."""
        if not (encrypted_path.exists() and decrypted_path.exists()):
            return False

        expected_size = self._script_size(data)
        return encrypted_path.stat().st_size == expected_size

    @staticmethod
    def _script_size(data: Any) -> int:
        """Size of a TextAsset's script content in bytes."""
        if isinstance(data.m_Script, bytes):
            return len(data.m_Script)
        return len(str(data.m_Script).encode("utf-8", errors="surrogateescape"))

    def _write_encrypted_data(self, encrypted_path: Path, data: Any) -> None:
        """Write a TextAsset's encrypted data to disk."""
        try:
            encrypted_path.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(data.m_Script, bytes):
                encrypted_path.write_bytes(data.m_Script)
            else:
                # String data may contain surrogates produced by UnityPy
                encoded = str(data.m_Script).encode("utf-8", errors="surrogateescape")
                encrypted_path.write_bytes(encoded)
        except OSError as e:
            raise DecryptionError(f"Failed to write encrypted data: {e}") from e

    def decrypt_all_assets(
        self,
        keys: dict[str, str] | None = None,
        max_workers: int | None = None,
    ) -> tuple[list[Path], list[str]]:
        """Decrypt all downloaded assets using the provided keys.

        When keys is None, the weapon list is loaded and keys are fetched
        from the API first.
        """
        if keys is None:
            weapon_list = self._get_available_weapons()
            if not weapon_list:
                self.logger.warning("No weapons to decrypt")
                return [], []

            self.logger.info(f"Fetching keys for {len(weapon_list)} weapons")
            keys = self.key_manager.fetch_keys_parallel(weapon_list, max_workers)

        if not keys:
            self.logger.error("No decryption keys available")
            return [], []

        successful: list[Path] = []
        failed: list[str] = []

        assets = [
            f
            for f in self.config.assets_dir.glob("*.unity3d")
            if f.name != "spider_gen.unity3d"
        ]

        if not assets:
            self.logger.warning(f"No assets found in {self.config.assets_dir}")
            return [], []

        for asset_path in assets:
            asset_name = asset_path.stem

            if asset_name not in keys:
                self.logger.warning(f"No key found for {asset_name}")
                failed.append(asset_name)
                continue

            try:
                files = self.decrypt_asset(asset_path, keys[asset_name])
                successful.extend(files)
            except Exception as e:
                self.logger.error(f"Failed to decrypt {asset_name}: {e}")
                failed.append(asset_name)

        successful_assets = len(assets) - len(failed)
        self.logger.info(
            f"Decryption complete: {len(successful)} files from "
            f"{successful_assets}/{len(assets)} assets"
        )

        if failed:
            self.logger.warning(f"Failed to decrypt {len(failed)} assets")

        return successful, failed

    def _get_available_weapons(self) -> list[str]:
        """Get list of available weapons from storage or the assets directory."""
        storage = DataStorageManager(self.config)
        weapons = storage.get_weapons()
        if weapons:
            return weapons

        # Fallback: scan the assets directory
        if not self.config.assets_dir.exists():
            return []

        weapons = [
            asset_file.stem
            for asset_file in self.config.assets_dir.glob("*.unity3d")
            if asset_file.stem not in self.config.weapon_blacklist
        ]
        return sorted(weapons)

    def get_decryption_stats(self) -> dict[str, int]:
        """Get decryption statistics."""
        return self._decryption_stats.copy()

    def clear_stats(self) -> None:
        """Clear decryption statistics."""
        self._decryption_stats = {
            "total_bytes": 0,
            "files_processed": 0,
            "files_failed": 0,
        }
