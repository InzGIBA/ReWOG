"""Asset decryption for WOG Dump.

XOR-decrypts the encrypted Unity bundles embedded in downloaded assets.
Key fetching lives in ``core/keys.py``; this module owns the XOR stream
and the per-asset bundle extraction.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import UnityPy

from ..utils.logging import get_logger
from .config import WOGConfig, get_config
from .database import WOGDatabase
from .keys import (
    AuthenticationError,
    DecryptionError,
    KeyManager,
    NetworkError,
)

__all__ = [
    "AssetDecryptor",
    "AuthenticationError",
    "DecryptionError",
    "KeyManager",
    "NetworkError",
    "KEY_SALT",
]

# Appended to every server-provided key before hashing
KEY_SALT = "World of Guns: Gun Disassembly"


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

        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Decrypting assets", total=len(assets))

            for asset_path in assets:
                asset_name = asset_path.stem

                if asset_name not in keys:
                    self.logger.warning(f"No key found for {asset_name}")
                    failed.append(asset_name)
                    progress.update(task, advance=1)
                    continue

                try:
                    files = self.decrypt_asset(asset_path, keys[asset_name])
                    successful.extend(files)
                except Exception as e:
                    self.logger.error(f"Failed to decrypt {asset_name}: {e}")
                    failed.append(asset_name)

                if failed:
                    progress.update(
                        task, description=f"Decrypting assets ({len(failed)} failed)"
                    )
                progress.update(task, advance=1)

        successful_assets = len(assets) - len(failed)
        self.logger.info(
            f"Decryption complete: {len(successful)} files from "
            f"{successful_assets}/{len(assets)} assets"
        )

        if failed:
            self.logger.warning(f"Failed to decrypt {len(failed)} assets")

        return successful, failed

    def _get_available_weapons(self) -> list[str]:
        """Get list of available weapons from the database or the assets directory."""
        db = WOGDatabase(self.config)
        weapons = db.get_weapons()
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
