"""Full pipeline command for the WOG Dump CLI."""

from __future__ import annotations

import sys
import time

import click

from ..utils.logging import format_duration
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
@click.option(
    "--performance", is_flag=True, help="Print an aggregated timing breakdown"
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
    performance: bool,
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
        # This command owns the step-by-step console output; the invoked
        # subcommands stay quiet and report results through return values
        ctx.obj["pipeline_nested"] = True

        flow = ["weapon list", "download", "decrypt", "unpack"]
        if convert_normals:
            flow.append("normal maps")
        logger.console.print(f"[dim]{' → '.join(flow)}[/dim]\n")

        started = time.perf_counter()
        output_dir = config.base_dir / "runtime" / "unpacked"
        had_step_failure = False

        try:
            if not skip_download:
                with logger.step("Weapon list") as step:
                    weapons = ctx.invoke(download_weapons, force=False, validate=True)
                    step.result(f"{len(weapons)} weapons")

                with logger.step("Download") as step:
                    successful, failed, missing = ctx.invoke(
                        download_assets,
                        update_keys=update_keys,
                        check_only=False,
                        weapons=None,
                        batch_size=batch_size,
                        continue_on_error=continue_on_error,
                    )
                    detail = f"{len(successful)} assets"
                    if missing:
                        detail += f", {missing} not on server"
                    if failed:
                        detail += f", {len(failed)} failed"
                    step.result(detail)

            if not skip_decrypt:
                with logger.step("Decrypt") as step:
                    decrypted, failed_assets = ctx.invoke(
                        decrypt_assets,
                        # Keys were just updated by the download step;
                        # fetch them again only when that step was skipped
                        update_keys=update_keys and skip_download,
                        weapons=None,
                        validate=config.enable_validation,
                    )
                    detail = f"{len(decrypted)} files"
                    if failed_assets:
                        detail += f", {len(failed_assets)} failed"
                    step.result(detail)

            with logger.step("Unpack") as step:
                extracted = ctx.invoke(
                    unpack_assets,
                    input_dir=None,
                    output_dir=None,
                    asset_filter=None,
                    extract_types="Texture2D,Mesh,Material",
                )
                step.result(f"{extracted} files")

            if convert_normals:
                with logger.step("Normal maps") as step:
                    converted = ctx.invoke(
                        convert_normals_command,
                        path=output_dir,
                        recursive=True,
                        backup=False,
                        pattern="*.png",
                    )
                    step.result(f"{converted} converted")

        except OperationCancelled:
            logger.print_status("Pipeline cancelled by user", "warning")
            sys.exit(130)
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            if not continue_on_error:
                sys.exit(1)
            # Keep going, but never report a clean finish afterwards
            had_step_failure = True

        logger.console.print()
        if had_step_failure:
            logger.print_status(
                "Pipeline finished with errors (see wog.log for details)",
                "warning",
            )
        logger.console.print(
            f"[bold green]Done[/bold green]"
            f" [dim]in {format_duration(time.perf_counter() - started)}"
            f" — models in {output_dir}[/dim]"
        )

        if performance:
            logger.print_performance_summary()

        if had_step_failure:
            sys.exit(1)
