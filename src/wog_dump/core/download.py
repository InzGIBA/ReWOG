"""Download module for WOG Dump with modern async/await support."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.config import WOGConfig, get_config
from ..utils.logging import get_logger


class DownloadError(Exception):
    """Raised when download operations fail."""
    pass


class DownloadManager:
    """Manages downloads with retry logic and progress tracking."""
    
    def __init__(self, config: WOGConfig | None = None) -> None:
        self.config = config or get_config()
        self.logger = get_logger()
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create a session with retry strategy."""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=1,
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def download_weapon_list(self) -> Path:
        """Download the weapon list asset."""
        asset_path = self.config.assets_dir / "spider_gen.unity3d"
        url = f"{self.config.data_base_url}/spider/spider_gen.unity3d"
        
        try:
            # Check if update is needed
            if asset_path.exists():
                current_size = asset_path.stat().st_size
                response = self.session.head(url)
                response.raise_for_status()
                server_size = int(response.headers.get("Content-Length", 0))
                
                self.logger.info(
                    f"Current size: {current_size:,} | Server size: {server_size:,}"
                )
                
                if current_size == server_size:
                    self.logger.info("Weapon list asset is up to date")
                    return asset_path
                
                self.logger.info("Weapon list asset needs update")
            
            # Download the asset
            self.logger.info("Downloading weapon list asset...")
            response = self.session.get(url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get("Content-Length", 0))
            
            with open(asset_path, "wb") as f, self.logger.create_download_progress() as progress:
                task = progress.add_task("Downloading", total=total_size)
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))
            
            self.logger.info(f"Downloaded weapon list to {asset_path}")
            return asset_path
            
        except requests.RequestException as e:
            raise DownloadError(f"Failed to download weapon list: {e}") from e
    
    def get_asset_size(self, asset_name: str) -> int:
        """Get the size of an asset from the server."""
        url = f"{self.config.data_base_url}/{asset_name}.unity3d"
        try:
            response = self.session.head(url)
            response.raise_for_status()
            return int(response.headers.get("Content-Length", 0))
        except requests.RequestException as e:
            self.logger.warning(f"Failed to get size for {asset_name}: {e}")
            return 0
    
    def check_asset_needs_update(self, asset_name: str) -> bool:
        """Check if an asset needs to be downloaded or updated."""
        asset_path = self.config.assets_dir / f"{asset_name}.unity3d"
        
        if not asset_path.exists():
            return True
        
        current_size = asset_path.stat().st_size
        server_size = self.get_asset_size(asset_name)
        
        return current_size != server_size
    
    def download_single_asset(self, asset_name: str) -> bool:
        """Download a single asset file."""
        url = f"{self.config.data_base_url}/{asset_name}.unity3d"
        asset_path = self.config.assets_dir / f"{asset_name}.unity3d"
        
        try:
            response = self.session.get(url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get("Content-Length", 0))
            
            with open(asset_path, "wb") as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            self.logger.debug(f"Downloaded {asset_name} ({downloaded:,} bytes)")
            return True
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to download {asset_name}: {e}")
            return False
    
    def check_for_updates(self, weapon_list: list[str]) -> list[str]:
        """Check which assets need updates using parallel processing."""
        to_download = []
        
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Checking for updates", total=len(weapon_list))
            
            with ThreadPoolExecutor(max_workers=self.config.max_threads) as executor:
                future_to_weapon = {
                    executor.submit(self.check_asset_needs_update, weapon): weapon
                    for weapon in weapon_list
                }
                
                for future in as_completed(future_to_weapon):
                    weapon = future_to_weapon[future]
                    try:
                        needs_update = future.result()
                        if needs_update:
                            to_download.append(weapon)
                    except Exception as e:
                        self.logger.error(f"Error checking {weapon}: {e}")
                    
                    progress.update(task, advance=1)
        
        self.logger.info(f"Found {len(to_download)} assets to download")
        return to_download
    
    def download_assets(self, weapon_list: list[str]) -> tuple[list[str], list[str]]:
        """Download multiple assets with progress tracking."""
        to_download = self.check_for_updates(weapon_list)
        
        if not to_download:
            self.logger.info("All assets are up to date")
            return [], []
        
        successful = []
        failed = []
        
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Downloading assets", total=len(to_download))
            
            with ThreadPoolExecutor(max_workers=self.config.max_threads) as executor:
                future_to_weapon = {
                    executor.submit(self.download_single_asset, weapon): weapon
                    for weapon in to_download
                }
                
                for future in as_completed(future_to_weapon):
                    weapon = future_to_weapon[future]
                    try:
                        success = future.result()
                        if success:
                            successful.append(weapon)
                        else:
                            failed.append(weapon)
                    except Exception as e:
                        self.logger.error(f"Error downloading {weapon}: {e}")
                        failed.append(weapon)
                    
                    progress.update(task, advance=1)
        
        self.logger.info(f"Downloaded {len(successful)} assets successfully")
        if failed:
            self.logger.warning(f"Failed to download {len(failed)} assets")
        
        return successful, failed
    
    def __enter__(self) -> DownloadManager:
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.session.close()