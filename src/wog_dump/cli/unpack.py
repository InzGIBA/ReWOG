"""Unpack command for the WOG Dump CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..core.unpack import AssetUnpacker
from .errors import error_handler


@click.command()
@click.option(
    "--input-dir",
    type=click.Path(exists=True, path_type=Path),
    help="Input directory containing decrypted assets",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    help="Output directory for unpacked files",
)
@click.option(
    "--asset-filter",
    type=str,
    help="Filter assets by name pattern (supports wildcards)",
)
@click.option(
    "--extract-types",
    type=str,
    default="Texture2D,Mesh,Material",
    help="Comma-separated list of Unity object types to extract",
)
@click.pass_context
def unpack_assets(
    ctx: click.Context,
    input_dir: Path | None,
    output_dir: Path | None,
    asset_filter: str | None,
    extract_types: str,
) -> None:
    """Unpack decrypted Unity assets to extract models and textures.

    Extracts 3D models, textures, and materials from decrypted Unity asset files
    into standard formats (OBJ, PNG, etc.).
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Asset unpacking"):
        logger.print_banner()

        # Set directories
        if input_dir is None:
            input_dir = config.decrypted_dir
        if output_dir is None:
            output_dir = config.base_dir / "runtime" / "unpacked"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Find assets to process
        if asset_filter:
            asset_files = list(input_dir.glob(f"{asset_filter}.unity3d"))
        else:
            asset_files = list(input_dir.glob("*.unity3d"))

        if not asset_files:
            logger.error(f"No assets found in {input_dir}")
            sys.exit(1)

        logger.info(f"Found {len(asset_files)} assets to unpack")

        extract_type_list = [t.strip() for t in extract_types.split(",")]

        # Unpack assets
        with logger.operation_context("unpacking", "asset unpacking"):
            unpacker = AssetUnpacker(config)
            results = unpacker.unpack_multiple_assets(
                asset_files, output_dir=output_dir, extract_types=extract_type_list
            )

            successful = sum(1 for files in results.values() if files)
            total_files = sum(len(files) for files in results.values())

            logger.print_status(
                f"Unpacked {successful}/{len(asset_files)} assets", "success"
            )
            logger.print_status(
                f"Extracted {total_files} files to {output_dir}", "info"
            )
