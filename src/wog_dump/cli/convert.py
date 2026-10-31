"""Normal map conversion command for the WOG Dump CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..utils.normal_map import NormalMapConverter
from .errors import error_handler, print_banner_if_standalone


@click.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--recursive", "-r", is_flag=True, help="Process directories recursively")
@click.option("--backup", "-b", is_flag=True, help="Create backups before conversion")
@click.option(
    "--pattern",
    default="*.png",
    help="File pattern to match; only swizzled normal maps are converted",
)
@click.pass_context
def convert_normals(
    ctx: click.Context, path: Path, recursive: bool, backup: bool, pattern: str
) -> int:
    """Convert Unity normal maps to standard format with batch processing.

    Converts Unity's normal map format (data in green/alpha channels) to
    standard format (data in red/green channels) for compatibility with
    other 3D applications.
    """
    logger = ctx.obj["logger"]

    with error_handler("Normal map conversion"):
        print_banner_if_standalone(ctx)
        nested = bool(ctx.obj.get("pipeline_nested"))

        converter = NormalMapConverter()

        converted = 0
        with logger.operation_context("normal_conversion", "normal map conversion"):
            if path.is_file():
                output_path = converter.convert_normal_map(path)
                converted = 1
                if not nested:
                    logger.print_status(f"Converted: {output_path}", "success")
            elif path.is_dir():
                converted_files = converter.batch_convert_directory(
                    path, recursive=recursive, pattern=pattern, backup=backup
                )
                converted = len(converted_files)
                if not nested:
                    logger.print_status(f"Converted {converted} normal maps", "success")
            else:
                logger.error(f"Invalid path: {path}")
                sys.exit(1)

    return converted
