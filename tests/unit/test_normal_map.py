"""Unit tests for normal map converter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from wog_dump.utils.normal_map import NormalMapConverter, NormalMapError


class TestNormalMapConverter:
    """Test NormalMapConverter class."""
    
    def test_init(self) -> None:
        """Test NormalMapConverter initialization."""
        converter = NormalMapConverter()
        assert converter.logger is not None
    
    def test_is_normal_map_positive(self) -> None:
        """Test normal map detection with positive cases."""
        converter = NormalMapConverter()
        
        positive_cases = [
            Path("texture_n.png"),
            Path("material_normal.jpg"),
            Path("surface_nrm.tga"),
            Path("mesh_norm.bmp"),
            Path("TEXTURE_N.PNG"),  # Case insensitive
        ]
        
        for case in positive_cases:
            assert converter.is_normal_map(case) is True
    
    def test_is_normal_map_negative(self) -> None:
        """Test normal map detection with negative cases."""
        converter = NormalMapConverter()
        
        negative_cases = [
            Path("texture_diffuse.png"),
            Path("material_albedo.jpg"),
            Path("surface_roughness.tga"),
            Path("mesh.bmp"),
            Path("normal_map.txt"),  # Wrong extension context
        ]
        
        for case in negative_cases:
            assert converter.is_normal_map(case) is False
    
    def test_convert_normal_map_success(self, temp_dir: Path) -> None:
        """Test successful normal map conversion."""
        converter = NormalMapConverter()
        
        # Create a test image (RGBA)
        input_path = temp_dir / "test_normal_n.png"
        
        # Create test image with specific channel data
        img = Image.new("RGBA", (64, 64))
        # Fill with test data: R=50, G=100, B=150, A=200
        pixels = [(50, 100, 150, 200) for _ in range(64 * 64)]
        img.putdata(pixels)
        img.save(input_path)
        
        # Convert
        output_path = converter.convert_normal_map(input_path)
        
        # Verify output
        assert output_path.exists()
        assert output_path != input_path
        
        # Check converted image
        converted_img = Image.open(output_path)
        assert converted_img.mode == "RGB"
        
        # Verify channel mapping: Alpha->Red, Blue->Green, 255->Blue
        pixel = converted_img.getpixel((0, 0))
        assert pixel[0] == 200  # Red = original Alpha
        assert pixel[1] == 150  # Green = original Blue
        assert pixel[2] == 255  # Blue = maximum
    
    def test_convert_normal_map_with_output_path(self, temp_dir: Path) -> None:
        """Test normal map conversion with custom output path."""
        converter = NormalMapConverter()
        
        # Create test image
        input_path = temp_dir / "input_normal.png"
        output_path = temp_dir / "custom_output.png"
        
        img = Image.new("RGBA", (32, 32), (128, 128, 128, 255))
        img.save(input_path)
        
        # Convert with custom output
        result_path = converter.convert_normal_map(input_path, output_path)
        
        assert result_path == output_path
        assert output_path.exists()
    
    def test_convert_normal_map_non_rgba(self, temp_dir: Path) -> None:
        """Test converting non-RGBA image."""
        converter = NormalMapConverter()
        
        # Create RGB image
        input_path = temp_dir / "test_rgb.png"
        img = Image.new("RGB", (32, 32), (100, 150, 200))
        img.save(input_path)
        
        # Should still work by converting to RGBA first
        output_path = converter.convert_normal_map(input_path)
        
        assert output_path.exists()
        converted_img = Image.open(output_path)
        assert converted_img.mode == "RGB"
    
    def test_convert_normal_map_failure(self, temp_dir: Path) -> None:
        """Test normal map conversion failure."""
        converter = NormalMapConverter()
        
        # Try to convert non-existent file
        input_path = temp_dir / "nonexistent.png"
        
        with pytest.raises(NormalMapError):
            converter.convert_normal_map(input_path)
    
    def test_convert_normal_map_advanced(self, temp_dir: Path) -> None:
        """Test advanced normal map conversion with options."""
        converter = NormalMapConverter()
        
        # Create test image
        input_path = temp_dir / "test_advanced.png"
        img = Image.new("RGBA", (32, 32), (50, 100, 150, 200))
        img.save(input_path)
        
        # Test with invert_y=False
        output_path = converter.convert_normal_map_advanced(
            input_path, invert_y=False, calculate_z=False
        )
        
        assert output_path.exists()
        
        # Check that Y channel is not inverted
        converted_img = Image.open(output_path)
        pixel = converted_img.getpixel((0, 0))
        assert pixel[1] == 150  # Should be original blue value, not inverted
    
    def test_batch_convert_directory_empty(self, temp_dir: Path) -> None:
        """Test batch conversion on empty directory."""
        converter = NormalMapConverter()
        
        converted_files = converter.batch_convert_directory(temp_dir)
        
        assert converted_files == []
    
    def test_batch_convert_directory_with_files(self, temp_dir: Path) -> None:
        """Test batch conversion with normal map files."""
        converter = NormalMapConverter()
        
        # Create test normal maps
        for i in range(3):
            img_path = temp_dir / f"texture_{i}_n.png"
            img = Image.new("RGBA", (16, 16), (i * 50, 100, 150, 200))
            img.save(img_path)
        
        # Create non-normal map (should be ignored)
        other_img = temp_dir / "texture_diffuse.png"
        Image.new("RGB", (16, 16)).save(other_img)
        
        # Mock the actual conversion to avoid file operations
        converter.convert_normal_map = Mock(side_effect=lambda src, dst=None: dst or src.parent / f"{src.stem}_converted{src.suffix}")
        
        converted_files = converter.batch_convert_directory(temp_dir)
        
        assert len(converted_files) == 3
        assert converter.convert_normal_map.call_count == 3
    
    def test_batch_convert_recursive(self, temp_dir: Path) -> None:
        """Test recursive batch conversion."""
        converter = NormalMapConverter()
        
        # Create subdirectory with normal map
        subdir = temp_dir / "subdir"
        subdir.mkdir()
        
        img_path = subdir / "texture_n.png"
        img = Image.new("RGBA", (16, 16))
        img.save(img_path)
        
        # Mock conversion
        converter.convert_normal_map = Mock(return_value=img_path)
        
        # Test recursive
        converted_files = converter.batch_convert_directory(temp_dir, recursive=True)
        assert len(converted_files) == 1
        
        # Test non-recursive
        converter.convert_normal_map.reset_mock()
        converted_files = converter.batch_convert_directory(temp_dir, recursive=False)
        assert len(converted_files) == 0
    
    def test_validate_normal_map_unity_format(self, temp_dir: Path) -> None:
        """Test validation of Unity format normal map."""
        converter = NormalMapConverter()
        
        # Create Unity format normal map (data in green and alpha)
        input_path = temp_dir / "unity_normal.png"
        img = Image.new("RGBA", (32, 32))
        
        # Unity format: G and A channels have data, R and B minimal
        pixels = [(10, 120, 20, 180) for _ in range(32 * 32)]
        img.putdata(pixels)
        img.save(input_path)
        
        result = converter.validate_normal_map(input_path)
        
        assert result["is_valid"] is True
        assert result["format"] == "unity"
        assert result["mode"] == "RGBA"
        assert "channels" in result
    
    def test_validate_normal_map_standard_format(self, temp_dir: Path) -> None:
        """Test validation of standard format normal map."""
        converter = NormalMapConverter()
        
        # Create standard format normal map (data in red and green)
        input_path = temp_dir / "standard_normal.png"
        img = Image.new("RGBA", (32, 32))
        
        # Standard format: R and G channels have data
        pixels = [(120, 150, 20, 10) for _ in range(32 * 32)]
        img.putdata(pixels)
        img.save(input_path)
        
        result = converter.validate_normal_map(input_path)
        
        assert result["is_valid"] is True
        assert result["format"] == "standard"
    
    def test_validate_normal_map_invalid(self, temp_dir: Path) -> None:
        """Test validation of invalid normal map."""
        converter = NormalMapConverter()
        
        # Create image with wrong mode
        input_path = temp_dir / "invalid.png"
        img = Image.new("L", (32, 32), 128)  # Grayscale
        img.save(input_path)
        
        result = converter.validate_normal_map(input_path)
        
        assert result["is_valid"] is False
        assert "Unsupported mode" in result["issues"][0]
    
    def test_validate_normal_map_nonexistent(self, temp_dir: Path) -> None:
        """Test validation of non-existent file."""
        converter = NormalMapConverter()
        
        input_path = temp_dir / "nonexistent.png"
        result = converter.validate_normal_map(input_path)
        
        assert result["is_valid"] is False
        assert len(result["issues"]) > 0