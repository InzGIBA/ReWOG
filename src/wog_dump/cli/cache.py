"""Cache management command for the WOG Dump CLI."""

from __future__ import annotations

import click

from ..core.database import WOGDatabase
from ..core.download import DownloadManager
from .errors import error_handler


@click.command()
@click.option("--clear", is_flag=True, help="Clear cached weapons and keys")
@click.option(
    "--migrate", is_flag=True, help="Import legacy runtime txt files into the database"
)
@click.option("--show-cache", is_flag=True, help="Show detailed database statistics")
@click.option(
    "--validate-assets",
    is_flag=True,
    help="Validate that locally cached assets exist and are non-empty",
)
@click.pass_context
def cache(
    ctx: click.Context,
    clear: bool,
    migrate: bool,
    show_cache: bool,
    validate_assets: bool,
) -> None:
    """Manage the local database (weapons, keys, account).

    Data lives in a per-user SQLite database outside the repository. Use
    --migrate to import weapons.txt/keys.txt from an older installation.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Cache management"):
        logger.print_banner()

        db = WOGDatabase(config)

        if clear:
            with logger.operation_context("cache_clear", "cache clear"):
                db.clear_cached_data()
                logger.print_status("Cached weapons and keys cleared", "success")

        elif migrate:
            with logger.operation_context("migration", "legacy import"):
                success = db.import_legacy_txt(
                    weapons_file=config.weapons_file,
                    keys_file=config.keys_file,
                )
                if success:
                    logger.print_status(
                        "Legacy import completed successfully", "success"
                    )
                else:
                    logger.print_status("No legacy files found to import", "warning")

        elif validate_assets:
            with logger.operation_context("asset_validation", "asset validation"):
                weapons = db.get_weapons()
                if not weapons:
                    logger.error("No weapons found. Run 'download-weapons' first.")
                    return

                with DownloadManager(config) as downloader:
                    valid, invalid = downloader.validate_cached_assets(weapons)

                if invalid:
                    logger.console.print(
                        f"\n[bold red]Invalid assets found ({len(invalid)}):[/bold red]"
                    )
                    for asset in invalid[:10]:
                        logger.console.print(f"  • {asset}")
                    if len(invalid) > 10:
                        logger.console.print(f"  ... and {len(invalid) - 10} more")

                logger.print_status(
                    f"Validation: {len(valid)} valid, {len(invalid)} invalid",
                    "success" if len(invalid) == 0 else "warning",
                )

        else:
            # Default and --show-cache: database statistics
            stats = db.get_stats()
            account = (
                stats["account"]["email"] or "not logged in (run 'wog-dump login')"
            )

            cache_data = [
                ["Database", stats["database"]],
                ["Account", account],
                ["Weapons Count", str(stats["weapons"]["count"])],
                ["Keys Count", str(stats["keys"]["count"])],
                ["Session Updated", stats["identity"]["updated_at"] or "never"],
            ]

            logger.print_table("Database Statistics", ["Property", "Value"], cache_data)

            weapons = db.get_weapons()
            if show_cache and weapons:
                logger.console.print("\n[bold]Sample weapons (first 10):[/bold]")
                for weapon in weapons[:10]:
                    has_key = "✓" if db.get_key(weapon) else "✗"
                    logger.console.print(f"  {has_key} {weapon}")
                if len(weapons) > 10:
                    logger.console.print(f"  ... and {len(weapons) - 10} more")
