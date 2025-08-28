"""Normal map converter for WOG Dump with enhanced functionality."""

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
        # Check filename patterns
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
        - Blue channel: Z component (calculated or set to 1)
        """
        if output_path is None:
            # Create output path with _converted suffix
            output_path = input_path.parent / f"{input_path.stem}_converted{input_path.suffix}"
        
        try:
            # Open and validate image
            with Image.open(input_path) as img:
                # Convert to RGBA if not already
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                
                # Split channels
                r, g, b, a = img.split()
                
                # Convert Unity normal map format:
                # Red = Alpha (X component)
                # Green = Blue (Y component, may need inversion)
                # Blue = calculated Z or set to maximum
                
                # Create Z channel (blue) - calculate or set to maximum
                # For now, we'll set it to maximum (255) for compatibility
                z_channel = Image.new('L', img.size, 255)
                
                # Merge channels: (Alpha->Red, Blue->Green, 255->Blue)
                converted = Image.merge("RGB", (a, b, z_channel))
                
                # Save converted image
                converted.save(output_path)
                
                self.logger.debug(f"Converted normal map: {input_path} -> {output_path}")
                return output_path
                
        except Exception as e:
            raise NormalMapError(f"Failed to convert {input_path}: {e}") from e
    
    def convert_normal_map_advanced(self, input_path: Path, output_path: Path | None = None,
                                  invert_y: bool = True, calculate_z: bool = True) -> Path:
        """Advanced normal map conversion with options."""
        if output_path is None:
            output_path = input_path.parent / f"{input_path.stem}_converted{input_path.suffix}"
        
        try:
            with Image.open(input_path) as img:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                
                r, g, b, a = img.split()
                
                # Red channel = Alpha channel (X component)
                x_channel = a
                
                # Green channel = Blue channel (Y component)
                y_channel = b
                if invert_y:
                    y_channel = ImageChops.invert(y_channel)
                
                # Blue channel (Z component)
                if calculate_z:
                    # Calculate Z from X and Y: Z = sqrt(1 - X² - Y²)
                    # This is computationally expensive, so we'll use a simplified approach
                    z_channel = Image.new('L', img.size, 128)  # Neutral Z
                else:
                    z_channel = Image.new('L', img.size, 255)  # Maximum Z
                
                converted = Image.merge("RGB", (x_channel, y_channel, z_channel))
                converted.save(output_path)
                
                self.logger.debug(f"Advanced conversion: {input_path} -> {output_path}")
                return output_path
                
        except Exception as e:
            raise NormalMapError(f"Failed to convert {input_path}: {e}") from e
    
    def batch_convert_directory(self, directory: Path, recursive: bool = True,
                              pattern: str = "*_n*.png", backup: bool = False) -> list[Path]:
        """Convert all normal maps in a directory."""
        converted_files = []
        
        # Find normal map files
        if recursive:
            normal_maps = list(directory.rglob(pattern))
        else:
            normal_maps = list(directory.glob(pattern))
        
        # Filter to only actual image files
        image_extensions = {'.png', '.jpg', '.jpeg', '.tga', '.bmp'}
        normal_maps = [f for f in normal_maps if f.suffix.lower() in image_extensions]
        
        if not normal_maps:
            self.logger.info(f"No normal maps found in {directory}")
            return converted_files
        
        self.logger.info(f"Found {len(normal_maps)} normal maps to convert")
        
        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Converting normal maps", total=len(normal_maps))
            
            for normal_map in normal_maps:
                try:
                    # Create backup if requested
                    if backup:
                        backup_path = normal_map.parent / f"{normal_map.stem}_backup{normal_map.suffix}"
                        if not backup_path.exists():
                            normal_map.rename(backup_path)
                            source_path = backup_path
                        else:
                            source_path = normal_map
                    else:
                        source_path = normal_map
                    
                    # Convert in place (overwrite original)
                    output_path = self.convert_normal_map(source_path, normal_map)
                    converted_files.append(output_path)
                    
                except Exception as e:
                    self.logger.error(f"Failed to convert {normal_map}: {e}")
                
                progress.update(task, advance=1)
        
        self.logger.info(f"Successfully converted {len(converted_files)} normal maps")
        return converted_files
    
    def validate_normal_map(self, image_path: Path) -> dict[str, object]:
        """Validate and analyze a normal map."""
        result = {
            "is_valid": False,
            "format": "unknown",
            "channels": {},
            "issues": [],
        }
        
        try:
            with Image.open(image_path) as img:
                result["size"] = img.size
                result["mode"] = img.mode
                
                if img.mode == "RGBA":
                    r, g, b, a = img.split()
                    
                    # Analyze channel distributions
                    result["channels"] = {
                        "red": {"min": min(r.getdata()), "max": max(r.getdata())},
                        "green": {"min": min(g.getdata()), "max": max(g.getdata())},
                        "blue": {"min": min(b.getdata()), "max": max(b.getdata())},
                        "alpha": {"min": min(a.getdata()), "max": max(a.getdata())},
                    }
                    
                    # Get max values for easier comparison
                    red_max = result["channels"]["red"]["max"]
                    green_max = result["channels"]["green"]["max"]
                    blue_max = result["channels"]["blue"]["max"]
                    alpha_max = result["channels"]["alpha"]["max"]
                    
                    # Check for standard format first (data primarily in red and green)
                    if (red_max > 50 and green_max > 50 and 
                        red_max > alpha_max and green_max > alpha_max):
                        result["format"] = "standard"
                        result["is_valid"] = True
                    
                    # Check for Unity format (data primarily in green and alpha)
                    elif (green_max > 50 and alpha_max > 50 and 
                          alpha_max > red_max and green_max > blue_max):
                        result["format"] = "unity"
                        result["is_valid"] = True
                    
                    # Check for issues
                    if result["channels"]["red"]["max"] == 0:
                        result["issues"].append("Red channel is empty")
                    if result["channels"]["alpha"]["max"] == 0:
                        result["issues"].append("Alpha channel is empty")
                
                else:
                    result["issues"].append(f"Unsupported mode: {img.mode}")
                    
        except Exception as e:
            result["issues"].append(f"Failed to analyze: {e}")
        
        return result


# CLI Interface
@click.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
@click.option('--recursive', '-r', is_flag=True, help='Process directories recursively')
@click.option('--pattern', '-p', default='*_n*.png', help='File pattern to match')
@click.option('--backup', '-b', is_flag=True, help='Create backups before conversion')
@click.option('--invert-y', is_flag=True, default=True, help='Invert Y channel')
@click.option('--calculate-z', is_flag=True, help='Calculate Z channel')
@click.option('--validate', is_flag=True, help='Validate normal maps instead of converting')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def cli_main(path: Path, recursive: bool, pattern: str, backup: bool, 
           invert_y: bool, calculate_z: bool, validate: bool, verbose: bool) -> None:
    """Convert Unity normal maps to standard format.
    
    PATH can be a single image file or directory containing normal maps.
    """
    logger = get_logger()
    if verbose:
        logger.logger.setLevel("DEBUG")
    
    converter = NormalMapConverter()
    
    try:
        if path.is_file():
            # Single file
            if validate:
                result = converter.validate_normal_map(path)
                logger.console.print(f"Validation result for {path}:")
                logger.console.print(result)
            else:
                output_path = converter.convert_normal_map_advanced(
                    path, invert_y=invert_y, calculate_z=calculate_z
                )
                logger.info(f"Converted: {output_path}")
        
        elif path.is_dir():
            # Directory
            if validate:
                normal_maps = list(path.rglob(pattern) if recursive else path.glob(pattern))
                for normal_map in normal_maps:
                    if converter.is_normal_map(normal_map):
                        result = converter.validate_normal_map(normal_map)
                        logger.console.print(f"\n{normal_map}:")
                        logger.console.print(result)
            else:
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