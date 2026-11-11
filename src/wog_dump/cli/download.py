"""Download commands for the WOG Dump CLI."""

from __future__ import annotations

import sys

import click

from ..core.config import WOGConfig
from ..core.download import DownloadManager
from ..core.keys import KeyManager
from ..core.paths import is_safe_weapon_name
from ..core.spider import extract_spider_content
from ..core.unpack import WeaponListProcessor
from ..utils.i18n import t
from ..utils.logging import get_logger
from ..utils.progress import make_byte_tracker
from .errors import error_handler, print_banner_if_standalone


def _load_weapon_list(config: WOGConfig, weapons: str | None) -> list[str]:
    """Resolve the weapon list from --weapons or the database."""
    logger = get_logger()
    if weapons:
        weapon_list = [w.strip() for w in weapons.split(",")]
        invalid = [w for w in weapon_list if not is_safe_weapon_name(w)]
        if invalid:
            # Names become URL segments and filenames; refuse anything
            # outside the vendor alphabet instead of downloading it
            logger.error(
                f"Invalid weapon name(s) in --weapons: {', '.join(repr(w) for w in invalid)}"
            )
            sys.exit(1)
        deduped = list(dict.fromkeys(weapon_list))
        logger.info(f"Using custom weapon list: {len(deduped)} weapons")
        return deduped

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
def download_weapons(ctx: click.Context, force: bool, validate: bool) -> list[str]:
    """Download and extract the weapon list from game servers.

    This command downloads the spider_gen.unity3d asset containing the complete
    weapon list and extracts it into a usable format.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Weapon list download"):
        print_banner_if_standalone(ctx)
        nested = bool(ctx.obj.get("pipeline_nested"))

        weapon_list: list[str] = []
        with logger.operation_context("download_weapons", "weapon list download"):
            with DownloadManager(config) as downloader:
                spinner = None
                if not nested and logger.progress_enabled:
                    spinner = logger.console.status("Fetching weapon list")
                    spinner.start()
                try:
                    asset_path = downloader.download_weapon_list(force_update=force)
                finally:
                    if spinner:
                        spinner.stop()

                if validate and not downloader.validate_asset(asset_path):
                    logger.error("Downloaded asset failed validation")
                    sys.exit(1)

                processor = WeaponListProcessor(config)
                weapon_list = processor.process_weapon_list_asset(asset_path)

                # The same bundle carries the store cards: extract the
                # per-weapon icons, studio renders and metadata records
                spider_result = extract_spider_content(
                    config, asset_path, weapon_list, force=force
                )

                if not nested:
                    logger.print_status(
                        f"Weapon list: {len(weapon_list)} weapons", "success"
                    )
                    logger.print_status(
                        f"Cards: {spider_result.icons} icons, "
                        f"{spider_result.cards} renders, "
                        f"{spider_result.meta_count} metadata records",
                        "success",
                    )

    return weapon_list


def _update_keys(config: WOGConfig, weapon_list: list[str]) -> dict[str, str] | None:
    """Fetch keys for weapon_list and merge them into the store."""
    logger = get_logger()
    with logger.operation_context("key_update", "decryption key update"):
        key_manager = KeyManager(config)
        fetched = key_manager.fetch_keys_parallel(weapon_list)
        if fetched:
            # Merge: a partial fetch must never drop existing keys, and
            # per-row provenance must survive the write
            key_manager.db.merge_keys(fetched, source="api_fetch")
            key_manager._key_cache.update(fetched)
            return key_manager.db.get_keys()
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
) -> tuple[list[str], list[str], int]:
    """Download weapon assets from game servers with batch processing.

    Downloads Unity asset files containing 3D models and textures for weapons.
    Supports batch processing for handling large numbers of assets efficiently.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Asset download"):
        print_banner_if_standalone(ctx)
        nested = bool(ctx.obj.get("pipeline_nested"))

        weapon_list = _load_weapon_list(config, weapons)

        # Update keys if requested
        if update_keys:
            if _update_keys(config, weapon_list) is None and config.strict_mode:
                logger.error("Failed to fetch keys in strict mode")
                sys.exit(1)

        # Process downloads
        results: tuple[list[str], list[str], int] = ([], [], 0)
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
                    results = (to_download, [], 0)
                else:
                    byte_bar = make_byte_tracker(logger)
                    try:
                        successful, failed, missing = (
                            downloader.download_assets_batched(
                                weapon_list,
                                batch_size=batch_size,
                                continue_on_error=continue_on_error,
                                byte_progress=byte_bar[1] if byte_bar else None,
                            )
                        )
                    finally:
                        if byte_bar:
                            byte_bar[0].stop()
                    results = (successful, failed, missing)

                    if successful and not nested:
                        detail = f"Downloaded {len(successful)} assets"
                        if missing:
                            detail += f", {missing} not on server"
                        logger.print_status(detail, "success")
                    elif not successful and not failed and not nested:
                        logger.print_status(
                            t("All {count} assets up to date", count=len(weapon_list)),
                            "success",
                        )

                    if failed:
                        logger.print_status(
                            f"Failed to download {len(failed)} assets", "error"
                        )
                        if not continue_on_error and config.strict_mode:
                            sys.exit(1)

    return results
