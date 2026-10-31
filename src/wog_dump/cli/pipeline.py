"""Full pipeline command for the WOG Dump CLI."""

from __future__ import annotations

import sys

import click

from .convert import convert_normals as convert_normals_command
from .decrypt import decrypt_assets
from .download import download_assets, download_weapons
from .errors import OperationCancelled, error_handler
from .unpack import unpack_assets


@click.command()
@click.option("--update-keys", is_flag=True, help="Update keys before processing")
@click.option("--skip-download", is_flag=True, help="Skip downloading assets")
@click.option("--skip-decrypt", is_flag=True, help="Skip decryption step")
@click.option(
    "--convert-normals", is_flag=True, help="Convert normal maps after unpacking"
)
@click.option("--batch-size", type=int, default=50, help="Batch size for processing")
@click.option(
    "--continue-on-error", is_flag=True, help="Continue pipeline on non-critical errors"
)
@click.pass_context
def full_pipeline(
    ctx: click.Context,
    update_keys: bool,
    skip_download: bool,
    skip_decrypt: bool,
    convert_normals: bool,
    batch_size: int,
    continue_on_error: bool,
) -> None:
    """Run the complete WOG Dump pipeline.

    Executes the full extraction pipeline: download weapon list → download assets
    → decrypt assets → unpack assets → (optionally) convert normal maps.

    This is the recommended way to use WOG Dump for complete model extraction.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

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
                ctx.invoke(
                    download_assets,
                    update_keys=update_keys,
                    check_only=False,
                    weapons=None,
                    batch_size=batch_size,
                    continue_on_error=continue_on_error,
                )

            # Step 3: Decrypt assets
            if not skip_decrypt:
                logger.print_status("Step 3: Decrypting assets...", "processing")
                ctx.invoke(
                    decrypt_assets,
                    update_keys=update_keys,
                    weapons=None,
                    validate=config.enable_validation,
                )

            # Step 4: Unpack assets
            logger.print_status("Step 4: Unpacking assets...", "processing")
            ctx.invoke(
                unpack_assets,
                input_dir=None,
                output_dir=None,
                asset_filter=None,
                extract_types="Texture2D,Mesh,Material",
            )

            # Step 5: Convert normal maps (optional)
            if convert_normals:
                logger.print_status("Step 5: Converting normal maps...", "processing")
                unpacked_dir = config.base_dir / "runtime" / "unpacked"
                if unpacked_dir.exists():
                    ctx.invoke(
                        convert_normals_command,
                        path=unpacked_dir,
                        recursive=True,
                        backup=False,
                        pattern="*_n*.png",
                    )

            # Show performance summary
            logger.print_performance_summary()

            logger.print_status("Pipeline completed successfully!", "success")
            logger.console.print(
                "\n🎉 [bold green]All done! Your extracted models are"
                " ready to use.[/bold green]"
            )

        except OperationCancelled:
            logger.print_status("Pipeline cancelled by user", "warning")
            sys.exit(130)
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            if not continue_on_error:
                sys.exit(1)
