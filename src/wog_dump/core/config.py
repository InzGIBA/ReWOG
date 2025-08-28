"""Configuration management for WOG Dump using Pydantic."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class WOGConfig(BaseModel):
    """Configuration for WOG Dump application."""

    # Base URLs
    data_base_url: str = Field(
        default="https://data1eu.ultimate-disassembly.com/uni2018",
        description="Base URL for downloading assets",
    )
    api_base_url: str = Field(
        default="https://eu1.ultimate-disassembly.com/v/query2018.php",
        description="Base URL for API queries",
    )
    
    # Directories
    base_dir: Path = Field(
        default_factory=lambda: Path.cwd(),
        description="Base directory for the application",
    )
    assets_dir: Path | None = Field(
        default=None,
        description="Directory for downloaded assets",
    )
    encrypted_dir: Path | None = Field(
        default=None,
        description="Directory for encrypted files",
    )
    decrypted_dir: Path | None = Field(
        default=None,
        description="Directory for decrypted files",
    )
    
    # Threading and performance
    max_threads: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Maximum number of threads for parallel operations",
    )
    
    # File paths
    weapons_file: Path | None = Field(
        default=None,
        description="Path to weapons list file",
    )
    keys_file: Path | None = Field(
        default=None,
        description="Path to decryption keys file",
    )
    
    # Game-specific settings
    game_version: str = Field(
        default="2.2.1z5",
        description="Game version for API queries",
    )
    unity_version: str = Field(
        default="2019.2.18f1",
        description="Unity version for API queries",
    )
    device_id: str = Field(
        default="e35c060a502dd9fdee3bfa107ab0cc24477f6a1a",
        description="Device ID for API queries",
    )
    
    # Blacklists
    weapon_blacklist: list[str] = Field(
        default_factory=lambda: [
            "hk_g28", "drag_racing", "tac_50", "zis_tmp", 
            "groza_1", "glock_19x", "cat_349f"
        ],
        description="List of weapons to exclude from processing",
    )
    texture_blacklist: list[str] = Field(
        default_factory=lambda: [
            f"shooting_{i:02d}" for i in range(1, 11)
        ],
        description="List of textures to exclude from processing",
    )
    
    model_config = {"extra": "forbid", "validate_assignment": True}
    
    @model_validator(mode="after")
    def set_default_paths(self) -> "WOGConfig":
        """Set default paths relative to base_dir if not provided."""
        if self.assets_dir is None:
            self.assets_dir = self.base_dir / "assets"
        if self.encrypted_dir is None:
            self.encrypted_dir = self.base_dir / "encrypted"
        if self.decrypted_dir is None:
            self.decrypted_dir = self.base_dir / "decrypted"
        if self.weapons_file is None:
            self.weapons_file = self.base_dir / "weapons.txt"
        if self.keys_file is None:
            self.keys_file = self.base_dir / "keys.txt"
            
        # Create directories after setting paths
        for dir_path in [self.assets_dir, self.encrypted_dir, self.decrypted_dir]:
            if dir_path:
                dir_path.mkdir(parents=True, exist_ok=True)
        
        return self
    
    def get_api_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            'Content-Type': 'application/octet-stream',
            'User-Agent': f'UnityPlayer/{self.unity_version} (UnityWebRequest/1.0, libcurl/7.52.0-DEV)',
            'Accept-Encoding': 'identity',
            'Accept': '*/*',
            'X-Unity-Version': self.unity_version,
        }
    
    def get_combined_blacklist(self) -> list[str]:
        """Get combined blacklist of weapons and textures."""
        return list(set(self.weapon_blacklist + self.texture_blacklist))


# Global configuration instance
_config: WOGConfig | None = None


def get_config() -> WOGConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = WOGConfig()
    return _config


def set_config(**kwargs: object) -> WOGConfig:
    """Set configuration parameters and return the updated config."""
    global _config
    if _config is None:
        _config = WOGConfig(**kwargs)
    else:
        for key, value in kwargs.items():
            if hasattr(_config, key):
                setattr(_config, key, value)
    return _config


def reset_config() -> None:
    """Reset configuration to default values."""
    global _config
    _config = None