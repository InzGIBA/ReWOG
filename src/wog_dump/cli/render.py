"""Render command: turntable previews through headless Blender."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..core.config import get_config
from ..core.render import RenderError, RenderManager, RenderOptions
from .errors import error_handler, print_banner_if_standalone


def _collect_weapons(weapons: tuple[str, ...], render_all: bool) -> list[str]:
    """Resolve the weapon list; --all sweeps assembled scenes only."""
    if render_all:
        scenes_dir = get_config().base_dir / "runtime" / "scenes"
        if not scenes_dir.exists():
            return []
        return sorted(
            path.parent.name
            for path in scenes_dir.glob("*/*.glb")
            if path.stem == path.parent.name
        )
    return list(weapons)


@click.command("render")
@click.argument("weapons", nargs=-1)
@click.option(
    "--all",
    "render_all",
    is_flag=True,
    default=False,
    help="Render every weapon that has a built scene",
)
@click.option(
    "--angles", type=int, default=8, show_default=True, help="Views around the weapon"
)
@click.option(
    "--size",
    type=int,
    default=1280,
    show_default=True,
    help="Square image size in pixels",
)
@click.option(
    "--elevation",
    type=float,
    default=15.0,
    show_default=True,
    help="Camera height angle in degrees",
)
@click.option(
    "--image-format",
    "image_format",
    type=click.Choice(("png", "jpg", "webp")),
    default="png",
    show_default=True,
    help="Frame format; PNG renders on a transparent background",
)
@click.option(
    "--no-gif",
    "no_gif",
    is_flag=True,
    default=False,
    help="Skip the preview.gif assembled from the frames",
)
@click.pass_context
def render(
    ctx: click.Context,
    weapons: tuple[str, ...],
    render_all: bool,
    angles: int,
    size: int,
    elevation: float,
    image_format: str,
    no_gif: bool,
) -> int:
    """Render turntable previews for built scenes via headless Blender.

    Writes view_01.png ... view_NN.png plus preview.gif into
    runtime/scenes/<weapon>/previews/.

    \b
    Examples:
        wog-dump render ak47
        wog-dump render ak74n --angles 12 --size 1600
        wog-dump render --all --image-format webp
    """
    logger = ctx.obj["logger"]

    with error_handler("Preview render"):
        print_banner_if_standalone(ctx)

        try:
            options = RenderOptions(
                angles=angles,
                size=size,
                elevation=elevation,
                image_format=image_format,
                gif=not no_gif,
            )
        except RenderError as e:
            logger.error(str(e))
            sys.exit(1)

        weapon_list = _collect_weapons(weapons, render_all)
        if not weapon_list:
            logger.error(
                "No weapons requested (pass names or --all); "
                "scenes come from runtime/scenes/"
            )
            sys.exit(1)

        manager = RenderManager()
        rendered = 0
        for weapon in weapon_list:
            try:
                frames = manager.render(weapon, options)
                rendered += 1
                logger.print_status(
                    f"Previews for {weapon}: {len(frames)} frames in "
                    f"{manager.scenes_dir(weapon) / 'previews'}",
                    "success",
                )
            except RenderError as e:
                logger.error(f"Render failed for {weapon}: {e}")

        if not rendered:
            logger.error("No previews rendered")
            sys.exit(1)

    return rendered
