"""Download commands for the WOG Dump CLI."""

from __future__ import annotations

import sys

import click

from ..core.config import WOGConfig
from ..core.download import DownloadManager
from ..core.keys import KeyManager
from ..core.unpack import WeaponListProcessor
from ..utils.logging import get_logger
from .errors import error_handler


def _load_weapon_list(config: WOGConfig, weapons: str | None) -> list[str]:
    """Resolve the weapon list from --weapons or the database."""
    logger = get_logger()
    if weapons:
        weapon_list = [w.strip() for w in weapons.split(",")]
        logger.info(f"Using custom weapon list: {len(weapon_list)} weapons")
        return weapon_list

    processor = WeaponListProcessor(config)
    try:
        return processor.load_weapon_list()
    except Exception:
        logger.error("No weapon list found. Run 'download-weapons' first.")
        sys.exit(1)


@click.command()
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force download even if file exists and is up to date",
)
@click.option("--validate", is_flag=True, help="Validate downloaded file integrity")
@click.pass_context
def download_weapons(ctx: click.Context, force: bool, validate: bool) -> None:
    """Download and extract the weapon list from game servers.

    This command downloads the spider_gen.unity3d asset containing the complete
    weapon list and extracts it into a usable format.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Weapon list download"):
        logger.print_banner()

        with logger.operation_context("download_weapons", "weapon list download"):
            with DownloadManager(config) as downloader:
                asset_path = downloader.download_weapon_list(force_update=force)

                if validate and not downloader.validate_asset(asset_path):
                    logger.error("Downloaded asset failed validation")
                    sys.exit(1)

                processor = WeaponListProcessor(config)
                weapon_list = processor.process_weapon_list_asset(asset_path)

                logger.print_status(
                    f"Successfully extracted {len(weapon_list)} weapons", "success"
                )

                if weapon_list:
                    logger.console.print("\n[bold]Sample weapons (first 10):[/bold]")
                    for i, weapon in enumerate(weapon_list[:10], 1):
                        logger.console.print(f"  {i:2d}. {weapon}")

                    if len(weapon_list) > 10:
                        logger.console.print(f"  ... and {len(weapon_list) - 10} more")


def _update_keys(config: WOGConfig, weapon_list: list[str]) -> dict[str, str] | None:
    """Fetch keys for weapon_list and merge them into the store."""
    logger = get_logger()
    with logger.operation_context("key_update", "decryption key update"):
        key_manager = KeyManager(config)
        fetched = key_manager.fetch_keys_parallel(weapon_list)
        if fetched:
            # Merge: a partial fetch must never drop existing keys
            merged = {**key_manager.load_keys(), **fetched}
            key_manager.save_keys(merged)
            return merged
        return None


@click.command()
@click.option(
    "--update-keys", is_flag=True, help="Update decryption keys before downloading"
)
@click.option(
    "--check-only", is_flag=True, help="Only check for updates, do not download"
)
@click.option(
    "--weapons", type=str, help="Comma-separated list of specific weapons to download"
)
@click.option(
    "--batch-size",
    type=int,
    default=50,
    help="Number of assets to process in each batch",
)
@click.option(
    "--continue-on-error",
    is_flag=True,
    help="Continue processing even if some downloads fail",
)
@click.pass_context
def download_assets(
    ctx: click.Context,
    update_keys: bool,
    check_only: bool,
    weapons: str | None,
    batch_size: int,
    continue_on_error: bool,
) -> None:
    """Download weapon assets from game servers with batch processing.

    Downloads Unity asset files containing 3D models and textures for weapons.
    Supports batch processing for handling large numbers of assets efficiently.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Asset download"):
        logger.print_banner()

        weapon_list = _load_weapon_list(config, weapons)

        # Update keys if requested
        if update_keys:
            if _update_keys(config, weapon_list) is None and config.strict_mode:
                logger.error("Failed to fetch keys in strict mode")
                sys.exit(1)

        # Process downloads
        with logger.operation_context("asset_download", "asset download"):
            with DownloadManager(config) as downloader:
                if check_only:
                    to_download = downloader.check_for_updates(weapon_list)
                    logger.print_status(
                        f"Found {len(to_download)} assets needing updates", "info"
                    )

                    if to_download:
                        logger.console.print("\n[bold]Assets needing updates:[/bold]")
                        display_count = min(20, len(to_download))
                        for weapon in to_download[:display_count]:
                            logger.console.print(f"  • {weapon}")
                        if len(to_download) > display_count:
                            logger.console.print(
                                f"  ... and {len(to_download) - display_count} more"
                            )
                else:
                    successful, failed = downloader.download_assets_batched(
                        weapon_list,
                        batch_size=batch_size,
                        continue_on_error=continue_on_error,
                    )

                    if successful:
                        logger.print_status(
                            f"Downloaded {len(successful)} assets successfully",
                            "success",
                        )

                    if failed:
                        logger.print_status(
                            f"Failed to download {len(failed)} assets", "error"
                        )
                        if not continue_on_error and config.strict_mode:
                            sys.exit(1)
