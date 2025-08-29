"""Decryption module for WOG Dump with XOR decryption support."""

from __future__ import annotations

import bz2
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import requests
import UnityPy

from ..core.config import WOGConfig, get_config
from ..utils.logging import get_logger


class DecryptionError(Exception):
    """Raised when decryption operations fail."""
    pass


class KeyManager:
    """Manages decryption keys for WOG assets."""
    
    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.session = requests.Session()
    
    def get_key_for_asset(self, asset_name: str) -> str | None:
        """Get decryption key for a specific asset.
        
        Args:
            asset_name: Name of the asset to get key for
        """
        
        # Create API request data
        current_time = int(time.time())
        
        # Generate hash for request integrity (simplified version)
        data = (
            f"query=3&"
            f"model={asset_name}&"
            f"need_details=1&"
            # Auth
            f"session={self.config.auth_session}&"
            f"id={self.config.auth_id}&"
            f"dev={self.config.device_id}&"
            # Game settings
            f"mode={self.config.game_mode}&"
            f"ver={self.config.game_version}&"
            f"uver={self.config.unity_version}&"
            #
            f"time={current_time}"
        )

        try:
            # Compress the data using BZ2
            compressed_data = bz2.compress(data.encode())
            
            # Create request payload with length prefix (4 bytes little-endian)
            length = len(compressed_data)
            payload = length.to_bytes(4, "little") + compressed_data
            
            headers = self.config.get_api_headers()
            headers['Content-Length'] = str(len(payload))
            
            # Send request to the API endpoint
            response = self.session.put(
                f"{self.config.api_base_url}?soc=steam",
                data=payload,
                headers=headers
            )
            response.raise_for_status()
            
            # Process response - remove 4-byte length prefix and decompress
            if len(response.content) < 4:
                self.logger.error(f"Invalid response length for {asset_name}")
                return None
                
            response_data = response.content[4:]
            decompressed = bz2.decompress(response_data).decode()

            # Parse response
            if "result=0" in decompressed:
                # Success - look for sync key or alternative key format
                if "sync=" in decompressed:
                    key = decompressed.split("sync=")[1].split("&")[0]
                    self.logger.debug(f"Found sync key for {asset_name}: {key}")
                    return key
                else:
                    # Look for alternative key formats in successful responses
                    # Based on analysis, the API may return keys in different formats
                    self.logger.info(f"API returned success for {asset_name} but no sync key found")
                    self.logger.debug(f"Response content: {decompressed[:200]}...")
                    return None
            else:
                # Check for specific error codes
                if "result=100" in decompressed:
                    self.logger.warning(f"Authentication error for {asset_name} (result=100)")
                    self.logger.debug(f"Full API response: {decompressed}")
                elif "result=1000" in decompressed:
                    self.logger.warning(f"Server error for {asset_name} (result=1000)")
                    self.logger.debug(f"Full API response: {decompressed}")
                else:
                    result_match = re.search(r'result=(\d+)', decompressed)
                    result_code = result_match.group(1) if result_match else "unknown"
                    self.logger.warning(f"API error for {asset_name} (result={result_code})")
                    self.logger.debug(f"Full API response: {decompressed}")
                
                return None
            
        except bz2.BZ2Error as e:
            self.logger.error(f"BZ2 decompression failed for {asset_name}: {e}")
            return None
        except (requests.RequestException, OSError, UnicodeDecodeError) as e:
            self.logger.error(f"Network error getting key for {asset_name}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error getting key for {asset_name}: {e}")
            return None
    
    def fetch_keys_parallel(self, weapon_list: list[str]) -> dict[str, str]:
        """Fetch keys for multiple weapons in parallel.
        
        Args:
            weapon_list: List of weapon names to fetch keys for
            
        Returns:
            Dictionary mapping weapon names to decryption keys
        """
        keys = {}
        successful_fetches = 0
        
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Fetching decryption keys", total=len(weapon_list))
            
            with ThreadPoolExecutor(max_workers=self.config.max_threads) as executor:
                future_to_weapon = {
                    executor.submit(self.get_key_for_asset, weapon): weapon
                    for weapon in weapon_list
                }
                
                for future in as_completed(future_to_weapon):
                    weapon = future_to_weapon[future]
                    try:
                        key = future.result()
                        if key:
                            keys[weapon] = key
                            successful_fetches += 1
                            self.logger.debug(f"Successfully fetched key for {weapon}")
                        else:
                            self.logger.warning(f"No key found for {weapon}")
                    except Exception as e:
                        self.logger.error(f"Error getting key for {weapon}: {e}")
                    
                    progress.update(task, advance=1)
        
        # Provide feedback based on results
        if successful_fetches > 0:
            self.logger.info(f"Successfully fetched {successful_fetches} keys out of {len(weapon_list)} requested")
        
        return keys
    
    def save_keys(self, keys: dict[str, str]) -> None:
        """Save keys to file."""
        try:
            with open(self.config.keys_file, "w", encoding="utf-8") as f:
                for weapon, key in keys.items():
                    f.write(f"{weapon} {key}\n")
            
            self.logger.info(f"Saved {len(keys)} keys to {self.config.keys_file}")
            
        except IOError as e:
            raise DecryptionError(f"Failed to save keys: {e}") from e
    
    def load_keys(self) -> dict[str, str]:
        """Load keys from file."""
        keys = {}
        
        if not self.config.keys_file.exists():
            self.logger.warning(f"Keys file {self.config.keys_file} not found")
            return keys
        
        try:
            with open(self.config.keys_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        asset_name, key = parts
                        keys[asset_name] = key
                    else:
                        self.logger.warning(f"Invalid key format at line {line_num}: {line}")
            
            self.logger.info(f"Loaded {len(keys)} keys from {self.config.keys_file}")
            return keys
            
        except IOError as e:
            raise DecryptionError(f"Failed to load keys: {e}") from e


class AssetDecryptor:
    """Handles decryption of Unity assets."""
    
    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
    
    def generate_decryption_key(self, base_key: str) -> str:
        """Generate the final decryption key from base key."""
        full_key = base_key + "World of Guns: Gun Disassembly"
        return hashlib.md5(full_key.encode()).hexdigest()
    

    
    def decrypt_with_python(self, input_path: Path, key: str, output_path: Path) -> bool:
        """Decrypt using optimized Python XOR implementation."""
        try:
            key_bytes = key.encode()
            key_len = len(key_bytes)
            chunk_size = 8192  # 8KB chunks for better performance
            
            with open(input_path, "rb") as infile, open(output_path, "wb") as outfile:
                key_index = 0
                
                while True:
                    chunk = infile.read(chunk_size)
                    if not chunk:
                        break
                    
                    # XOR the entire chunk
                    decrypted_chunk = bytearray()
                    for byte in chunk:
                        decrypted_chunk.append(byte ^ key_bytes[key_index % key_len])
                        key_index += 1
                    
                    outfile.write(decrypted_chunk)
            
            return True
            
        except IOError as e:
            self.logger.error(f"Python XOR decryption failed: {e}")
            return False
    
    def decrypt_asset(self, asset_path: Path, key: str) -> list[Path]:
        """Decrypt a single asset and extract encrypted content."""
        decrypted_files = []
        
        try:
            env = UnityPy.load(str(asset_path))
            
            for obj in env.objects:
                if obj.type.name == "TextAsset":
                    data = obj.read()
                    
                    # Create encrypted file
                    encrypted_path = self.config.encrypted_dir / f"{data.m_Name}.bytes"
                    decrypted_path = self.config.decrypted_dir / f"{data.m_Name}.unity3d"
                    
                    # Calculate expected size based on data type
                    if isinstance(data.m_Script, bytes):
                        expected_size = len(data.m_Script)
                    else:
                        # For strings, try to encode and handle surrogates
                        try:
                            expected_size = len(str(data.m_Script).encode('utf-8'))
                        except UnicodeEncodeError:
                            # If string contains surrogates, encode with error handling
                            expected_size = len(str(data.m_Script).encode('utf-8', errors='surrogateescape'))
                    
                    # Skip if already processed and sizes match
                    if (decrypted_path.exists() and 
                        encrypted_path.exists() and 
                        encrypted_path.stat().st_size == expected_size):
                        self.logger.debug(f"Already decrypted: {data.m_Name}")
                        decrypted_files.append(decrypted_path)
                        continue
                    
                    # Write encrypted data - handle both bytes and string types
                    if isinstance(data.m_Script, bytes):
                        with open(encrypted_path, "wb") as f:
                            f.write(data.m_Script)
                    else:
                        # Convert string to bytes with proper error handling
                        try:
                            with open(encrypted_path, "wb") as f:
                                f.write(str(data.m_Script).encode('utf-8'))
                        except UnicodeEncodeError:
                            # Handle strings with surrogates that can't be encoded to UTF-8
                            with open(encrypted_path, "wb") as f:
                                f.write(str(data.m_Script).encode('utf-8', errors='surrogateescape'))
                    
                    # Generate decryption key
                    decryption_key = self.generate_decryption_key(key)
                    
                    # Use Python XOR implementation
                    success = self.decrypt_with_python(encrypted_path, decryption_key, decrypted_path)
                    
                    if success:
                        decrypted_files.append(decrypted_path)
                        self.logger.debug(f"Decrypted: {data.m_Name}")
                    else:
                        self.logger.error(f"Failed to decrypt: {data.m_Name}")
            
        except Exception as e:
            self.logger.error(f"Failed to process asset {asset_path}: {e}")
        
        return decrypted_files
    
    def decrypt_all_assets(self, keys: dict[str, str]) -> tuple[list[Path], list[str]]:
        """Decrypt all assets using provided keys."""
        assets = [
            f for f in self.config.assets_dir.glob("*.unity3d")
            if f.name != "spider_gen.unity3d"
        ]
        
        decrypted_files = []
        failed_assets = []
        
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Decrypting assets", total=len(assets))
            
            for asset_path in assets:
                asset_name = asset_path.stem
                
                if asset_name not in keys:
                    self.logger.warning(f"No key found for {asset_name}")
                    failed_assets.append(asset_name)
                    progress.update(task, advance=1)
                    continue
                
                key = keys[asset_name]
                
                try:
                    files = self.decrypt_asset(asset_path, key)
                    decrypted_files.extend(files)
                except Exception as e:
                    self.logger.error(f"Failed to decrypt {asset_name}: {e}")
                    failed_assets.append(asset_name)
                
                progress.update(task, advance=1)
        
        self.logger.info(f"Decrypted {len(decrypted_files)} files from {len(assets)} assets")
        if failed_assets:
            self.logger.warning(f"Failed to decrypt {len(failed_assets)} assets")
        
        return decrypted_files, failed_assets