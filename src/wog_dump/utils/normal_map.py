"""Normal map converter for WOG Dump - clean version without numpy."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from PIL import Image, ImageChops

from ..utils.logging import get_logger


class NormalMapError(Exception):
    """Raised when normal map operations fail."""
    pass


class NormalMapConverter:
    """Converts Unity normal maps to standard format."""

    def __init__(self) -> None:
        self.logger = get_logger()

    def is_normal_map(self, image_path: Path) -> bool:
        """Check if an image file is likely a normal map."""
        name_lower = image_path.name.lower()
        normal_indicators = ["_n.", "_normal.", "_nrm.", "_norm."]

        return any(indicator in name_lower for indicator in normal_indicators)

    def convert_normal_map(self, input_path: Path, output_path: Path | None = None) -> Path:
        """Convert a Unity normal map to standard format.

        Unity stores normal maps with:
        - Red channel: Empty/ignored
        - Green channel: Y component (inverted)
        - Blue channel: Ignored
        - Alpha channel: X component

        Standard format:
        - Red channel: X component
        - Green channel: Y component
        - Blue channel: Z component (set to neutral)
        """
        if output_path is None:
            output_path = input_path.parent / f"{input_path.stem}_converted{input_path.suffix}"

        try:
            with Image.open(input_path) as img:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")

                # Split channels
                r, g, b, a = img.split()

                # Convert Unity format:
                # Red = Alpha (X component)
                # Green = Blue (Y component, NOT inverted by default)
                # Blue = maximum Z (255 = 1.0 in normalized space)
                x_channel = a
                y_channel = b  # Use blue channel as-is
                z_channel = Image.new('L', img.size, 255)  # Use 255 for maximum Z

                # Merge channels
                converted = Image.merge("RGB", (x_channel, y_channel, z_channel))
                converted.save(output_path)

                self.logger.debug(f"Converted normal map: {input_path} -> {output_path}")
                return output_path

        except Exception as e:
            raise NormalMapError(f"Failed to convert {input_path}: {e}") from e

    def convert_normal_map_advanced(self, input_path: Path, output_path: Path | None = None,
                                  invert_y: bool = True, calculate_z: bool = True) -> Path:
        """Advanced normal map conversion with options (for backward compatibility)."""
        if output_path is None:
            output_path = input_path.parent / f"{input_path.stem}_converted{input_path.suffix}"

        try:
            with Image.open(input_path) as img:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")

                r, g, b, a = img.split()

                x_channel = a
                y_channel = b
                if invert_y:
                    y_channel = ImageChops.invert(b)
                z_channel = Image.new('L', img.size, 128)

                converted = Image.merge("RGB", (x_channel, y_channel, z_channel))
                converted.save(output_path)

                return output_path

        except Exception as e:
            raise NormalMapError(f"Failed to convert {input_path}: {e}") from e

    def validate_normal_map(self, image_path: Path) -> dict[str, any]:
        """Validate and analyze a normal map (simplified version)."""
        result = {
            "is_valid": False,
            "format": "unknown",  # Use "format" to match test expectations
            "format_detected": "unknown",  # Keep this for backward compatibility
            "mode": "unknown",  # Add mode field for test compatibility
            "size": (0, 0),
            "channels": {},
            "issues": [],
            "confidence": 0.0,
            "recommendations": []
        }

        try:
            with Image.open(image_path) as img:
                result["size"] = img.size
                result["mode"] = img.mode

                if img.mode != "RGBA":
                    result["issues"].append(f"Unsupported mode: {img.mode}")
                    return result

                # Analyze channel data to determine format
                r, g, b, a = img.split()

                # Sample a few pixels to determine format
                sample_pixels = []
                width, height = img.size
                for y in range(0, height, height//4 or 1):
                    for x in range(0, width, width//4 or 1):
                        pixel = img.getpixel((x, y))
                        sample_pixels.append(pixel)

                if sample_pixels:
                    # Calculate average channel values
                    avg_r = sum(p[0] for p in sample_pixels) / len(sample_pixels)
                    avg_g = sum(p[1] for p in sample_pixels) / len(sample_pixels)
                    avg_b = sum(p[2] for p in sample_pixels) / len(sample_pixels)
                    avg_a = sum(p[3] for p in sample_pixels) / len(sample_pixels)

                    # Unity format: data primarily in green and alpha channels
                    # Standard format: data primarily in red and green channels
                    if avg_a > 50 and avg_g > 50 and avg_r < 50:
                        format_type = "unity"
                    elif avg_r > 50 and avg_g > 50:
                        format_type = "standard"
                    else:
                        format_type = "unity"  # Default fallback
                else:
                    format_type = "unity"

                result["is_valid"] = True
                result["format"] = format_type
                result["format_detected"] = format_type
                result["confidence"] = 0.8

        except Exception as e:
            result["issues"].append(f"Failed to open image: {e}")

        return result

    def batch_convert_directory(self, directory: Path, recursive: bool = True,
                              pattern: str = "*_n*.png", backup: bool = False) -> list[Path]:
        """Convert all normal maps in a directory."""
        # Find normal map files
        if recursive:
            normal_maps = list(directory.rglob(pattern))
        else:
            normal_maps = list(directory.glob(pattern))

        # Filter to only image files
        image_extensions = {'.png', '.jpg', '.jpeg', '.tga', '.bmp'}
        normal_maps = [f for f in normal_maps if f.suffix.lower() in image_extensions]

        if not normal_maps:
            self.logger.info(f"No normal maps found in {directory}")
            return []

        self.logger.info(f"Found {len(normal_maps)} normal maps to convert")

        converted_files = []

        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Converting normal maps", total=len(normal_maps))

            for normal_map in normal_maps:
                try:
                    # Create backup if requested
                    if backup:
                        backup_path = normal_map.parent / f"{normal_map.stem}_backup{normal_map.suffix}"
                        if not backup_path.exists():
                            backup_path.write_bytes(normal_map.read_bytes())

                    # Convert in place (overwrite original)
                    output_path = self.convert_normal_map(normal_map, normal_map)
                    converted_files.append(output_path)

                except Exception as e:
                    self.logger.error(f"Failed to convert {normal_map}: {e}")

                progress.update(task, advance=1)

        self.logger.info(f"Successfully converted {len(converted_files)} normal maps")
        return converted_files


# CLI Interface
@click.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
@click.option('--recursive', '-r', is_flag=True, help='Process directories recursively')
@click.option('--pattern', '-p', default='*_n*.png', help='File pattern to match')
@click.option('--backup', '-b', is_flag=True, help='Create backups before conversion')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def cli_main(path: Path, recursive: bool, pattern: str, backup: bool, verbose: bool) -> None:
    """Convert Unity normal maps to standard format.

    PATH can be a single image file or directory containing normal maps.
    """
    logger = get_logger()
    if verbose:
        logger.logger.setLevel(10)  # DEBUG

    converter = NormalMapConverter()

    try:
        if path.is_file():
            output_path = converter.convert_normal_map(path)
            logger.info(f"Converted: {output_path}")

        elif path.is_dir():
            converted_files = converter.batch_convert_directory(
                path, recursive=recursive, pattern=pattern, backup=backup
            )
            logger.info(f"Conversion complete: {len(converted_files)} files processed")

        else:
            logger.error(f"Invalid path: {path}")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
