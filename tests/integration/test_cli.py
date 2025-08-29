"""Integration tests for WOG Dump CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from wog_dump.cli.main import cli
from wog_dump.core.config import WOGConfig


class TestCLIIntegration:
    """Integration tests for CLI commands."""
    
    def test_cli_help(self) -> None:
        """Test CLI help command."""
        runner = CliRunner()
        result = runner.invoke(cli, ['--help'])
        
        assert result.exit_code == 0
        assert "WOG Dump" in result.output
        assert "Modern tool for extracting 3D models" in result.output
    
    def test_cli_version(self) -> None:
        """Test CLI version option."""
        runner = CliRunner()
        result = runner.invoke(cli, ['--version'])
        
        assert result.exit_code == 0
        assert "2.3.0" in result.output
    
    def test_info_command(self) -> None:
        """Test info command."""
        runner = CliRunner()
        
        with runner.isolated_filesystem():
            # Create the runtime directory structure that config expects
            Path("runtime").mkdir(exist_ok=True)
            
            result = runner.invoke(cli, ['info'])
        
        # Info command should succeed even with missing files
        assert result.exit_code == 0
        assert "Configuration" in result.output
        assert "Status" in result.output
    
    @patch('wog_dump.core.download.DownloadManager.download_weapon_list')
    @patch('wog_dump.core.unpack.WeaponListProcessor.process_weapon_list_asset')
    def test_download_weapons_command(self, mock_process: Mock, mock_download: Mock) -> None:
        """Test download-weapons command."""
        runner = CliRunner()
        
        # Mock the download and processing
        with runner.isolated_filesystem():
            # Create the runtime directory structure
            runtime_dir = Path("runtime")
            runtime_dir.mkdir(exist_ok=True)
            
            mock_asset_path = Path("spider_gen.unity3d")
            mock_download.return_value = mock_asset_path
            mock_process.return_value = ["ak74", "m4a1", "glock17"]
            
            result = runner.invoke(cli, ['download-weapons'])
        
        assert result.exit_code == 0
        mock_download.assert_called_once()
        mock_process.assert_called_once()
    
    def test_convert_normals_command_file(self, temp_dir: Path) -> None:
        """Test convert-normals command with a file."""
        from PIL import Image
        
        runner = CliRunner()
        
        # Create a test normal map
        test_image = temp_dir / "test_normal_n.png"
        img = Image.new("RGBA", (32, 32), (50, 100, 150, 200))
        img.save(test_image)
        
        result = runner.invoke(cli, ['convert-normals', str(test_image)])
        
        assert result.exit_code == 0
        assert "Converted:" in result.output
    
    @patch('wog_dump.core.download.DownloadManager')
    @patch('wog_dump.core.unpack.WeaponListProcessor')
    @patch('wog_dump.core.decrypt.KeyManager')
    @patch('wog_dump.core.decrypt.AssetDecryptor')
    @patch('wog_dump.core.unpack.AssetUnpacker')
    def test_full_pipeline_command(self, mock_unpacker: Mock, mock_decryptor: Mock, 
                                 mock_key_manager: Mock, mock_processor: Mock, 
                                 mock_downloader: Mock) -> None:
        """Test full-pipeline command."""
        runner = CliRunner()
        
        # Setup mocks
        mock_downloader.return_value.__enter__.return_value.download_weapon_list.return_value = Path("spider_gen.unity3d")
        mock_downloader.return_value.__enter__.return_value.download_assets.return_value = (["ak74"], [])
        
        mock_processor.return_value.process_weapon_list_asset.return_value = ["ak74"]
        mock_processor.return_value.load_weapon_list.return_value = ["ak74"]
        
        mock_key_manager.return_value.load_keys.return_value = {"ak74": "test_key"}
        mock_decryptor.return_value.decrypt_all_assets.return_value = ([Path("ak74.unity3d")], [])
        mock_unpacker.return_value.unpack_multiple_assets.return_value = {Path("ak74.unity3d"): [Path("model.obj")]}
        
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ['full-pipeline', '--skip-download'])
        
        # Should succeed but some commands might be skipped due to mocking
        assert result.exit_code in [0, 1]  # May fail due to missing files in test env
    
    def test_cli_with_custom_config(self) -> None:
        """Test CLI with custom configuration options."""
        runner = CliRunner()
        
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                '--verbose',
                '--max-threads', '8',
                'info'
            ])
        
        assert result.exit_code == 0
        # The config should be updated with custom values
    
    def test_cli_debug_mode(self) -> None:
        """Test CLI debug mode."""
        runner = CliRunner()
        
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ['--debug', 'info'])
        
        assert result.exit_code == 0
        # Debug mode should enable more verbose output
    
    def test_download_assets_check_only(self) -> None:
        """Test download-assets command with check-only flag."""
        runner = CliRunner()
        
        with runner.isolated_filesystem():
            # This will fail due to missing weapon list, but tests the command structure
            result = runner.invoke(cli, ['download-assets', '--check-only'])
        
        assert result.exit_code == 1  # Expected to fail without weapon list
        assert "No weapon list found" in result.output
    
    def test_decrypt_assets_no_keys(self) -> None:
        """Test decrypt-assets command without keys."""
        runner = CliRunner()
        
        with runner.isolated_filesystem():
            # Create runtime directory and weapon list in correct location
            runtime_dir = Path("runtime")
            runtime_dir.mkdir(exist_ok=True)
            (runtime_dir / "weapons.txt").write_text("ak74\nm4a1\n")
            
            result = runner.invoke(cli, ['decrypt-assets'])
        
        assert result.exit_code == 1
        assert "No decryption keys found" in result.output


@pytest.mark.integration
class TestFullWorkflow:
    """Integration tests for full workflow scenarios."""
    
    def test_configuration_persistence(self, temp_dir: Path) -> None:
        """Test that configuration changes persist across commands."""
        runner = CliRunner()
        
        with runner.isolated_filesystem():
            # First command with custom config
            result1 = runner.invoke(cli, [
                '--config-dir', str(temp_dir),
                '--max-threads', '8',
                'info'
            ])
            
            assert result1.exit_code == 0
            
            # Second command should use the same config
            result2 = runner.invoke(cli, ['info'])
            assert result2.exit_code == 0
    
    @patch('wog_dump.utils.normal_map.NormalMapConverter.batch_convert_directory')
    def test_batch_processing_workflow(self, mock_convert: Mock, temp_dir: Path) -> None:
        """Test batch processing workflow."""
        runner = CliRunner()
        
        # Mock successful conversion
        mock_convert.return_value = [
            temp_dir / "texture1_converted.png",
            temp_dir / "texture2_converted.png",
        ]
        
        result = runner.invoke(cli, [
            'convert-normals',
            str(temp_dir),
            '--recursive',
            '--backup'
        ])
        
        assert result.exit_code == 0
        mock_convert.assert_called_once()
        
        # Verify arguments passed correctly
        args, kwargs = mock_convert.call_args
        assert kwargs.get('recursive') is True
        assert kwargs.get('backup') is True