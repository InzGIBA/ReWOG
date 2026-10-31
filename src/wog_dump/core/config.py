"""Configuration management for WOG Dump using Pydantic with validation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

DB_FILE_NAME = "wog_dump.db"


def default_db_path() -> Path:
    """Per-user database location (honors WOG_DB_PATH).

    Secrets (keys, account credentials) live outside the repository in a
    user data directory.
    """
    override = os.environ.get("WOG_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "wog_dump"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "wog_dump"
    else:
        base = (
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            / "wog_dump"
        )
    return base / DB_FILE_NAME


# (field name, file name) pairs resolved relative to base_dir/runtime
RUNTIME_PATH_DEFAULTS = {
    "assets_dir": "assets",
    "encrypted_dir": "encrypted",
    "decrypted_dir": "decrypted",
    "data_file": "data.json",
    "weapons_file": "weapons.txt",
    "keys_file": "keys.txt",
}


class WOGConfig(BaseModel):
    """Configuration for WOG Dump application with comprehensive validation."""

    # API Configuration
    data_base_url: str = Field(
        default="https://data1eu.ultimate-disassembly.com/uni2018",
        description="Base URL for downloading assets",
        pattern=r"^https?://[\w\-\.]+",
    )
    api_base_url: str = Field(
        default="https://eu1.ultimate-disassembly.com/v/query2026.php",
        description="Base URL for API queries",
        pattern=r"^https?://[\w\-\.]+",
    )

    # Directory Configuration
    base_dir: Path = Field(
        default_factory=lambda: Path.cwd(),
        description="Base directory for the application",
    )
    assets_dir: Path = Field(description="Directory for downloaded assets")
    encrypted_dir: Path = Field(description="Directory for encrypted files")
    decrypted_dir: Path = Field(description="Directory for decrypted files")

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
    request_delay: float = Field(
        default=0.1,
        ge=0.0,
        le=60.0,
        description="Minimum interval between asset-server requests in seconds",
    )
    api_request_delay: float = Field(
        default=0.25,
        ge=0.0,
        le=60.0,
        description="Minimum interval between key-API requests in seconds",
    )

    # File Configuration
    data_file: Path = Field(description="Path to unified JSON data file")
    weapons_file: Path = Field(description="Path to legacy weapons list file")
    keys_file: Path = Field(description="Path to legacy decryption keys file")

    # Local database (keys, weapons, auth - never committed to the repo)
    db_path: Path = Field(
        default_factory=lambda: default_db_path(),
        description="Path to the local SQLite database",
    )

    # Game Configuration
    game_mode: str = Field(
        default="DISASSEMBLY",
        description="Game mode for API queries",
    )
    game_version: str = Field(
        default="2.2.3u9",
        pattern=r"^\d+\.\d+\.\d+[a-z]\d+$",
        description="Game version for API queries",
    )
    unity_version: str = Field(
        default="6000.0.84f1",
        pattern=r"^\d+\.\d+\.\d+f\d+$",
        description="Unity version for API queries",
    )

    # Blacklist Configuration
    weapon_blacklist: list[str] = Field(
        default_factory=lambda: [
            "ac_cobra",
            "allosaurus",
            "cap_america",
            "delorean",
            "gorilla",
            "hmmwv",
            "hot_rod",
            "horse",
            "lion",
            "lotus_seven",
            "wolf",
            "ducati916",
            "corsair",
            "t72",
            "fnx_45",
            "korth_super_sport_alx",
            "canik_tp9",
            "swiss_army_knife_spartan",
            "beretta_dt11",
            "zis_tmp",
            "cat_349f",
            "drag_racing",
            "hk_g28",
            "tac_50",
            "groza_1",
            "glock_19x",
            "grand_power_k100",
            "browning_citori",
            "solothurn_s18_1000",
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

    @model_validator(mode="before")
    @classmethod
    def _fill_runtime_paths(cls, data: Any) -> Any:
        """Resolve path fields that were not provided to base_dir/runtime defaults."""
        if isinstance(data, dict):
            base_dir = Path(data.get("base_dir") or Path.cwd())
            runtime_dir = base_dir / "runtime"
            for field_name, default_name in RUNTIME_PATH_DEFAULTS.items():
                if not data.get(field_name):
                    data[field_name] = runtime_dir / default_name
        return data

    @field_validator(
        "base_dir",
        "assets_dir",
        "encrypted_dir",
        "decrypted_dir",
        "data_file",
        "weapons_file",
        "keys_file",
        "db_path",
    )
    @classmethod
    def validate_paths(cls, v: Path) -> Path:
        """Resolve relative paths to absolute ones."""
        if not v.is_absolute():
            v = v.resolve()
        return v

    @model_validator(mode="after")
    def _create_directories(self) -> WOGConfig:
        """Create the runtime directories if they don't exist yet."""
        for directory in (self.assets_dir, self.encrypted_dir, self.decrypted_dir):
            if not directory.exists():
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                except OSError:
                    # Skip directory creation in restricted environments (tests, read-only FS)
                    pass

        for file_path in (self.data_file, self.weapons_file, self.keys_file):
            if not file_path.parent.exists():
                try:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass

        return self

    def get_api_headers(self) -> dict[str, str]:
        """Generate HTTP headers for API requests."""
        return {
            "Content-Type": "application/octet-stream",
            "User-Agent": f"UnityPlayer/{self.unity_version} (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
            "Accept-Encoding": "deflate, gzip",
            "Accept": "*/*",
            "X-Unity-Version": self.unity_version,
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
            "WOG_BASE_DIR": "base_dir",
            "WOG_MAX_THREADS": "max_threads",
            "WOG_API_BASE_URL": "api_base_url",
            "WOG_DATA_BASE_URL": "data_base_url",
            "WOG_GAME_VERSION": "game_version",
            "WOG_UNITY_VERSION": "unity_version",
            "WOG_STRICT_MODE": "strict_mode",
            "WOG_ENABLE_BACKUP": "enable_backup",
            "WOG_ENABLE_VALIDATION": "enable_validation",
            "WOG_REQUEST_DELAY": "request_delay",
            "WOG_API_REQUEST_DELAY": "api_request_delay",
        }
        bool_fields = {"strict_mode", "enable_backup", "enable_validation"}
        int_fields = {"max_threads"}
        float_fields = {"request_delay", "api_request_delay"}

        kwargs: dict[str, Any] = {}
        for env_key, config_key in env_mapping.items():
            if env_key not in os.environ:
                continue
            raw = os.environ[env_key]
            if config_key in bool_fields:
                kwargs[config_key] = raw.lower() in ("true", "1", "yes", "on")
            elif config_key in int_fields:
                kwargs[config_key] = int(raw)
            elif config_key in float_fields:
                kwargs[config_key] = float(raw)
            else:
                kwargs[config_key] = raw

        # Path fields are filled in by the _fill_runtime_paths validator
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
            # Path fields are filled in by the _fill_runtime_paths validator
            cls._instance = WOGConfig()  # type: ignore[call-arg]
            cls._initialized = True
        return cls._instance

    @classmethod
    def set_config(cls, **kwargs: Any) -> WOGConfig:
        """Update configuration parameters."""
        if cls._instance is None:
            cls._instance = WOGConfig(**kwargs)
        else:
            # Update existing instance
            for key, value in kwargs.items():
                if hasattr(cls._instance, key):
                    setattr(cls._instance, key, value)

        cls._initialized = True
        assert cls._instance is not None
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


# Convenience functions
def get_config() -> WOGConfig:
    """Get the global configuration instance."""
    return ConfigManager.get_config()


def set_config(**kwargs: Any) -> WOGConfig:
    """Set configuration parameters and return the updated config."""
    return ConfigManager.set_config(**kwargs)


def reset_config() -> None:
    """Reset configuration to default values."""
    ConfigManager.reset_config()
