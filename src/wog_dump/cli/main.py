"""CLI entry point for WOG Dump.

Commands live in their own modules under ``wog_dump.cli``; this module
only defines the top-level group, global options and ``main()``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click
from dotenv import dotenv_values

from .. import __version__
from ..core.config import get_config, set_config
from ..utils.logging import get_logger, set_log_level
from .auth import login, logout
from .cache import cache
from .convert import convert_normals
from .decrypt import decrypt_assets
from .download import download_assets, download_weapons
from .errors import OperationCancelled
from .info import info
from .pipeline import full_pipeline
from .scene import build_scene
from .unpack import unpack_assets


def load_env_file(env_path: Path) -> list[str]:
    """Apply ``WOG_*`` keys from a working-directory ``.env`` file.

    The file is untrusted input - any unpacked or shared directory can
    carry one - so only the tool's own ``WOG_*`` namespace is honored
    (transport variables such as HTTPS_PROXY or REQUESTS_CA_BUNDLE can
    never be planted this way) and variables already present in the
    environment always win. Returns the applied key names.
    """
    if not env_path.is_file():
        return []

    applied: list[str] = []
    for key, value in dotenv_values(env_path).items():
        if not key or value is None or not key.startswith("WOG_"):
            continue
        if os.environ.get(key) is not None:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


# Credential-affecting keys from a working-directory file deserve an
# explicit announcement: a .env planted in an unpacked "weapon pack"
# could otherwise silently take over the identity or the store location.
_SENSITIVE_ENV_KEYS = {
    "WOG_AUTH_ID",
    "WOG_AUTH_SESSION",
    "WOG_DEVICE_ID",
    "WOG_ACCOUNT_EMAIL",
    "WOG_ACCOUNT_PASSWORD",
    "WOG_DB_PATH",
}


def validate_config(ctx: click.Context) -> None:
    """Validate configuration and show warnings if needed."""
    config = ctx.obj["config"]
    logger = ctx.obj["logger"]

    # Check directory permissions
    directories = [config.assets_dir, config.encrypted_dir, config.decrypted_dir]
    for directory in directories:
        if directory and not directory.exists():
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.error(f"Permission denied creating directory: {directory}")
                sys.exit(1)
        elif directory and not directory.is_dir():
            logger.error(f"Path exists but is not a directory: {directory}")
            sys.exit(1)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--debug", "-d", is_flag=True, help="Enable debug logging")
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    help="Custom configuration directory",
)
@click.option("--max-threads", type=int, help="Maximum number of threads (1-16)")
@click.option(
    "--chunk-size", type=int, help="Chunk size for file operations in KB (1-1024)"
)
@click.option(
    "--strict-mode", is_flag=True, help="Enable strict validation and error handling"
)
@click.version_option(version=__version__, prog_name="WOG Dump")
@click.pass_context
def cli(
    ctx: click.Context,
    verbose: bool,
    debug: bool,
    config_dir: Path | None,
    max_threads: int | None,
    chunk_size: int | None,
    strict_mode: bool,
) -> None:
    """WOG Dump - Modern tool for extracting 3D models from World of Guns: Gun Disassembly.

    This tool provides a complete pipeline for downloading, decrypting, and unpacking
    3D gun models from World of Guns: Gun Disassembly with modern Python 3.12+ features,
    comprehensive error handling, and performance monitoring.

    Examples:
        wog-dump full-pipeline                    # Run complete extraction process
        wog-dump download-assets --update-keys    # Download with key update
        wog-dump decrypt-assets --weapons ak74,m4 # Decrypt specific weapons
        wog-dump info                            # Show configuration status
    """
    ctx.ensure_object(dict)

    logger = get_logger()

    # Honor a .env from the working directory (WOG_* keys only) without
    # overriding variables already in the environment. Explicit cwd
    # lookup: the default walks up from this file's location.
    applied_env = load_env_file(Path.cwd() / ".env")
    if applied_env:
        sensitive = sorted(set(applied_env) & _SENSITIVE_ENV_KEYS)
        if sensitive:
            logger.warning(
                f"Applied credential-affecting keys from {Path.cwd() / '.env'}: "
                f"{', '.join(sensitive)}. Make sure this .env is your own."
            )
        else:
            logger.info(f"Applied {len(applied_env)} keys from working-directory .env")

    # Set up logging
    if debug:
        set_log_level("DEBUG")
        logger.info("Debug logging enabled")
    elif verbose:
        set_log_level("INFO")
    else:
        set_log_level("WARNING")

    # Validate and set configuration
    config_updates: dict[str, Any] = {}
    if config_dir:
        config_updates["base_dir"] = config_dir
    if max_threads:
        if max_threads < 1 or max_threads > 16:
            logger.error("max-threads must be between 1 and 16")
            sys.exit(1)
        config_updates["max_threads"] = max_threads
    if chunk_size:
        if chunk_size < 1 or chunk_size > 1024:
            logger.error("chunk-size must be between 1 and 1024 KB")
            sys.exit(1)
        config_updates["chunk_size"] = chunk_size * 1024  # Convert to bytes
    if strict_mode:
        config_updates["strict_mode"] = True

    if config_updates:
        set_config(**config_updates)

    # Store context
    config = get_config()
    ctx.obj["config"] = config
    ctx.obj["logger"] = logger

    # Validate configuration
    validate_config(ctx)

    # DEBUG records: shown on the console with -d, kept in the log file
    # when WOG_LOG_LEVEL=DEBUG
    logger.log_system_info()


cli.add_command(download_weapons)
cli.add_command(download_assets)
cli.add_command(decrypt_assets)
cli.add_command(unpack_assets)
cli.add_command(convert_normals)
cli.add_command(build_scene)
cli.add_command(full_pipeline)
cli.add_command(login)
cli.add_command(logout)
cli.add_command(cache)
cli.add_command(info)


def main() -> None:
    """Main entry point with error handling."""
    try:
        cli()
    except OperationCancelled:
        sys.exit(130)
    except KeyboardInterrupt:
        logger = get_logger()
        logger.print_status("Operation cancelled by user", "warning")
        sys.exit(130)
    except Exception as e:
        logger = get_logger()
        logger.error(f"Unexpected error: {e}")
        # The traceback belongs in the log file; keep the console to the
        # one-line error unless -d asked for debug output
        if logger.is_debug():
            logger.logger.exception("Full traceback:")
        else:
            logger.log_file_only(f"Unexpected error traceback: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
