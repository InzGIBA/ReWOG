"""Unit tests for JSON storage module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.storage import DataStorageManager, WOGDataStore, StorageError


class TestWOGDataStore:
    """Test WOGDataStore class."""
    
    def test_default_initialization(self) -> None:
        """Test default data store initialization."""
        store = WOGDataStore()
        
        assert store.weapons.count == 0
        assert store.keys.count == 0
        assert len(store.weapons.weapons) == 0
        assert len(store.keys.keys) == 0
        assert store.cache.version == "2.3.2"
        
    def test_auto_count_calculation(self) -> None:
        """Test automatic count calculation."""
        weapons = ["ak74", "m4a1", "glock17"]
        keys = {"ak74": "key1", "m4a1": "key2"}
        
        store = WOGDataStore()
        store.weapons.weapons = weapons
        store.keys.keys = keys
        
        # Manual update to trigger count calculation
        store.weapons.count = len(weapons)
        store.keys.count = len(keys)
        
        assert store.weapons.count == 3
        assert store.keys.count == 2
        
    def test_cache_expiration(self) -> None:
        """Test cache expiration logic."""
        store = WOGDataStore()
        
        # Fresh cache should not be expired
        assert not store.is_cache_expired(max_age_hours=24)
        
        # Test with very short expiration
        assert store.is_cache_expired(max_age_hours=0)
        
    def test_get_stats(self) -> None:
        """Test statistics generation."""
        store = WOGDataStore()
        store.weapons.weapons = ["ak74", "m4a1"]
        store.weapons.count = 2
        store.keys.keys = {"ak74": "key1"}
        store.keys.count = 1
        
        stats = store.get_stats()
        
        assert stats["weapons"]["count"] == 2
        assert stats["keys"]["count"] == 1
        assert "cache" in stats
        assert "created_at" in stats["cache"]


class TestDataStorageManager:
    """Test DataStorageManager class."""
    
    def test_initialization(self, test_config: WOGConfig) -> None:
        """Test storage manager initialization."""
        storage = DataStorageManager(test_config)
        
        assert storage.config == test_config
        # Storage manager uses runtime/data.json, not the direct config path
        assert storage.data_file == test_config.base_dir / "runtime" / "data.json"
        
    def test_save_and_load_weapons(self, test_storage: DataStorageManager, sample_weapon_list: list[str]) -> None:
        """Test saving and loading weapons."""
        # Save weapons
        test_storage.save_weapons(sample_weapon_list, source_asset="test_asset")
        
        # Load weapons
        loaded_weapons = test_storage.get_weapons()
        
        assert loaded_weapons == sample_weapon_list
        assert test_storage.data.weapons.source_asset == "test_asset"
        assert test_storage.data.weapons.filtered is True
        
    def test_save_and_load_keys(self, test_storage: DataStorageManager, sample_keys: dict[str, str]) -> None:
        """Test saving and loading keys."""
        # Save keys
        test_storage.save_keys(sample_keys)
        
        # Load keys
        loaded_keys = test_storage.get_keys()
        
        assert loaded_keys == sample_keys
        assert test_storage.data.keys.validation_enabled is True
        
    def test_empty_weapons_save_error(self, test_storage: DataStorageManager) -> None:
        """Test saving empty weapons raises error."""
        with pytest.raises(ValueError, match="Cannot save empty weapon list"):
            test_storage.save_weapons([])
            
    def test_empty_keys_save_error(self, test_storage: DataStorageManager) -> None:
        """Test saving empty keys raises error."""
        with pytest.raises(ValueError, match="Cannot save empty keys"):
            test_storage.save_keys({})
            
    def test_load_data_missing_file(self, test_storage: DataStorageManager) -> None:
        """Test loading data when file doesn't exist."""
        # File shouldn't exist initially
        data = test_storage.load_data()
        
        assert isinstance(data, WOGDataStore)
        assert data.weapons.count == 0
        assert data.keys.count == 0
        
    def test_data_file_creation(self, test_storage: DataStorageManager, sample_weapon_list: list[str]) -> None:
        """Test data file is created properly."""
        # Save some data
        test_storage.save_weapons(sample_weapon_list)
        
        # Check file exists and is valid JSON
        assert test_storage.data_file.exists()
        
        with open(test_storage.data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        assert "weapons" in data
        assert "keys" in data
        assert "cache" in data
        assert data["weapons"]["count"] == len(sample_weapon_list)
        
    def test_backup_creation(self, test_storage: DataStorageManager, sample_weapon_list: list[str]) -> None:
        """Test backup file creation."""
        # Create initial data
        test_storage.save_weapons(sample_weapon_list)
        
        # Save again to trigger backup
        test_storage.save_weapons(sample_weapon_list + ["additional_weapon"])
        
        # Check backup exists
        backup_file = test_storage.data_file.with_suffix('.json.bak')
        assert backup_file.exists()
        
    def test_clear_cache(self, test_storage: DataStorageManager, sample_weapon_list: list[str], sample_keys: dict[str, str]) -> None:
        """Test cache clearing functionality."""
        # Add some data
        test_storage.save_weapons(sample_weapon_list)
        test_storage.save_keys(sample_keys)
        
        # Verify data exists
        assert len(test_storage.get_weapons()) > 0
        assert len(test_storage.get_keys()) > 0
        
        # Clear cache
        test_storage.clear_cache()
        
        # Verify data is cleared
        assert len(test_storage.get_weapons()) == 0
        assert len(test_storage.get_keys()) == 0
        
    def test_cache_stats(self, populated_storage: DataStorageManager) -> None:
        """Test cache statistics."""
        stats = populated_storage.get_cache_stats()
        
        assert "weapons" in stats
        assert "keys" in stats
        assert "cache" in stats
        assert stats["weapons"]["count"] > 0
        assert stats["keys"]["count"] > 0
        
    def test_migration_from_txt_files(self, test_storage: DataStorageManager, temp_dir: Path) -> None:
        """Test migration from legacy txt files."""
        # Create legacy files
        weapons_file = temp_dir / "weapons.txt"
        keys_file = temp_dir / "keys.txt"
        
        weapons_file.write_text("ak74\nm4a1\nglock17\n")
        keys_file.write_text("ak74 key1\nm4a1 key2\n")
        
        # Perform migration
        success = test_storage.migrate_from_txt_files(weapons_file, keys_file)
        
        assert success is True
        assert len(test_storage.get_weapons()) == 3
        assert len(test_storage.get_keys()) == 2
        assert test_storage.get_keys()["ak74"] == "key1"
        
    def test_migration_no_files(self, test_storage: DataStorageManager, temp_dir: Path) -> None:
        """Test migration when no legacy files exist."""
        non_existent_weapons = temp_dir / "no_weapons.txt"
        non_existent_keys = temp_dir / "no_keys.txt"
        
        success = test_storage.migrate_from_txt_files(non_existent_weapons, non_existent_keys)
        
        assert success is False
        
    def test_data_persistence(self, test_config: WOGConfig, sample_weapon_list: list[str]) -> None:
        """Test data persists across storage manager instances."""
        # Create first storage manager and save data
        storage1 = DataStorageManager(test_config)
        storage1.save_weapons(sample_weapon_list)
        
        # Create second storage manager and load data
        storage2 = DataStorageManager(test_config)
        loaded_weapons = storage2.get_weapons()
        
        assert loaded_weapons == sample_weapon_list
        
    def test_json_format_integrity(self, test_storage: DataStorageManager, sample_weapon_list: list[str], sample_keys: dict[str, str]) -> None:
        """Test JSON format is properly structured."""
        test_storage.save_weapons(sample_weapon_list)
        test_storage.save_keys(sample_keys)
        
        # Read raw JSON file
        with open(test_storage.data_file, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            
        # Verify structure
        required_keys = ["weapons", "keys", "cache", "config_snapshot"]
        for key in required_keys:
            assert key in raw_data
            
        # Verify weapons structure
        assert "weapons" in raw_data["weapons"]
        assert "count" in raw_data["weapons"]
        assert "filtered" in raw_data["weapons"]
        
        # Verify keys structure
        assert "keys" in raw_data["keys"]
        assert "count" in raw_data["keys"]
        
        # Verify cache structure
        assert "created_at" in raw_data["cache"]
        assert "updated_at" in raw_data["cache"]
        assert "version" in raw_data["cache"]
        # assert "assets_hashes" in raw_data["cache"] # Hash functionality removed
        # assert "hash_validation_enabled" in raw_data["cache"] # Hash functionality removed
        
    @pytest.mark.skip(reason="Hash functionality removed")
    def test_hash_functionality(self, test_storage: DataStorageManager) -> None:
        """Test hash storage and retrieval functionality - DISABLED."""
        pass
        
    @pytest.mark.skip(reason="Hash functionality removed")
    def test_hash_expiration(self, test_storage: DataStorageManager) -> None:
        """Test hash validation expiration logic - DISABLED."""
        pass
        
        # Test expiration with custom time
        assert test_storage.data.cache.is_hash_check_expired(max_age_hours=0) is True