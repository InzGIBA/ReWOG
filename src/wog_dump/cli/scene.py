"""Build-scene command for the WOG Dump CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..core.scene import SceneBuilder, SceneExportError
from .errors import error_handler, print_banner_if_standalone

EXTRA_CHOICES = ("arrows", "cut", "vfx", "all")


def _parse_extras(extras: tuple[str, ...]) -> frozenset[str]:
    """Expand --extras choices into a category set ('all' selects everything)."""
    selected: set[str] = set()
    for choice in extras:
        if choice == "all":
            selected.update(("arrows", "cut", "vfx"))
        else:
            selected.add(choice)
    return frozenset(selected)


@click.command("build-scene")
@click.argument("weapons", nargs=-1, required=True)
@click.option(
    "--extras",
    type=click.Choice(EXTRA_CHOICES),
    multiple=True,
    help="Also export UI objects hidden in the game's assembled view: "
    "arrows (assembly guides), cut (cutaway meshes), vfx (muzzle flash/gas), "
    "or all. Repeatable.",
)
@click.option(
    "--unit-scale",
    type=float,
    default=0.01,
    show_default=True,
    help="Unity centimeters to glTF meters factor baked into the scene",
)
@click.option(
    "--input-dir",
    type=click.Path(exists=True, path_type=Path),
    help="Directory with decrypted bundles (default: runtime/decrypted)",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    help="Output directory (default: runtime/scenes/<weapon>)",
)
@click.pass_context
def build_scene(
    ctx: click.Context,
    weapons: tuple[str, ...],
    extras: tuple[str, ...],
    unit_scale: float,
    input_dir: Path | None,
    output_dir: Path | None,
) -> int:
    """Build Blender-ready glTF scenes from decrypted weapon bundles.

    Rebuilds the in-game assembled hierarchy (exact transforms), covers
    every part with its game materials/textures, adds a camera and
    studio lighting, and writes <weapon>.glb plus a blender_setup.py
    helper into runtime/scenes/<weapon>/.

    \b
    Examples:
        wog-dump build-scene ak47
        wog-dump build-scene ak47 colt_1911 --extras all
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    with error_handler("Scene build"):
        print_banner_if_standalone(ctx)

        builder = SceneBuilder(config)
        include_extras = _parse_extras(extras)

        exported = 0
        for weapon in weapons:
            try:
                glb_path = builder.build_scene(
                    weapon,
                    include_extras=include_extras,
                    unit_scale=unit_scale,
                    input_dir=input_dir,
                    output_dir=output_dir / weapon if output_dir else None,
                )
                exported += 1
                logger.print_status(f"Scene exported: {glb_path}", "success")
            except SceneExportError as e:
                logger.error(f"Scene build failed for {weapon}: {e}")

        if not exported:
            logger.error("No scenes exported")
            sys.exit(1)

    return exported
