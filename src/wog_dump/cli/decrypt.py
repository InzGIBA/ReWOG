"""Decrypt command for the WOG Dump CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import UnityPy

from ..core.decrypt import AssetDecryptor
from ..core.keys import KeyManager
from ..utils.logging import get_logger
from .download import _load_weapon_list, _update_keys
from .errors import error_handler, print_banner_if_standalone


@click.command()
@click.option(
    "--update-keys", is_flag=True, help="Update decryption keys before decrypting"
)
@click.option(
    "--weapons", type=str, help="Comma-separated list of specific weapons to decrypt"
)
@click.option(
    "--validate", is_flag=True, help="Validate decrypted files after processing"
)
@click.pass_context
def decrypt_assets(
    ctx: click.Context, update_keys: bool, weapons: str | None, validate: bool
) -> tuple[list[Path], list[str]]:
    """Decrypt downloaded assets using fetched keys.

    Uses XOR decryption with MD5-derived keys to decrypt Unity assets
    into usable format for unpacking.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Asset decryption"):
        print_banner_if_standalone(ctx)
        nested = bool(ctx.obj.get("pipeline_nested"))

        weapon_list = _load_weapon_list(config, weapons)

        # Manage decryption keys
        key_manager = KeyManager(config)
        if update_keys:
            keys = _update_keys(config, weapon_list)
            if keys is None:
                keys = key_manager.load_keys()
        else:
            keys = key_manager.load_keys()

        if not keys:
            logger.error("No decryption keys found. Use --update-keys to fetch them.")
            sys.exit(1)

        # Decrypt assets
        results: tuple[list[Path], list[str]] = ([], [])
        with logger.operation_context("decryption", "asset decryption"):
            decryptor = AssetDecryptor(config)
            decrypted_files, failed_assets = decryptor.decrypt_all_assets(
                keys, weapons=weapon_list if weapons else None
            )
            results = (decrypted_files, failed_assets)

            if validate:
                with logger.operation_context("validation", "file validation"):
                    validation_failures = validate_decrypted_files(decrypted_files)
                    if validation_failures:
                        logger.warning(
                            f"Validation failed for {len(validation_failures)} files"
                        )

            if not nested:
                logger.print_status(
                    f"Decrypted {len(decrypted_files)} files", "success"
                )

            if failed_assets:
                logger.print_status(
                    f"Failed to decrypt {len(failed_assets)} assets", "error"
                )
                if config.strict_mode:
                    sys.exit(1)

    return results


def validate_decrypted_files(files: list[Path]) -> list[Path]:
    """Validate decrypted files and return the list of validation failures."""
    logger = get_logger()
    validation_failures = []

    with logger.create_task_progress() as progress:
        task = progress.add_task("Validating files", total=len(files))

        for file_path in files:
            try:
                if not file_path.exists() or file_path.stat().st_size == 0:
                    validation_failures.append(file_path)
                    continue

                # Try to load as a Unity asset for additional validation
                try:
                    env = UnityPy.load(str(file_path))
                    if not env.objects:
                        validation_failures.append(file_path)
                except Exception:
                    validation_failures.append(file_path)

            except Exception:
                validation_failures.append(file_path)

            progress.update(task, advance=1)

    return validation_failures
