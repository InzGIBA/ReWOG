"""print-export command: slicer-ready STLs with per-part diagnostics."""

from __future__ import annotations

import sys
from typing import Any

import click

from wog_dump.cli.errors import error_handler, print_banner_if_standalone
from wog_dump.core.config import get_config
from wog_dump.utils.logging import get_logger


def _parse_bed_size(value: str) -> tuple[float, float, float]:
    """Parse ``XxYxZ`` millimeters into a positive float triple."""
    parts = value.lower().split("x")
    if len(parts) != 3:
        raise click.BadParameter("expected XxYxZ, e.g. 220x220x250")
    try:
        bed = tuple(float(p) for p in parts)
    except ValueError:
        raise click.BadParameter("expected XxYxZ, e.g. 220x220x250") from None
    if any(v <= 0 for v in bed):
        raise click.BadParameter("bed dimensions must be positive")
    return bed[0], bed[1], bed[2]


def _format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {int(seconds % 60):02d}s"


def _print_estimate_table(report: dict[str, Any], weapon: str) -> None:
    """Rich per-part volume/mass/time/cost table with a totals row."""
    from rich.console import Console
    from rich.table import Table

    estimate = report.get("estimate")
    if not estimate:
        return
    table = Table(title=f"Print estimate: {weapon}")
    table.add_column("Part", style="cyan", no_wrap=True)
    table.add_column("Volume (cm3)", justify="right")
    table.add_column("Mass (g)", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Cost", justify="right")
    for part in report["parts"]:
        values = part.get("estimate")
        if not values:
            continue
        table.add_row(
            part["name"],
            f"{values['volume_cm3']:.2f}",
            f"{values['mass_g']:.1f}",
            _format_duration(values["time_s"]),
            f"{values['cost']:.2f}",
        )
    totals = estimate["totals"]
    table.add_row(
        "TOTAL",
        f"{totals['volume_cm3']:.2f}",
        f"{totals['mass_g']:.1f}",
        _format_duration(totals["time_s"]),
        f"{totals['cost']:.2f}",
        style="bold",
    )
    params = estimate["parameters"]
    table.caption = (
        f"density {params['density_g_cm3']:g} g/cm3, "
        f"flow {params['flow_rate_mm3_s']:g} mm3/s, "
        f"filament {params['filament_price_per_kg']:g}/kg "
        "(rough heuristic, not a slicer)"
    )
    Console().print(table)


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
@click.option(
    "--orient",
    is_flag=True,
    default=False,
    help="Auto-orient each part: largest supporting face on the bed",
)
@click.option(
    "--bed-size",
    "bed_size",
    metavar="XxYxZ",
    default=None,
    help="Split parts larger than the bed in X/Y (mm), e.g. 220x220x250",
)
@click.option(
    "--estimate",
    is_flag=True,
    default=False,
    help="Estimate volume, mass, print time and filament cost",
)
@click.option(
    "--density",
    type=float,
    default=1.24,
    show_default=True,
    metavar="G_CM3",
    help="Material density for --estimate (g/cm3, PLA is 1.24)",
)
@click.option(
    "--flow-rate",
    type=float,
    default=5.0,
    show_default=True,
    metavar="MM3_S",
    help="Extrusion flow for the --estimate time heuristic (mm3/s)",
)
@click.option(
    "--filament-price",
    type=float,
    default=25.0,
    show_default=True,
    metavar="PER_KG",
    help="Filament price per kg for the --estimate cost",
)
@click.pass_context
def print_export(
    ctx: click.Context,
    weapons: tuple[str, ...],
    parts: tuple[str, ...],
    merge: bool,
    spacing: float,
    orient: bool,
    bed_size: str | None,
    estimate: bool,
    density: float,
    flow_rate: float,
    filament_price: float,
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
        wog-dump print-export ak47 --orient --bed-size 220x220x250 --estimate
    """
    logger = (ctx.obj or {}).get("logger") or get_logger()

    bed: tuple[float, float, float] | None = None
    if bed_size is not None:
        bed = _parse_bed_size(bed_size)
    if density <= 0 or flow_rate <= 0:
        raise click.BadParameter("--density and --flow-rate must be positive")
    if filament_price < 0:
        raise click.BadParameter("--filament-price must not be negative")

    with error_handler("Print export"):
        print_banner_if_standalone(ctx)
        config = get_config()
        try:
            from .printprep import prepare_print
        except ImportError:
            logger.error(
                "The print pipeline needs trimesh — reinstall the plugin: "
                "pip install wog-plugin-print"
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
                    glb_path,
                    output_dir,
                    wanted,
                    merged=merge,
                    spacing_mm=spacing,
                    orient=orient,
                    bed_size_mm=bed,
                    estimate=estimate,
                    density_g_cm3=density,
                    flow_rate_mm3_s=flow_rate,
                    filament_price_per_kg=filament_price,
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
                f"Print export {weapon}: {len(statuses)} parts ({summary})"
                f" -> {output_dir}",
                "success",
            )
            for part in report["parts"]:
                if part["status"] == "warnings":
                    logger.print_status(
                        f"  {part['name']}: {', '.join(part['notes'])}", "warning"
                    )
                split = part.get("split")
                if split and split["was_split"]:
                    logger.print_status(
                        f"  {part['name']}: split into {len(split['chunks'])} chunks",
                        "info",
                    )
                if split and split["z_overflow"]:
                    logger.print_status(
                        f"  {part['name']}: exceeds bed Z, not split", "warning"
                    )
            if estimate:
                _print_estimate_table(report, weapon)
            prepared += 1

        if not prepared:
            logger.error("No print exports completed")
            sys.exit(1)
