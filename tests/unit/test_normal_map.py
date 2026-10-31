"""Unit tests for normal map converter."""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from wog_dump.utils.normal_map import (
    NormalMapConverter,
    NormalMapError,
    is_unity_swizzled_normal,
    unswizzle_normal_map,
)


def make_swizzled_image(size: int = 32) -> Image.Image:
    """Synthesize an image with the Unity swizzled normal map signature:
    red forced white, X in alpha, Y in green, both forming unit normals."""
    img = Image.new("RGBA", (size, size))
    img.putdata(
        [
            (
                255,
                round(127.5 * (1 + 0.9 * math.cos((x * 7 + y * 3) * 0.35))),
                0,
                round(127.5 * (1 + 0.9 * math.sin((x * 7 + y * 3) * 0.35))),
            )
            for y in range(size)
            for x in range(size)
        ]
    )
    return img


class TestSwizzleHelpers:
    """Test the shared detection/unswizzle helpers."""

    def test_detection_positive(self) -> None:
        """Swizzled signature: constant white red, data in green and alpha."""
        assert is_unity_swizzled_normal(make_swizzled_image()) is True

    def test_detection_rejects_albedo(self) -> None:
        img = Image.new("RGBA", (32, 32))
        img.putdata(
            [
                ((x * 7) % 256, (y * 5) % 256, 30, ((x + y) * 3) % 256)
                for y in range(32)
                for x in range(32)
            ]
        )
        assert is_unity_swizzled_normal(img) is False

    def test_detection_rejects_images_without_alpha(self) -> None:
        assert (
            is_unity_swizzled_normal(Image.new("RGB", (8, 8), (128, 128, 255))) is False
        )

    def test_detection_rejects_color_ramp(self) -> None:
        """Constant-red data that is not a normal map (e.g. the game's 1D
        heat/glow ramps) must not be unswizzled: channel pairs fall far
        outside the unit circle."""
        img = Image.new("RGBA", (32, 32))
        img.putdata(
            [
                (255, 255 if (x + y) % 2 else 0, 0, 255 if x % 2 else 0)
                for y in range(32)
                for x in range(32)
            ]
        )
        assert is_unity_swizzled_normal(img) is False

    def test_unswizzle_flat_pixel(self) -> None:
        """A flat normal (X=0 in alpha, Y=0 in green) maps to (128, 128, 255)."""
        img = Image.new("RGBA", (1, 1), (255, 128, 0, 128))
        out = unswizzle_normal_map(img)

        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (128, 128, 255)

    def test_unswizzle_steep_pixel(self) -> None:
        """X = +1 with Y = 0 leaves no room for Z."""
        img = Image.new("RGBA", (1, 1), (255, 128, 0, 255))
        out = unswizzle_normal_map(img)

        assert out.getpixel((0, 0)) == (255, 128, 128)

    def test_unswizzle_moves_alpha_to_red(self) -> None:
        img = make_swizzled_image(8)
        out = unswizzle_normal_map(img)

        source = img.getpixel((2, 3))
        result = out.getpixel((2, 3))
        assert result[0] == source[3]  # X from alpha
        assert result[1] == source[1]  # Y from green


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

        # Verify channel mapping: Alpha->Red, Green->Green, Z reconstructed
        # (X=200 -> x^2 term 41, Y=100 -> y^2 term 5, sum 46 -> z=229)
        pixel = converted_img.getpixel((0, 0))
        assert pixel[0] == 200  # Red = original Alpha (X component)
        assert pixel[1] == 100  # Green = original Green (Y component)
        assert pixel[2] == 229  # Blue = reconstructed Z

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
        output_path = converter.convert_normal_map_advanced(input_path, invert_y=False)

        assert output_path.exists()

        # Check that Y channel is not inverted (Y comes from green)
        converted_img = Image.open(output_path)
        pixel = converted_img.getpixel((0, 0))
        assert pixel[0] == 200  # X from alpha
        assert pixel[1] == 100  # original green value, not inverted
        assert pixel[2] == 229  # reconstructed Z

        # Test with invert_y=True (DirectX <-> OpenGL flip)
        inverted_path = converter.convert_normal_map_advanced(input_path)
        inverted_img = Image.open(inverted_path)
        assert inverted_img.getpixel((0, 0))[1] == 255 - 100

    def test_batch_convert_directory_empty(self, temp_dir: Path) -> None:
        """Test batch conversion on empty directory."""
        converter = NormalMapConverter()

        converted_files = converter.batch_convert_directory(temp_dir)

        assert converted_files == []

    def test_batch_convert_directory_with_files(self, temp_dir: Path) -> None:
        """Test batch conversion with normal map files."""
        converter = NormalMapConverter()

        # Create test normal maps (must match the swizzled signature)
        for i in range(3):
            img_path = temp_dir / f"texture_{i}_n.png"
            make_swizzled_image(16).save(img_path)

        # Create non-normal map (content does not match, left unchanged)
        other_img = temp_dir / "texture_diffuse.png"
        Image.new("RGB", (16, 16)).save(other_img)

        # Mock the actual conversion to avoid file operations
        converter.convert_normal_map = Mock(
            side_effect=lambda src, dst=None, force=True: dst
            or src.parent / f"{src.stem}_converted{src.suffix}"
        )

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
        make_swizzled_image(16).save(img_path)

        # Mock conversion
        converter.convert_normal_map = Mock(return_value=img_path)

        # Test recursive
        converted_files = converter.batch_convert_directory(temp_dir, recursive=True)
        assert len(converted_files) == 1

        # Test non-recursive
        converter.convert_normal_map.reset_mock()
        converted_files = converter.batch_convert_directory(temp_dir, recursive=False)
        assert len(converted_files) == 0

    def test_batch_convert_skips_already_converted(self, temp_dir: Path) -> None:
        """Re-running batch conversion is idempotent: converted files no
        longer match the swizzled signature and are left alone."""
        converter = NormalMapConverter()

        img_path = temp_dir / "texture_n.png"
        make_swizzled_image(16).save(img_path)

        first_run = converter.batch_convert_directory(temp_dir)
        assert first_run == [img_path]
        assert Image.open(img_path).mode == "RGB"

        second_run = converter.batch_convert_directory(temp_dir)
        assert second_run == []

    def test_validate_normal_map_unity_format(self, temp_dir: Path) -> None:
        """Test validation of Unity format normal map."""
        converter = NormalMapConverter()

        # Create Unity format normal map (X in alpha, Y in green, red forced white)
        input_path = temp_dir / "unity_normal.png"
        make_swizzled_image(32).save(input_path)

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
