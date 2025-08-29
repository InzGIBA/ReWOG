"""Modern CLI interface for WOG Dump using Click."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..core.config import get_config, set_config
from ..core.decrypt import AssetDecryptor, KeyManager
from ..core.download import DownloadManager
from ..core.unpack import AssetUnpacker, WeaponListProcessor
from ..utils.logging import get_logger, set_log_level
from ..utils.normal_map import NormalMapConverter


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.option('--debug', '-d', is_flag=True, help='Enable debug logging')
@click.option('--config-dir', type=click.Path(path_type=Path), 
              help='Custom configuration directory')
@click.option('--max-threads', type=int, help='Maximum number of threads')
@click.version_option(version='2.2.0', prog_name='WOG Dump')
@click.pass_context
def cli(ctx: click.Context, verbose: bool, debug: bool, 
        config_dir: Path | None, max_threads: int | None) -> None:
    """WOG Dump - Modern tool for extracting 3D models from World of Guns: Gun Disassembly.
    
    This tool allows you to download, decrypt, and unpack 3D gun models from the game
    World of Guns: Gun Disassembly. It provides a modern Python 3.12+ interface with
    comprehensive error handling and progress tracking.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)
    
    # Set up logging
    logger = get_logger()
    if debug:
        set_log_level(10)  # DEBUG
        logger.info("Debug logging enabled")
    elif verbose:
        set_log_level(20)  # INFO
    else:
        set_log_level(30)  # WARNING
    
    # Update configuration
    config_updates = {}
    if config_dir:
        config_updates['base_dir'] = config_dir
    if max_threads:
        config_updates['max_threads'] = max_threads
    
    if config_updates:
        set_config(**config_updates)
    
    # Store context
    ctx.obj['config'] = get_config()
    ctx.obj['logger'] = logger


@cli.command()
@click.option('--force', '-f', is_flag=True, 
              help='Force download even if file exists and is up to date')
@click.pass_context
def download_weapons(ctx: click.Context, force: bool) -> None:
    """Download and extract the weapon list from the game servers."""
    logger = ctx.obj['logger']
    config = ctx.obj['config']
    
    try:
        logger.print_banner()
        logger.print_status("Starting weapon list download...", "info")
        
        with DownloadManager(config) as downloader:
            # Download weapon list asset
            asset_path = downloader.download_weapon_list()
            
            # Process and extract weapon list
            processor = WeaponListProcessor(config)
            weapon_list = processor.process_weapon_list_asset(asset_path)
            
            logger.print_status(f"Successfully extracted {len(weapon_list)} weapons", "success")
            
            # Show some examples
            if weapon_list:
                logger.console.print("\nFirst 10 weapons:")
                for i, weapon in enumerate(weapon_list[:10], 1):
                    logger.console.print(f"  {i}. {weapon}")
                
                if len(weapon_list) > 10:
                    logger.console.print(f"  ... and {len(weapon_list) - 10} more")
            
    except Exception as e:
        logger.error(f"Failed to download weapons: {e}")
        sys.exit(1)


@cli.command()
@click.option('--update-keys', is_flag=True, 
              help='Update decryption keys before downloading')
@click.option('--check-only', is_flag=True,
              help='Only check for updates, do not download')
@click.option('--weapons', type=str, 
              help='Comma-separated list of specific weapons to download')
@click.pass_context
def download_assets(ctx: click.Context, update_keys: bool, check_only: bool, weapons: str | None) -> None:
    """Download weapon assets from the game servers."""
    logger = ctx.obj['logger']
    config = ctx.obj['config']
    
    try:
        logger.print_banner()
        
        # Load weapon list
        try:
            processor = WeaponListProcessor(config)
            if weapons:
                weapon_list = [w.strip() for w in weapons.split(',')]
                logger.info(f"Using custom weapon list: {len(weapon_list)} weapons")
            else:
                weapon_list = processor.load_weapon_list()
        except Exception:
            logger.error("No weapon list found. Run 'download-weapons' first.")
            sys.exit(1)
        
        # Update keys if requested
        if update_keys:
            logger.print_status("Updating decryption keys...", "info")
            key_manager = KeyManager(config)
            keys = key_manager.fetch_keys_parallel(weapon_list)
            key_manager.save_keys(keys)
        
        # Download assets
        with DownloadManager(config) as downloader:
            if check_only:
                to_download = downloader.check_for_updates(weapon_list)
                logger.print_status(f"Found {len(to_download)} assets to download", "info")
                
                if to_download:
                    logger.console.print("\nAssets needing updates:")
                    for weapon in to_download[:20]:  # Show first 20
                        logger.console.print(f"  • {weapon}")
                    if len(to_download) > 20:
                        logger.console.print(f"  ... and {len(to_download) - 20} more")
            else:
                successful, failed = downloader.download_assets(weapon_list)
                
                if successful:
                    logger.print_status(f"Downloaded {len(successful)} assets successfully", "success")
                
                if failed:
                    logger.print_status(f"Failed to download {len(failed)} assets", "error")
                    logger.print_error_summary([f"Failed: {weapon}" for weapon in failed])
    
    except Exception as e:
        logger.error(f"Asset download failed: {e}")
        sys.exit(1)


@cli.command()
@click.option('--update-keys', is_flag=True,
              help='Update decryption keys before decrypting')
@click.option('--weapons', type=str,
              help='Comma-separated list of specific weapons to decrypt')
@click.pass_context
def decrypt_assets(ctx: click.Context, update_keys: bool, weapons: str | None) -> None:
    """Decrypt downloaded assets using fetched keys."""
    logger = ctx.obj['logger']
    config = ctx.obj['config']
    
    try:
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
        
        # Update keys if requested
        key_manager = KeyManager(config)
        if update_keys:
            logger.print_status("Updating decryption keys...", "info")
            keys = key_manager.fetch_keys_parallel(weapon_list)
            key_manager.save_keys(keys)
        else:
            keys = key_manager.load_keys()
        
        if not keys:
            logger.error("No decryption keys found. Use --update-keys to fetch them.")
            sys.exit(1)
        
        # Decrypt assets
        decryptor = AssetDecryptor(config)
        decrypted_files, failed_assets = decryptor.decrypt_all_assets(keys)
        
        logger.print_status(f"Decrypted {len(decrypted_files)} files successfully", "success")
        
        if failed_assets:
            logger.print_status(f"Failed to decrypt {len(failed_assets)} assets", "error")
            logger.print_error_summary([f"Failed: {asset}" for asset in failed_assets])
    
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        sys.exit(1)


@cli.command()
@click.option('--input-dir', type=click.Path(exists=True, path_type=Path),
              help='Input directory containing decrypted assets')
@click.option('--output-dir', type=click.Path(path_type=Path),
              help='Output directory for unpacked files')
@click.pass_context
def unpack_assets(ctx: click.Context, input_dir: Path | None, output_dir: Path | None) -> None:
    """Unpack decrypted Unity assets to extract models and textures."""
    logger = ctx.obj['logger']
    config = ctx.obj['config']
    
    try:
        logger.print_banner()
        
        # Set directories
        if input_dir is None:
            input_dir = config.decrypted_dir
        if output_dir is None:
            output_dir = config.base_dir / "runtime" / "unpacked"
        
        # Ensure directories are Path objects
        if not isinstance(output_dir, Path):
            output_dir = Path(str(output_dir))
        if not isinstance(input_dir, Path):
            input_dir = Path(str(input_dir))
            
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Find decrypted assets
        asset_files = list(input_dir.glob("*.unity3d"))
        
        if not asset_files:
            logger.error(f"No decrypted assets found in {input_dir}")
            sys.exit(1)
        
        logger.info(f"Found {len(asset_files)} assets to unpack")
        
        # Unpack assets
        unpacker = AssetUnpacker(config)
        results = unpacker.unpack_multiple_assets(asset_files)
        
        # Summary
        successful = sum(1 for files in results.values() if files)
        total_files = sum(len(files) for files in results.values())
        
        logger.print_status(f"Unpacked {successful}/{len(asset_files)} assets", "success")
        logger.print_status(f"Extracted {total_files} files to {output_dir}", "info")
    
    except Exception as e:
        logger.error(f"Unpacking failed: {e}")
        sys.exit(1)


@cli.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
@click.option('--recursive', '-r', is_flag=True, help='Process directories recursively')
@click.option('--backup', '-b', is_flag=True, help='Create backups before conversion')
@click.option('--pattern', default='*_n*.png', help='File pattern to match')
@click.pass_context
def convert_normals(ctx: click.Context, path: Path, recursive: bool, backup: bool, pattern: str) -> None:
    """Convert Unity normal maps to standard format."""
    logger = ctx.obj['logger']
    
    try:
        logger.print_banner()
        
        converter = NormalMapConverter()
        
        if path.is_file():
            output_path = converter.convert_normal_map(path)
            logger.print_status(f"Converted: {output_path}", "success")
        elif path.is_dir():
            converted_files = converter.batch_convert_directory(
                path, recursive=recursive, pattern=pattern, backup=backup
            )
            logger.print_status(f"Converted {len(converted_files)} normal maps", "success")
        else:
            logger.error(f"Invalid path: {path}")
            sys.exit(1)
    
    except Exception as e:
        logger.error(f"Normal map conversion failed: {e}")
        sys.exit(1)


@cli.command()
@click.option('--update-keys', is_flag=True, help='Update keys before processing')
@click.option('--skip-download', is_flag=True, help='Skip downloading assets')
@click.option('--skip-decrypt', is_flag=True, help='Skip decryption step')
@click.option('--convert-normals', is_flag=True, help='Convert normal maps after unpacking')
@click.pass_context
def full_pipeline(ctx: click.Context, update_keys: bool, skip_download: bool, 
                  skip_decrypt: bool, convert_normals: bool) -> None:
    """Run the complete WOG Dump pipeline: download -> decrypt -> unpack."""
    logger = ctx.obj['logger']
    config = ctx.obj['config']
    
    try:
        logger.print_banner()
        logger.print_status("Starting full WOG Dump pipeline...", "info")
        
        # Step 1: Download weapon list
        logger.print_status("Step 1: Downloading weapon list...", "info")
        ctx.invoke(download_weapons)
        
        # Step 2: Download assets
        if not skip_download:
            logger.print_status("Step 2: Downloading assets...", "info")
            ctx.invoke(download_assets, update_keys=update_keys)
        
        # Step 3: Decrypt assets
        if not skip_decrypt:
            logger.print_status("Step 3: Decrypting assets...", "info")
            ctx.invoke(decrypt_assets, update_keys=update_keys)
        
        # Step 4: Unpack assets
        logger.print_status("Step 4: Unpacking assets...", "info")
        ctx.invoke(unpack_assets)
        
        # Step 5: Convert normal maps (optional)
        if convert_normals:
            logger.print_status("Step 5: Converting normal maps...", "info")
            unpacked_dir = config.base_dir / "runtime" / "unpacked"
            if unpacked_dir.exists():
                # Call the convert_normals command directly
                from wog_dump.utils.normal_map import NormalMapConverter
                converter = NormalMapConverter()
                converted_files = converter.batch_convert_directory(
                    unpacked_dir, recursive=True, backup=False
                )
                logger.print_status(f"Converted {len(converted_files)} normal maps", "success")
        
        logger.print_status("Pipeline completed successfully!", "success")
        logger.console.print("\n🎉 All done! Your extracted models are ready to use.")
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show configuration and status information."""
    logger = ctx.obj['logger']
    config = ctx.obj['config']
    
    logger.print_banner()
    
    # Configuration info
    config_data = [
        ["Setting", "Value"],
        ["Base Directory", str(config.base_dir)],
        ["Assets Directory", str(config.assets_dir)],
        ["Encrypted Directory", str(config.encrypted_dir)],
        ["Decrypted Directory", str(config.decrypted_dir)],
        ["Max Threads", str(config.max_threads)],
    ]
    
    logger.print_table("Configuration", ["Setting", "Value"], config_data[1:])
    
    # Status info
    status_data = []
    
    # Check weapon list
    weapons_exist = config.weapons_file.exists()
    if weapons_exist:
        try:
            processor = WeaponListProcessor(config)
            weapons = processor.load_weapon_list()
            weapons_count = len(weapons)
        except:
            weapons_count = "Error loading"
    else:
        weapons_count = "Not found"
    
    status_data.append(["Weapon List", f"{weapons_count} weapons" if isinstance(weapons_count, int) else weapons_count])
    
    # Check keys
    keys_exist = config.keys_file.exists()
    if keys_exist:
        try:
            key_manager = KeyManager(config)
            keys = key_manager.load_keys()
            keys_count = len(keys)
        except:
            keys_count = "Error loading"
    else:
        keys_count = "Not found"
    
    status_data.append(["Decryption Keys", f"{keys_count} keys" if isinstance(keys_count, int) else keys_count])
    
    # Check assets
    assets = list(config.assets_dir.glob("*.unity3d"))
    status_data.append(["Downloaded Assets", f"{len(assets)} files"])
    
    # Check decrypted
    decrypted = list(config.decrypted_dir.glob("*.unity3d"))
    status_data.append(["Decrypted Assets", f"{len(decrypted)} files"])
    
    logger.print_table("Status", ["Component", "Status"], status_data)


def main() -> None:
    """Main entry point for the CLI."""
    try:
        cli()
    except KeyboardInterrupt:
        logger = get_logger()
        logger.print_status("Operation cancelled by user", "warning")
        sys.exit(130)
    except Exception as e:
        logger = get_logger()
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()