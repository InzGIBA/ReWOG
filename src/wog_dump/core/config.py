"""Enhanced configuration management for WOG Dump using Pydantic with validation."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class WOGConfig(BaseModel):
    """Configuration for WOG Dump application with comprehensive validation."""

    # API Configuration
    data_base_url: str = Field(
        default="https://data1eu.ultimate-disassembly.com/uni2018",
        description="Base URL for downloading assets",
        pattern=r"^https?://[\w\-\.]+",
    )
    api_base_url: str = Field(
        default="https://eu1.ultimate-disassembly.com/v/query2018.php",
        description="Base URL for API queries",
        pattern=r"^https?://[\w\-\.]+",
    )

    # Directory Configuration
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

    # Performance Configuration
    max_threads: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Maximum number of threads for parallel operations",
    )
    chunk_size: int = Field(
        default=8192,
        ge=1024,
        le=1048576,
        description="Chunk size for file operations (bytes)",
    )
    request_timeout: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Request timeout in seconds",
    )

    # File Configuration
    data_file: Path | None = Field(
        default=None,
        description="Path to unified JSON data file",
    )
    # Legacy file paths for migration support
    weapons_file: Path | None = Field(
        default=None,
        description="Path to legacy weapons list file (for migration)",
    )
    keys_file: Path | None = Field(
        default=None,
        description="Path to legacy decryption keys file (for migration)",
    )

    # Authentication Configuration
    auth_id: int = Field(
        default=5684358,
        ge=1,
        description="Authentication ID for API queries",
    )
    auth_session: int = Field(
        default=15,
        ge=1,
        description="Authentication session for API queries",
    )
    device_id: str = Field(
        default="c8c52c9b992c6980ca6e15bf5006dff7488ce100",
        min_length=32,
        max_length=64,
        description="Device ID for API queries",
    )

    # Game Configuration
    game_mode: str = Field(
        default="DISASSEMBLY",
        description="Game mode for API queries",
    )
    game_version: str = Field(
        default="2.2.1z5",
        pattern=r"^\d+\.\d+\.\d+[a-z]\d+$",
        description="Game version for API queries",
    )
    unity_version: str = Field(
        default="2019.2.18f1",
        pattern=r"^\d+\.\d+\.\d+f\d+$",
        description="Unity version for API queries",
    )

    # Blacklist Configuration
    weapon_blacklist: list[str] = Field(
        default_factory=lambda: [
            "ac_cobra", "allosaurus", "cap_america", "delorean", "gorilla",
            "hmmwv", "hot_rod", "horse", "lion", "lotus_seven", "wolf",
            "ducati916", "corsair", "t72", "fnx_45", "korth_super_sport_alx",
            "canik_tp9", "swiss_army_knife_spartan", "beretta_dt11", "zis_tmp",
            "cat_349f", "drag_racing", "hk_g28", "tac_50", "groza_1",
            "glock_19x", "grand_power_k100", "browning_citori", "solothurn_s18_1000",
            "st_etienne_1907",
        ],
        description="List of weapons to exclude from processing",
    )
    texture_blacklist: list[str] = Field(
        default_factory=lambda: [f"shooting_{i:02d}" for i in range(1, 11)],
        description="List of textures to exclude from processing",
    )

    # Feature Flags
    enable_backup: bool = Field(
        default=True,
        description="Enable backup creation during operations",
    )
    enable_validation: bool = Field(
        default=True,
        description="Enable file validation after operations",
    )
    strict_mode: bool = Field(
        default=False,
        description="Enable strict validation and error handling",
    )

    model_config = {"extra": "forbid", "validate_assignment": True}

    @field_validator('base_dir', 'assets_dir', 'encrypted_dir', 'decrypted_dir', 'data_file', 'weapons_file', 'keys_file')
    @classmethod
    def validate_paths(cls, v: Path | None) -> Path | None:
        """Validate and resolve path fields."""
        if v is None:
            return v

        if isinstance(v, str):
            v = Path(v)

        # Resolve relative paths
        if not v.is_absolute():
            v = v.resolve()

        return v

    @model_validator(mode="after")
    def setup_directories(self) -> WOGConfig:
        """Set default paths and create directories."""
        # Set default paths relative to base_dir if not provided
        runtime_dir = self.base_dir / "runtime"

        if self.assets_dir is None:
            self.assets_dir = runtime_dir / "assets"
        if self.encrypted_dir is None:
            self.encrypted_dir = runtime_dir / "encrypted"
        if self.decrypted_dir is None:
            self.decrypted_dir = runtime_dir / "decrypted"
        if self.data_file is None:
            self.data_file = runtime_dir / "data.json"
        if self.weapons_file is None:
            self.weapons_file = runtime_dir / "weapons.txt"
        if self.keys_file is None:
            self.keys_file = runtime_dir / "keys.txt"

        # Create necessary directories
        self._create_directories()

        return self

    def _create_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        directories = [
            self.assets_dir,
            self.encrypted_dir,
            self.decrypted_dir,
        ]

        for directory in directories:
            if directory and not directory.exists():
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                except (PermissionError, OSError):
                    # Skip directory creation if we don't have permissions
                    # This can happen in tests or restricted environments
                    pass

        # Create parent directories for files
        file_paths = [self.data_file, self.weapons_file, self.keys_file]
        for file_path in file_paths:
            if file_path and not file_path.parent.exists():
                try:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                except (PermissionError, OSError):
                    # Skip directory creation if we don't have permissions
                    pass

    def get_api_headers(self) -> dict[str, str]:
        """Generate HTTP headers for API requests."""
        return {
            'Content-Type': 'application/octet-stream',
            'User-Agent': f'UnityPlayer/{self.unity_version} (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
            'Accept-Encoding': 'deflate, gzip',
            'Accept': '*/*',
            'X-Unity-Version': self.unity_version,
        }

    def get_combined_blacklist(self) -> set[str]:
        """Get combined blacklist as a set for efficient lookup."""
        return set(self.weapon_blacklist + self.texture_blacklist)

    def is_blacklisted(self, item_name: str) -> bool:
        """Check if an item is blacklisted."""
        blacklist = self.get_combined_blacklist()
        return item_name.lower() in {item.lower() for item in blacklist}

    def get_stats(self) -> dict[str, int | str]:
        """Get configuration statistics."""
        return {
            "weapon_blacklist_count": len(self.weapon_blacklist),
            "texture_blacklist_count": len(self.texture_blacklist),
            "max_threads": self.max_threads,
            "chunk_size_kb": self.chunk_size // 1024,
            "game_version": self.game_version,
            "unity_version": self.unity_version,
        }

    @classmethod
    def from_env(cls) -> WOGConfig:
        """Create configuration from environment variables."""
        env_mapping = {
            'WOG_BASE_DIR': 'base_dir',
            'WOG_MAX_THREADS': 'max_threads',
            'WOG_AUTH_ID': 'auth_id',
            'WOG_AUTH_SESSION': 'auth_session',
            'WOG_DEVICE_ID': 'device_id',
            'WOG_GAME_VERSION': 'game_version',
            'WOG_UNITY_VERSION': 'unity_version',
            'WOG_STRICT_MODE': 'strict_mode',
        }

        kwargs = {}
        for env_key, config_key in env_mapping.items():
            if env_key in os.environ:
                value = os.environ[env_key]
                # Handle boolean conversion
                if config_key in ['strict_mode', 'enable_backup', 'enable_validation']:
                    value = value.lower() in ('true', '1', 'yes', 'on')
                # Handle integer conversion
                elif config_key in ['max_threads', 'auth_id', 'auth_session']:
                    value = int(value)
                kwargs[config_key] = value

        return cls(**kwargs)


# Global configuration management
class ConfigManager:
    """Singleton configuration manager."""

    _instance: WOGConfig | None = None
    _initialized: bool = False

    @classmethod
    def get_config(cls) -> WOGConfig:
        """Get the global configuration instance."""
        if cls._instance is None:
            cls._instance = WOGConfig()
            cls._initialized = True
        return cls._instance

    @classmethod
    def set_config(cls, **kwargs) -> WOGConfig:
        """Update configuration parameters."""
        if cls._instance is None:
            cls._instance = WOGConfig(**kwargs)
        else:
            # Update existing instance
            for key, value in kwargs.items():
                if hasattr(cls._instance, key):
                    setattr(cls._instance, key, value)

        cls._initialized = True
        return cls._instance

    @classmethod
    def reset_config(cls) -> None:
        """Reset configuration to default values."""
        cls._instance = None
        cls._initialized = False

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if configuration has been initialized."""
        return cls._initialized


# Convenience functions for backward compatibility
def get_config() -> WOGConfig:
    """Get the global configuration instance."""
    return ConfigManager.get_config()


def set_config(**kwargs) -> WOGConfig:
    """Set configuration parameters and return the updated config."""
    return ConfigManager.set_config(**kwargs)


def reset_config() -> None:
    """Reset configuration to default values."""
    ConfigManager.reset_config()
