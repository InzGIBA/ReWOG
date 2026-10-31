"""Normal map conversion utilities for WOG Dump.

Unity stores normal maps in a swizzled layout (DXT5nm-style): the X
component lives in the alpha channel, Y in green, red is forced to
near-white and blue carries no usable data. Exported naively as RGBA
PNG such textures look washed-out and semi-transparent in viewers, so
extraction and conversion rewrite them to standard opaque RGB maps
(X/Y/Z in red/green/blue, Z reconstructed as ``sqrt(1 - x^2 - y^2)``).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import click
from PIL import Image, ImageChops, ImageStat

from ..utils.logging import get_logger

# Detection thresholds for the swizzled signature: the red channel is
# near-constant white (forced by the encoder) while green (Y) and alpha
# (X) carry varying per-pixel data. Measured on real game textures: a
# normal map scores R mean 247 / std 15, albedo atlases R std 28+.
_RED_MEAN_THRESHOLD = 230.0
_RED_STD_THRESHOLD = 25.0
_DATA_STD_THRESHOLD = 30.0
# Real normal maps are unit vectors: x^2 + y^2 <= 1 for essentially
# every pixel (plus headroom for block compression). Constant-red data
# that is NOT a normal map (e.g. 1D heat ramps) fails this hard.
_MAX_UNIT_DEVIATION = 1.05
_MIN_UNIT_NORMAL_RATIO = 0.9


class NormalMapError(Exception):
    """Raised when normal map operations fail."""


def _unit_normal_ratio(image: Image.Image, max_samples: int = 20000) -> float:
    """Fraction of sampled pixels whose (alpha, green) pair forms a valid
    unit normal (lies inside the unit circle)."""
    width, height = image.size
    stride = max(1, int((width * height / max_samples) ** 0.5))
    green = image.getchannel("G").tobytes()
    alpha = image.getchannel("A").tobytes()

    inside = total = 0
    for y in range(0, height, stride):
        base = y * width
        for x in range(0, width, stride):
            i = base + x
            nx = (alpha[i] - 127.5) / 127.5
            ny = (green[i] - 127.5) / 127.5
            total += 1
            if nx * nx + ny * ny <= _MAX_UNIT_DEVIATION:
                inside += 1

    return inside / total if total else 0.0


def is_unity_swizzled_normal(image: Image.Image) -> bool:
    """Heuristically detect a Unity swizzled (DXT5nm-style) normal map.

    Must run on the full-resolution image: resampling smears the
    constant red channel and breaks the test.
    """
    bands = image.getbands()
    if "R" not in bands or "A" not in bands:
        return False

    rgba = image.convert("RGBA")
    stat = ImageStat.Stat(rgba)
    if not (
        stat.mean[0] >= _RED_MEAN_THRESHOLD
        and stat.stddev[0] <= _RED_STD_THRESHOLD
        and stat.stddev[1] >= _DATA_STD_THRESHOLD
        and stat.stddev[3] >= _DATA_STD_THRESHOLD
    ):
        return False

    return _unit_normal_ratio(rgba) >= _MIN_UNIT_NORMAL_RATIO


def unswizzle_normal_map(image: Image.Image) -> Image.Image:
    """Rewrite a swizzled normal map as a standard opaque RGB map."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    _, y_band, _, x_band = image.split()

    # Integer-only Z reconstruction: q = (2v - 255)^2 // 512 fits in a
    # byte and x^2 + y^2 == (q_x + q_y) / 127, so two 256-entry lookup
    # tables replace per-pixel float math.
    square_lut = [(2 * v - 255) ** 2 // 512 for v in range(256)]
    z_lut = [
        round(127.5 * (1 + math.sqrt(max(0.0, 1.0 - v / 127.0)))) for v in range(256)
    ]
    sum_q = ImageChops.add(x_band.point(square_lut), y_band.point(square_lut))
    z_band = sum_q.point(z_lut)

    return Image.merge("RGB", (x_band, y_band, z_band))


class NormalMapConverter:
    """Converts Unity normal maps to standard format."""

    def __init__(self) -> None:
        self.logger = get_logger()

    def is_normal_map(self, image_path: Path) -> bool:
        """Check if an image file is likely a normal map by its name."""
        name_lower = image_path.name.lower()
        normal_indicators = ["_n.", "_normal.", "_nrm.", "_norm."]

        return any(indicator in name_lower for indicator in normal_indicators)

    def convert_normal_map(
        self, input_path: Path, output_path: Path | None = None, force: bool = True
    ) -> Path:
        """Convert a Unity normal map to standard opaque RGB.

        ``force`` treats the input as Unity-swizzled unconditionally (an
        explicit single-file conversion); with ``force=False`` files
        whose content does not match the swizzled signature are skipped
        unchanged, which keeps batch runs idempotent.
        """
        if output_path is None:
            output_path = (
                input_path.parent / f"{input_path.stem}_converted{input_path.suffix}"
            )

        try:
            with Image.open(input_path) as img:
                img.load()

                if not force and not is_unity_swizzled_normal(img):
                    self.logger.debug(
                        f"Not a swizzled normal map, left unchanged: {input_path}"
                    )
                    return input_path
                converted = unswizzle_normal_map(img)

            converted.save(output_path)

            self.logger.debug(f"Converted normal map: {input_path} -> {output_path}")
            return output_path

        except Exception as e:
            raise NormalMapError(f"Failed to convert {input_path}: {e}") from e

    def convert_normal_map_advanced(
        self,
        input_path: Path,
        output_path: Path | None = None,
        invert_y: bool = True,
    ) -> Path:
        """Convert a normal map with optional Y inversion (DirectX <-> OpenGL)."""
        if output_path is None:
            output_path = (
                input_path.parent / f"{input_path.stem}_converted{input_path.suffix}"
            )

        try:
            with Image.open(input_path) as img:
                img.load()
                converted = unswizzle_normal_map(img)

            if invert_y:
                r_band, g_band, b_band = converted.split()
                converted = Image.merge(
                    "RGB", (r_band, ImageChops.invert(g_band), b_band)
                )

            converted.save(output_path)
            return output_path

        except Exception as e:
            raise NormalMapError(f"Failed to convert {input_path}: {e}") from e

    def validate_normal_map(self, image_path: Path) -> dict[str, Any]:
        """Validate a normal map and detect its format."""
        result: dict[str, Any] = {
            "is_valid": False,
            "format": "unknown",
            "mode": "unknown",
            "size": (0, 0),
            "channels": {},
            "issues": [],
        }

        try:
            with Image.open(image_path) as img:
                result["size"] = img.size
                result["mode"] = img.mode

                if img.mode != "RGBA":
                    result["issues"].append(f"Unsupported mode: {img.mode}")
                    return result

                # Sample pixels spread over the image and average the channels
                sample_pixels = []
                width, height = img.size
                for y in range(0, height, height // 4 or 1):
                    for x in range(0, width, width // 4 or 1):
                        sample_pixels.append(img.getpixel((x, y)))

                averages = {
                    "r": sum(p[0] for p in sample_pixels) / len(sample_pixels),
                    "g": sum(p[1] for p in sample_pixels) / len(sample_pixels),
                    "b": sum(p[2] for p in sample_pixels) / len(sample_pixels),
                    "a": sum(p[3] for p in sample_pixels) / len(sample_pixels),
                }
                result["channels"] = {
                    name: round(value, 1) for name, value in averages.items()
                }

                # Unity format packs X/Y into alpha/green; standard format
                # keeps all three components in red/green/blue.
                result["format"] = (
                    "unity" if is_unity_swizzled_normal(img) else "standard"
                )
                result["is_valid"] = True

        except Exception as e:
            result["issues"].append(f"Failed to open image: {e}")

        return result

    def batch_convert_directory(
        self,
        directory: Path,
        recursive: bool = True,
        pattern: str = "*.png",
        backup: bool = False,
    ) -> list[Path]:
        """Convert every swizzled normal map in a directory (in place).

        Candidates are picked by ``pattern`` and then verified by
        content, so non-normal textures and already-converted files are
        left untouched and re-runs are idempotent.
        """
        image_extensions = {".png", ".jpg", ".jpeg", ".tga", ".bmp"}
        candidates = [
            f
            for f in (
                directory.rglob(pattern) if recursive else directory.glob(pattern)
            )
            if f.suffix.lower() in image_extensions
        ]

        if not candidates:
            self.logger.info(f"No image files found in {directory}")
            return []

        self.logger.info(f"Found {len(candidates)} candidate images")

        converted_files: list[Path] = []
        skipped = 0

        with self.logger.create_task_progress() as progress:
            task = progress.add_task("Converting normal maps", total=len(candidates))

            for candidate in candidates:
                try:
                    with Image.open(candidate) as img:
                        img.load()
                        swizzled = is_unity_swizzled_normal(img)

                    if not swizzled:
                        skipped += 1
                        continue

                    if backup:
                        backup_path = (
                            candidate.parent
                            / f"{candidate.stem}_backup{candidate.suffix}"
                        )
                        if not backup_path.exists():
                            backup_path.write_bytes(candidate.read_bytes())

                    converted_files.append(
                        self.convert_normal_map(candidate, candidate, force=True)
                    )

                except Exception as e:
                    self.logger.error(f"Failed to convert {candidate}: {e}")

                progress.update(task, advance=1)

        summary = f"Converted {len(converted_files)} normal maps"
        if skipped:
            summary += f", {skipped} files left unchanged"
        self.logger.info(summary)
        return converted_files


# CLI Interface
@click.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--recursive", "-r", is_flag=True, help="Process directories recursively")
@click.option(
    "--pattern",
    "-p",
    default="*.png",
    help="File pattern to match; only swizzled normal maps are converted",
)
@click.option("--backup", "-b", is_flag=True, help="Create backups before conversion")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def cli_main(
    path: Path, recursive: bool, pattern: str, backup: bool, verbose: bool
) -> None:
    """Convert Unity normal maps to standard format.

    PATH can be a single image file or directory containing normal maps.
    """
    logger = get_logger()
    if verbose:
        logger.set_level("DEBUG")

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
