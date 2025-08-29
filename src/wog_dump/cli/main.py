"""Enhanced CLI interface for WOG Dump with improved error handling and user experience."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import click

from ..core.config import get_config, set_config
from ..core.decrypt import AssetDecryptor, KeyManager, DecryptionError, AuthenticationError
from ..core.download import DownloadManager, DownloadError
from ..core.unpack import AssetUnpacker, WeaponListProcessor, UnpackError
from ..utils.logging import get_logger, set_log_level
from ..utils.normal_map import NormalMapConverter, NormalMapError


class CLIError(Exception):
    """Base exception for CLI operations."""
    pass


class OperationCancelled(CLIError):
    """Raised when operation is cancelled by user."""
    pass


@contextmanager
def error_handler(operation_name: str):
    """Context manager for consistent error handling."""
    logger = get_logger()
    try:
        yield
    except KeyboardInterrupt:
        logger.print_status(f"{operation_name} cancelled by user", "warning")
        raise OperationCancelled() from None
    except (DecryptionError, DownloadError, UnpackError, NormalMapError) as e:
        logger.print_status(f"{operation_name} failed: {str(e)}", "error")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error in {operation_name}: {e}")
        if logger.logger.level <= 10:  # DEBUG level
            logger.logger.exception("Full traceback:")
        sys.exit(1)


def validate_config(ctx: click.Context) -> None:
    """Validate configuration and show warnings if needed."""
    config = ctx.obj['config']
    logger = ctx.obj['logger']

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
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.option('--debug', '-d', is_flag=True, help='Enable debug logging')
@click.option('--config-dir', type=click.Path(path_type=Path),
              help='Custom configuration directory')
@click.option('--max-threads', type=int,
              help='Maximum number of threads (1-32)')
@click.option('--chunk-size', type=int,
              help='Chunk size for file operations in KB (1-1024)')
@click.option('--strict-mode', is_flag=True,
              help='Enable strict validation and error handling')
@click.version_option(version='2.3.1', prog_name='WOG Dump')
@click.pass_context
def cli(ctx: click.Context, verbose: bool, debug: bool,
        config_dir: Path | None, max_threads: int | None,
        chunk_size: int | None, strict_mode: bool) -> None:
    """WOG Dump - Modern tool for extracting 3D models from World of Guns: Gun Disassembly.

    This tool provides a complete pipeline for downloading, decrypting, and unpacking
    3D gun models from World of Guns: Gun Disassembly with modern Python 3.12+ features,
    comprehensive error handling, and performance monitoring.

    Examples:
        wog-dump full-pipeline                    # Run complete extraction process
        wog-dump download-assets --update-keys    # Download with key update
        wog-dump decrypt-assets --weapons ak47,m4 # Decrypt specific weapons
        wog-dump info                            # Show configuration status
    """
    # Ensure context object exists
    ctx.ensure_object(dict)

    # Set up logging first
    logger = get_logger()
    if debug:
        set_log_level("DEBUG")
        logger.info("Debug logging enabled")
    elif verbose:
        set_log_level("INFO")
    else:
        set_log_level("WARNING")

    # Validate and set configuration
    config_updates = {}
    if config_dir:
        config_updates['base_dir'] = config_dir
    if max_threads:
        if max_threads < 1 or max_threads > 32:
            logger.error("max-threads must be between 1 and 32")
            sys.exit(1)
        config_updates['max_threads'] = max_threads
    if chunk_size:
        if chunk_size < 1 or chunk_size > 1024:
            logger.error("chunk-size must be between 1 and 1024 KB")
            sys.exit(1)
        config_updates['chunk_size'] = chunk_size * 1024  # Convert to bytes
    if strict_mode:
        config_updates['strict_mode'] = True

    if config_updates:
        set_config(**config_updates)

    # Store context
    config = get_config()
    ctx.obj['config'] = config
    ctx.obj['logger'] = logger

    # Validate configuration
    validate_config(ctx)

    # Log system info in debug mode
    if logger.logger.level <= 10:  # DEBUG
        logger.log_system_info()


@cli.command()
@click.option('--force', '-f', is_flag=True,
              help='Force download even if file exists and is up to date')
@click.option('--validate', is_flag=True,
              help='Validate downloaded file integrity')
@click.pass_context
def download_weapons(ctx: click.Context, force: bool, validate: bool) -> None:
    """Download and extract the weapon list from game servers.

    This command downloads the spider_gen.unity3d asset containing the complete
    weapon list and extracts it into a usable format.
    """
    logger = ctx.obj['logger']
    config = ctx.obj['config']

    with error_handler("Weapon list download"):
        logger.print_banner()

        with logger.operation_context("download_weapons", "weapon list download"):
            with DownloadManager(config) as downloader:
                # Download weapon list asset
                asset_path = downloader.download_weapon_list(force_update=force)

                # Validate if requested
                if validate and not downloader.validate_asset(asset_path):
                    logger.error("Downloaded asset failed validation")
                    sys.exit(1)

                # Process and extract weapon list
                processor = WeaponListProcessor(config)
                weapon_list = processor.process_weapon_list_asset(asset_path)

                logger.print_status(f"Successfully extracted {len(weapon_list)} weapons", "success")

                # Show sample weapons
                if weapon_list:
                    logger.console.print("\n[bold]Sample weapons (first 10):[/bold]")
                    for i, weapon in enumerate(weapon_list[:10], 1):
                        logger.console.print(f"  {i:2d}. {weapon}")

                    if len(weapon_list) > 10:
                        logger.console.print(f"  ... and {len(weapon_list) - 10} more")


@cli.command()
@click.option('--update-keys', is_flag=True,
              help='Update decryption keys before downloading')
@click.option('--check-only', is_flag=True,
              help='Only check for updates, do not download')
@click.option('--weapons', type=str,
              help='Comma-separated list of specific weapons to download')
@click.option('--batch-size', type=int, default=50,
              help='Number of assets to process in each batch')
@click.option('--continue-on-error', is_flag=True,
              help='Continue processing even if some downloads fail')
@click.pass_context
def download_assets(ctx: click.Context, update_keys: bool, check_only: bool,
                   weapons: str | None, batch_size: int, continue_on_error: bool) -> None:
    """Download weapon assets from game servers with batch processing.

    Downloads Unity asset files containing 3D models and textures for weapons.
    Supports batch processing for handling large numbers of assets efficiently.
    """
    logger = ctx.obj['logger']
    config = ctx.obj['config']

    with error_handler("Asset download"):
        logger.print_banner()

        # Load weapon list
        processor = WeaponListProcessor(config)
        if weapons:
            weapon_list = [w.strip() for w in weapons.split(',')]
            logger.info(f"Using custom weapon list: {len(weapon_list)} weapons")
        else:
            try:
                weapon_list = processor.load_weapon_list()
            except Exception:
                logger.error("No weapon list found. Run 'download-weapons' first.")
                sys.exit(1)

        # Update keys if requested
        if update_keys:
            with logger.operation_context("key_update", "decryption key update"):
                key_manager = KeyManager(config)
                keys = key_manager.fetch_keys_parallel(weapon_list)
                if keys:
                    key_manager.save_keys(keys)
                elif config.strict_mode:
                    logger.error("Failed to fetch keys in strict mode")
                    sys.exit(1)

        # Process downloads
        with logger.operation_context("asset_download", "asset download"):
            with DownloadManager(config) as downloader:
                if check_only:
                    to_download = downloader.check_for_updates(weapon_list)
                    logger.print_status(f"Found {len(to_download)} assets needing updates", "info")

                    if to_download:
                        logger.console.print("\n[bold]Assets needing updates:[/bold]")
                        display_count = min(20, len(to_download))
                        for weapon in to_download[:display_count]:
                            logger.console.print(f"  • {weapon}")
                        if len(to_download) > display_count:
                            logger.console.print(f"  ... and {len(to_download) - display_count} more")
                else:
                    successful, failed = downloader.download_assets_batched(
                        weapon_list, batch_size=batch_size, continue_on_error=continue_on_error
                    )

                    if successful:
                        logger.print_status(f"Downloaded {len(successful)} assets successfully", "success")

                    if failed:
                        logger.print_status(f"Failed to download {len(failed)} assets", "error")
                        if not continue_on_error and config.strict_mode:
                            sys.exit(1)


@cli.command()
@click.option('--update-keys', is_flag=True,
              help='Update decryption keys before decrypting')
@click.option('--weapons', type=str,
              help='Comma-separated list of specific weapons to decrypt')
@click.option('--parallel', is_flag=True, default=True,
              help='Use parallel processing for decryption')
@click.option('--validate', is_flag=True,
              help='Validate decrypted files after processing')
@click.pass_context
def decrypt_assets(ctx: click.Context, update_keys: bool, weapons: str | None,
                  parallel: bool, validate: bool) -> None:
    """Decrypt downloaded assets using fetched keys.

    Uses XOR decryption with MD5-derived keys to decrypt Unity assets
    into usable format for unpacking.
    """
    logger = ctx.obj['logger']
    config = ctx.obj['config']

    with error_handler("Asset decryption"):
        logger.print_banner()

        # Load weapon list
        processor = WeaponListProcessor(config)
        if weapons:
            weapon_list = [w.strip() for w in weapons.split(',')]
        else:
            try:
                weapon_list = processor.load_weapon_list()
            except Exception:
                logger.error("No weapon list found. Run 'download-weapons' first.")
                sys.exit(1)

        # Manage decryption keys
        key_manager = KeyManager(config)
        if update_keys:
            with logger.operation_context("key_update", "decryption key update"):
                keys = key_manager.fetch_keys_parallel(weapon_list)
                if keys:
                    key_manager.save_keys(keys)
                else:
                    logger.error("Failed to fetch any keys")
                    if config.strict_mode:
                        sys.exit(1)
        else:
            keys = key_manager.load_keys()

        if not keys:
            logger.error("No decryption keys found. Use --update-keys to fetch them.")
            sys.exit(1)

        # Decrypt assets
        with logger.operation_context("decryption", "asset decryption"):
            decryptor = AssetDecryptor(config)
            decrypted_files, failed_assets = decryptor.decrypt_all_assets(keys)

            logger.print_status(f"Decrypted {len(decrypted_files)} files successfully", "success")

            # Validate decrypted files if requested
            if validate:
                with logger.operation_context("validation", "file validation"):
                    validation_failures = _validate_decrypted_files(decrypted_files, logger)
                    if validation_failures:
                        logger.warning(f"Validation failed for {len(validation_failures)} files")

            if failed_assets:
                logger.print_status(f"Failed to decrypt {len(failed_assets)} assets", "error")
                if config.strict_mode:
                    sys.exit(1)


@cli.command()
@click.option('--input-dir', type=click.Path(exists=True, path_type=Path),
              help='Input directory containing decrypted assets')
@click.option('--output-dir', type=click.Path(path_type=Path),
              help='Output directory for unpacked files')
@click.option('--asset-filter', type=str,
              help='Filter assets by name pattern (supports wildcards)')
@click.option('--extract-types', type=str, default="Texture2D,Mesh,Material",
              help='Comma-separated list of Unity object types to extract')
@click.pass_context
def unpack_assets(ctx: click.Context, input_dir: Path | None, output_dir: Path | None,
                 asset_filter: str | None, extract_types: str) -> None:
    """Unpack decrypted Unity assets to extract models and textures.

    Extracts 3D models, textures, and materials from decrypted Unity asset files
    into standard formats (OBJ, PNG, etc.).
    """
    logger = ctx.obj['logger']
    config = ctx.obj['config']

    with error_handler("Asset unpacking"):
        logger.print_banner()

        # Set directories
        if input_dir is None:
            input_dir = config.decrypted_dir
        if output_dir is None:
            output_dir = config.base_dir / "runtime" / "unpacked"

        # Ensure paths are Path objects
        input_dir = Path(input_dir)
        output_dir = Path(output_dir)
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

        # Parse extract types
        extract_type_list = [t.strip() for t in extract_types.split(',')]

        # Unpack assets
        with logger.operation_context("unpacking", "asset unpacking"):
            unpacker = AssetUnpacker(config)
            results = unpacker.unpack_multiple_assets(
                asset_files,
                output_dir=output_dir,
                extract_types=extract_type_list
            )

            # Generate summary
            successful = sum(1 for files in results.values() if files)
            total_files = sum(len(files) for files in results.values())

            logger.print_status(f"Unpacked {successful}/{len(asset_files)} assets", "success")
            logger.print_status(f"Extracted {total_files} files to {output_dir}", "info")


@cli.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
@click.option('--recursive', '-r', is_flag=True, help='Process directories recursively')
@click.option('--backup', '-b', is_flag=True, help='Create backups before conversion')
@click.option('--pattern', default='*_n*.png', help='File pattern to match')
@click.option('--batch-size', type=int, default=10,
              help='Number of files to process in each batch')
@click.pass_context
def convert_normals(ctx: click.Context, path: Path, recursive: bool, backup: bool,
                   pattern: str, batch_size: int) -> None:
    """Convert Unity normal maps to standard format with batch processing.

    Converts Unity's normal map format (data in green/alpha channels) to
    standard format (data in red/green channels) for compatibility with
    other 3D applications.
    """
    logger = ctx.obj['logger']

    with error_handler("Normal map conversion"):
        logger.print_banner()

        converter = NormalMapConverter()

        with logger.operation_context("normal_conversion", "normal map conversion"):
            if path.is_file():
                output_path = converter.convert_normal_map(path)
                logger.print_status(f"Converted: {output_path}", "success")
            elif path.is_dir():
                converted_files = converter.batch_convert_directory(
                    path, recursive=recursive, pattern=pattern,
                    backup=backup, batch_size=batch_size
                )
                logger.print_status(f"Converted {len(converted_files)} normal maps", "success")
            else:
                logger.error(f"Invalid path: {path}")
                sys.exit(1)


@cli.command()
@click.option('--update-keys', is_flag=True, help='Update keys before processing')
@click.option('--skip-download', is_flag=True, help='Skip downloading assets')
@click.option('--skip-decrypt', is_flag=True, help='Skip decryption step')
@click.option('--convert-normals', is_flag=True, help='Convert normal maps after unpacking')
@click.option('--batch-size', type=int, default=50, help='Batch size for processing')
@click.option('--continue-on-error', is_flag=True, help='Continue pipeline on non-critical errors')
@click.pass_context
def full_pipeline(ctx: click.Context, update_keys: bool, skip_download: bool,
                  skip_decrypt: bool, convert_normals: bool, batch_size: int,
                  continue_on_error: bool) -> None:
    """Run the complete WOG Dump pipeline with enhanced error handling.

    Executes the full extraction pipeline: download weapon list → download assets
    → decrypt assets → unpack assets → (optionally) convert normal maps.

    This is the recommended way to use WOG Dump for complete model extraction.
    """
    logger = ctx.obj['logger']
    config = ctx.obj['config']

    with error_handler("Full pipeline"):
        logger.print_banner()
        logger.print_status("Starting complete WOG Dump pipeline...", "processing")

        pipeline_steps = [
            ("Download weapon list", not skip_download),
            ("Download assets", not skip_download),
            ("Decrypt assets", not skip_decrypt),
            ("Unpack assets", True),
            ("Convert normal maps", convert_normals),
        ]

        # Show pipeline overview
        logger.console.print("\n[bold cyan]Pipeline Overview:[/bold cyan]")
        for i, (step_name, enabled) in enumerate(pipeline_steps, 1):
            status = "✓" if enabled else "⏭"
            logger.console.print(f"  {i}. {status} {step_name}")
        logger.console.print()

        try:
            # Step 1: Download weapon list
            if not skip_download:
                logger.print_status("Step 1: Downloading weapon list...", "processing")
                ctx.invoke(download_weapons, force=False, validate=True)

            # Step 2: Download assets
            if not skip_download:
                logger.print_status("Step 2: Downloading assets...", "processing")
                ctx.invoke(download_assets, update_keys=update_keys, check_only=False,
                          weapons=None, batch_size=batch_size, continue_on_error=continue_on_error)

            # Step 3: Decrypt assets
            if not skip_decrypt:
                logger.print_status("Step 3: Decrypting assets...", "processing")
                ctx.invoke(decrypt_assets, update_keys=update_keys, weapons=None,
                          parallel=True, validate=config.enable_validation)

            # Step 4: Unpack assets
            logger.print_status("Step 4: Unpacking assets...", "processing")
            ctx.invoke(unpack_assets, input_dir=None, output_dir=None,
                      asset_filter=None, extract_types="Texture2D,Mesh,Material")

            # Step 5: Convert normal maps (optional)
            if convert_normals:
                logger.print_status("Step 5: Converting normal maps...", "processing")
                unpacked_dir = config.base_dir / "runtime" / "unpacked"
                if unpacked_dir.exists():
                    ctx.invoke(convert_normals, path=unpacked_dir, recursive=True,
                              backup=False, pattern="*_n*.png", batch_size=batch_size)

            # Show performance summary
            logger.print_performance_summary()

            logger.print_status("Pipeline completed successfully!", "success")
            logger.console.print("\n🎉 [bold green]All done! Your extracted models are ready to use.[/bold green]")

        except OperationCancelled:
            logger.print_status("Pipeline cancelled by user", "warning")
            sys.exit(130)
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            if not continue_on_error:
                sys.exit(1)


@cli.command()
@click.option('--show-performance', is_flag=True, help='Show performance metrics')
@click.option('--validate-files', is_flag=True, help='Validate existing files')
@click.pass_context
def info(ctx: click.Context, show_performance: bool, validate_files: bool) -> None:
    """Show comprehensive configuration and status information.

    Displays current configuration, file status, and optionally performance
    metrics and file validation results.
    """
    logger = ctx.obj['logger']
    config = ctx.obj['config']

    logger.print_banner()

    # Configuration info
    config_data = [
        ["Base Directory", str(config.base_dir)],
        ["Assets Directory", str(config.assets_dir)],
        ["Encrypted Directory", str(config.encrypted_dir)],
        ["Decrypted Directory", str(config.decrypted_dir)],
        ["Max Threads", str(config.max_threads)],
        ["Chunk Size", f"{config.chunk_size // 1024} KB"],
        ["Strict Mode", "Enabled" if config.strict_mode else "Disabled"],
    ]

    logger.print_table("Configuration", ["Setting", "Value"], config_data)

    # Status info
    status_data = _collect_status_info(config)
    logger.print_table("Status", ["Component", "Status"], status_data)

    # Performance metrics
    if show_performance:
        logger.print_performance_summary()

    # File validation
    if validate_files:
        _perform_file_validation(config, logger)


def _collect_status_info(config: WOGConfig) -> list[list[str]]:
    """Collect system status information."""
    status_data = []

    # Check weapon list
    weapons_exist = config.weapons_file.exists()
    if weapons_exist:
        try:
            processor = WeaponListProcessor(config)
            weapons = processor.load_weapon_list()
            weapons_status = f"{len(weapons)} weapons"
        except Exception:
            weapons_status = "Error loading"
    else:
        weapons_status = "Not found"

    status_data.append(["Weapon List", weapons_status])

    # Check keys
    keys_exist = config.keys_file.exists()
    if keys_exist:
        try:
            key_manager = KeyManager(config)
            keys = key_manager.load_keys()
            keys_status = f"{len(keys)} keys"
        except Exception:
            keys_status = "Error loading"
    else:
        keys_status = "Not found"

    status_data.append(["Decryption Keys", keys_status])

    # Check assets
    assets = list(config.assets_dir.glob("*.unity3d"))
    status_data.append(["Downloaded Assets", f"{len(assets)} files"])

    # Check decrypted
    decrypted = list(config.decrypted_dir.glob("*.unity3d"))
    status_data.append(["Decrypted Assets", f"{len(decrypted)} files"])

    # Check unpacked
    unpacked_dir = config.base_dir / "runtime" / "unpacked"
    if unpacked_dir.exists():
        unpacked_count = sum(1 for _ in unpacked_dir.rglob("*") if _.is_file())
        status_data.append(["Unpacked Files", f"{unpacked_count} files"])
    else:
        status_data.append(["Unpacked Files", "0 files"])

    return status_data


def _validate_decrypted_files(files: list[Path], logger) -> list[Path]:
    """Validate decrypted files and return list of validation failures."""
    validation_failures = []

    for file_path in files:
        try:
            # Basic validation - check if file exists and is not empty
            if not file_path.exists():
                validation_failures.append(file_path)
                continue

            if file_path.stat().st_size == 0:
                validation_failures.append(file_path)
                continue

            # Try to load as Unity asset for additional validation
            try:
                env = UnityPy.load(str(file_path))
                if not env.objects:
                    validation_failures.append(file_path)
            except Exception:
                validation_failures.append(file_path)

        except Exception:
            validation_failures.append(file_path)

    return validation_failures


def _perform_file_validation(config: WOGConfig, logger) -> None:
    """Perform comprehensive file validation."""
    logger.console.print("\n[bold]File Validation Results:[/bold]")

    # Validate decrypted files
    decrypted_files = list(config.decrypted_dir.glob("*.unity3d"))
    if decrypted_files:
        validation_failures = _validate_decrypted_files(decrypted_files, logger)
        valid_count = len(decrypted_files) - len(validation_failures)
        logger.console.print(f"  Decrypted Assets: {valid_count}/{len(decrypted_files)} valid")

        if validation_failures and logger.logger.level <= 20:  # INFO level
            logger.console.print("  Invalid files:")
            for file_path in validation_failures[:5]:  # Show first 5
                logger.console.print(f"    - {file_path.name}")
            if len(validation_failures) > 5:
                logger.console.print(f"    ... and {len(validation_failures) - 5} more")


def main() -> None:
    """Main entry point with enhanced error handling."""
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
        if logger.logger.level <= 10:  # DEBUG level
            logger.logger.exception("Full traceback:")
        sys.exit(1)


if __name__ == "__main__":
    main()
