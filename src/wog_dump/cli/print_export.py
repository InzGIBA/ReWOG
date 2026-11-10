"""print-export command: slicer-ready STLs with per-part diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..core.config import get_config
from .errors import error_handler, print_banner_if_standalone


@click.command("print-export")
@click.argument("weapons", nargs=-1, required=True)
@click.option(
    "--part",
    "parts",
    multiple=True,
    metavar="NAME",
    help="Export only the named parts (GLB node names, repeatable)",
)
@click.option(
    "--merge",
    is_flag=True,
    default=False,
    help="One combined STL with parts laid out side by side",
)
@click.option(
    "--spacing",
    type=float,
    default=5.0,
    show_default=True,
    metavar="MM",
    help="Gap between parts in --merge layout",
)
@click.pass_context
def print_export(
    ctx: click.Context,
    weapons: tuple[str, ...],
    parts: tuple[str, ...],
    merge: bool,
    spacing: float,
) -> None:
    """Prepare slicer-ready STLs with print diagnostics.

    Every real part is checked and repaired (vertex merge, degenerate
    and duplicate faces, hole filling) with trimesh; statuses land in
    print_report.json and the console. Coordinates are millimeters at
    true scale from the scene.

    \b
    Examples:
        wog-dump print-export ak47
        wog-dump print-export ak47 --part bolt --part receiver
        wog-dump print-export psm --merge --spacing 3
    """
    logger = ctx.obj["logger"]

    with error_handler("Print export"):
        print_banner_if_standalone(ctx)
        config = get_config()
        try:
            from ..core.printprep import prepare_print
        except ImportError:
            logger.error(
                "The print pipeline needs the print extra: pip install wog-dump[print]"
            )
            sys.exit(1)

        scenes_dir = config.base_dir / "runtime" / "scenes"
        prepared = 0
        for weapon in weapons:
            glb_path = scenes_dir / weapon / f"{weapon}.glb"
            if not glb_path.exists():
                logger.error(
                    f"Scene not found for {weapon}: {glb_path} (run build-scene)"
                )
                continue
            output_dir = scenes_dir / weapon / "print"
            wanted = set(parts) if parts else None
            try:
                report = prepare_print(
                    glb_path, output_dir, wanted, merged=merge, spacing_mm=spacing
                )
            except ValueError as e:
                logger.error(f"Print export failed for {weapon}: {e}")
                continue

            statuses = [part["status"] for part in report["parts"]]
            summary = ", ".join(
                f"{statuses.count(s)} {s}"
                for s in ("ok", "fixed", "warnings")
                if s in statuses
            )
            logger.print_status(
                f"Print export {weapon}: {len(statuses)} parts ({summary}) -> {output_dir}",
                "success",
            )
            for part in report["parts"]:
                if part["status"] == "warnings":
                    logger.print_status(
                        f"  {part['name']}: {', '.join(part['notes'])}", "warning"
                    )
            prepared += 1

        if not prepared:
            logger.error("No print exports completed")
            sys.exit(1)
