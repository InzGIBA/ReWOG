"""Unit tests for decrypt module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from wog_dump.core.config import WOGConfig
from wog_dump.core.decrypt import AssetDecryptor, DecryptionError, KeyManager
from wog_dump.core.storage import DataStorageManager


class TestKeyManager:
    """Test KeyManager class."""
    
    def test_init(self, test_config: WOGConfig) -> None:
        """Test KeyManager initialization."""
        manager = KeyManager(test_config)
        assert manager.config == test_config
        assert manager.session is not None
    
    def test_save_and_load_keys(self, test_config: WOGConfig, sample_keys: dict[str, str]) -> None:
        """Test saving and loading keys with JSON storage."""
        manager = KeyManager(test_config)
        
        # Save keys
        manager.save_keys(sample_keys)
        
        # Load keys
        loaded_keys = manager.load_keys()
        
        assert loaded_keys == sample_keys
        
        # Verify JSON storage
        storage = DataStorageManager(test_config)
        storage_keys = storage.get_keys()
        assert storage_keys == sample_keys
    
    def test_load_keys_missing_file(self, test_config: WOGConfig) -> None:
        """Test loading keys when no data exists."""
        manager = KeyManager(test_config)
        keys = manager.load_keys()
        assert keys == {}
    
    def test_save_keys_invalid_path(self, test_config: WOGConfig) -> None:
        """Test saving keys with invalid path."""
        # Set base_dir to a directory that doesn't exist to force storage error
        test_config.base_dir = Path("/nonexistent/path")
        manager = KeyManager(test_config)
        
        with pytest.raises(DecryptionError):
            manager.save_keys({"test": "key"})


class TestAssetDecryptor:
    """Test AssetDecryptor class."""
    
    def test_init(self, test_config: WOGConfig) -> None:
        """Test AssetDecryptor initialization."""
        decryptor = AssetDecryptor(test_config)
        assert decryptor.config == test_config
    
    def test_generate_decryption_key(self, test_config: WOGConfig) -> None:
        """Test decryption key generation."""
        decryptor = AssetDecryptor(test_config)
        
        base_key = "test_key"
        generated_key = decryptor.generate_decryption_key(base_key)
        
        # Should be MD5 hash (32 hex characters)
        assert len(generated_key) == 32
        assert all(c in "0123456789abcdef" for c in generated_key)
        
        # Same input should produce same output
        assert decryptor.generate_decryption_key(base_key) == generated_key
    
    def test_decrypt_with_python(self, test_config: WOGConfig) -> None:
        """Test Python XOR decryption."""
        decryptor = AssetDecryptor(test_config)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create test input file
            input_path = temp_path / "input.bin"
            test_data = b"Hello, World!"
            input_path.write_bytes(test_data)
            
            # Test encryption/decryption
            key = "test_key"
            encrypted_path = temp_path / "encrypted.bin"
            decrypted_path = temp_path / "decrypted.bin"
            
            # Encrypt
            success = decryptor.decrypt_with_python(input_path, key, encrypted_path)
            assert success is True
            assert encrypted_path.exists()
            
            # Decrypt (XOR is reversible)
            success = decryptor.decrypt_with_python(encrypted_path, key, decrypted_path)
            assert success is True
            assert decrypted_path.exists()
            
            # Should match original data
            assert decrypted_path.read_bytes() == test_data
    
    def test_decrypt_asset_with_bytes_m_script(self, test_config: WOGConfig) -> None:
        """Test decrypting asset when m_Script is bytes."""
        # Mock UnityPy objects
        mock_data = Mock()
        mock_data.m_Name = "test_asset"
        mock_data.m_Script = b"test binary data"
        
        mock_obj = Mock()
        mock_obj.type.name = "TextAsset"
        mock_obj.read.return_value = mock_data
        
        mock_env = Mock()
        mock_env.objects = [mock_obj]
        
        with patch('wog_dump.core.decrypt.UnityPy.load') as mock_load:
            mock_load.return_value = mock_env
            
            decryptor = AssetDecryptor(test_config)
            
            # Mock the decrypt_with_python method to avoid actual file operations
            with patch.object(decryptor, 'decrypt_with_python', return_value=True):
                result = decryptor.decrypt_asset(Path("test.unity3d"), "test_key")
                
                # Should successfully process the asset
                assert len(result) == 1
                assert result[0].name == "test_asset.unity3d"
    
    def test_decrypt_asset_with_string_m_script(self, test_config: WOGConfig) -> None:
        """Test decrypting asset when m_Script is string - this tests the type handling fix."""
        # Mock UnityPy objects
        mock_data = Mock()
        mock_data.m_Name = "test_asset_string"
        mock_data.m_Script = "test string data"  # String instead of bytes
        
        mock_obj = Mock()
        mock_obj.type.name = "TextAsset"
        mock_obj.read.return_value = mock_data
        
        mock_env = Mock()
        mock_env.objects = [mock_obj]
        
        with patch('wog_dump.core.decrypt.UnityPy.load') as mock_load:
            mock_load.return_value = mock_env
            
            decryptor = AssetDecryptor(test_config)
            
            # Mock the decrypt_with_python method to avoid actual file operations
            with patch.object(decryptor, 'decrypt_with_python', return_value=True):
                # This should NOT raise a "bytes-like object required" error
                result = decryptor.decrypt_asset(Path("test.unity3d"), "test_key")
                
                # Should successfully process the asset
                assert len(result) == 1
                assert result[0].name == "test_asset_string.unity3d"
    
    def test_decrypt_asset_type_handling_integration(self, test_config: WOGConfig) -> None:
        """Integration test for m_Script type handling with actual file operations."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Update config to use temp directory
            test_config.encrypted_dir = temp_path / "encrypted"
            test_config.decrypted_dir = temp_path / "decrypted"
            test_config.encrypted_dir.mkdir(exist_ok=True)
            test_config.decrypted_dir.mkdir(exist_ok=True)
            
            # Test both bytes and string types
            test_cases = [
                ("bytes_asset", b"binary data content"),
                ("string_asset", "string data content"),
            ]
            
            for asset_name, script_data in test_cases:
                # Mock UnityPy objects
                mock_data = Mock()
                mock_data.m_Name = asset_name
                mock_data.m_Script = script_data
                
                mock_obj = Mock()
                mock_obj.type.name = "TextAsset"
                mock_obj.read.return_value = mock_data
                
                mock_env = Mock()
                mock_env.objects = [mock_obj]
                
                with patch('wog_dump.core.decrypt.UnityPy.load') as mock_load:
                    mock_load.return_value = mock_env
                    
                    decryptor = AssetDecryptor(test_config)
                    
                    # This should work for both bytes and string without errors
                    result = decryptor.decrypt_asset(Path("test.unity3d"), "test_key")
                    
                    # Verify the encrypted file was written correctly
                    encrypted_file = test_config.encrypted_dir / f"{asset_name}.bytes"
                    assert encrypted_file.exists()
                    
                    # Verify file contents based on type
                    if isinstance(script_data, bytes):
                        assert encrypted_file.read_bytes() == script_data
                    else:
                        assert encrypted_file.read_bytes() == script_data.encode('utf-8')
    
    def test_decrypt_asset_with_surrogate_string_m_script(self, test_config: WOGConfig) -> None:
        """Test decrypting asset when m_Script contains surrogates that can't be encoded to UTF-8."""
        # Mock UnityPy objects with surrogate characters
        mock_data = Mock()
        mock_data.m_Name = "test_surrogate_asset"
        # String with surrogates (like the ones causing the error)
        mock_data.m_Script = "test data\udce0\udcd9\udc8a more data"
        
        mock_obj = Mock()
        mock_obj.type.name = "TextAsset"
        mock_obj.read.return_value = mock_data
        
        mock_env = Mock()
        mock_env.objects = [mock_obj]
        
        with patch('wog_dump.core.decrypt.UnityPy.load') as mock_load:
            mock_load.return_value = mock_env
            
            decryptor = AssetDecryptor(test_config)
            
            # Mock the decrypt_with_python method to avoid actual file operations
            with patch.object(decryptor, 'decrypt_with_python', return_value=True):
                # This should NOT raise a "surrogates not allowed" error
                result = decryptor.decrypt_asset(Path("test.unity3d"), "test_key")
                
                # Should successfully process the asset despite surrogates
                assert len(result) == 1
                assert result[0].name == "test_surrogate_asset.unity3d"
    
    def test_decrypt_all_assets_with_mixed_types(self, test_config: WOGConfig) -> None:
        """Test decrypting multiple assets with mixed m_Script types."""
        # Create test asset files
        asset1_path = test_config.assets_dir / "asset1.unity3d"
        asset2_path = test_config.assets_dir / "asset2.unity3d"
        asset1_path.touch()
        asset2_path.touch()
        
        decryptor = AssetDecryptor(test_config)
        
        # Mock decrypt_asset to simulate successful decryption
        with patch.object(decryptor, 'decrypt_asset') as mock_decrypt:
            mock_decrypt.side_effect = [
                [Path("decrypted1.unity3d")],  # First asset
                [Path("decrypted2.unity3d")],  # Second asset
            ]
            
            keys = {"asset1": "key1", "asset2": "key2"}
            decrypted_files, failed_assets = decryptor.decrypt_all_assets(keys)
            
            assert len(decrypted_files) == 2
            assert len(failed_assets) == 0
            assert mock_decrypt.call_count == 2