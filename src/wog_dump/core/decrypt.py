"""Decryption module for WOG Dump with XOR decryption support."""

from __future__ import annotations

import bz2
import hashlib
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
        """Get decryption key for a specific asset."""
        data = (
            f"query=3&model={asset_name}&mode=FIELD_STRIP&need_details=1&"
            f"ver={self.config.game_version}&uver={self.config.unity_version}&"
            f"dev={self.config.device_id}&session=37&id=5390315&"
            f"time={int(time.time())}"
        )
        
        try:
            # Compress the data
            compressed_data = bz2.compress(data.encode())
            
            # Create request payload
            data_io = BytesIO(compressed_data)
            length = len(compressed_data)
            payload = length.to_bytes(4, "little") + compressed_data
            
            headers = self.config.get_api_headers()
            headers['Content-Length'] = str(len(payload))
            
            # Send request
            response = self.session.put(
                f"{self.config.api_base_url}?soc=steam",
                data=payload,
                headers=headers
            )
            response.raise_for_status()
            
            # Process response
            response_data = response.content[4:]  # Remove first 4 bytes
            decompressed = bz2.decompress(response_data).decode()
            
            # Extract key
            if "sync=" in decompressed:
                key = decompressed.split("sync=")[1].split("&")[0]
                return key
            
            return None
            
        except (requests.RequestException, OSError, UnicodeDecodeError) as e:
            self.logger.error(f"Failed to get key for {asset_name}: {e}")
            return None
    
    def fetch_keys_parallel(self, weapon_list: list[str]) -> dict[str, str]:
        """Fetch keys for multiple weapons in parallel."""
        keys = {}
        
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
                        else:
                            self.logger.warning(f"No key found for {weapon}")
                    except Exception as e:
                        self.logger.error(f"Error getting key for {weapon}: {e}")
                    
                    progress.update(task, advance=1)
        
        self.logger.info(f"Fetched {len(keys)} keys successfully")
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
                    encrypted_path = self.config.encrypted_dir / f"{data.name}.bytes"
                    decrypted_path = self.config.decrypted_dir / f"{data.name}.unity3d"
                    
                    # Skip if already processed and sizes match
                    if (decrypted_path.exists() and 
                        encrypted_path.exists() and 
                        encrypted_path.stat().st_size == data.script.nbytes):
                        self.logger.debug(f"Already decrypted: {data.name}")
                        decrypted_files.append(decrypted_path)
                        continue
                    
                    # Write encrypted data
                    with open(encrypted_path, "wb") as f:
                        f.write(bytes(data.script))
                    
                    # Generate decryption key
                    decryption_key = self.generate_decryption_key(key)
                    
                    # Use Python XOR implementation
                    success = self.decrypt_with_python(encrypted_path, decryption_key, decrypted_path)
                    
                    if success:
                        decrypted_files.append(decrypted_path)
                        self.logger.debug(f"Decrypted: {data.name}")
                    else:
                        self.logger.error(f"Failed to decrypt: {data.name}")
            
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