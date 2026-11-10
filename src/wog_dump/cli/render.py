"""Render command: turntable previews through headless Blender."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..core.config import get_config
from ..core.render import (
    RenderError,
    RenderManager,
    RenderOptions,
    VideoOptions,
)
from ..utils.i18n import t
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
@click.option(
    "--video",
    is_flag=True,
    default=False,
    help="Render a video instead of still previews: an orbit MP4, or "
    "one animation clip with --clip",
)
@click.option(
    "--clip",
    metavar="NAME",
    default=None,
    help="Animation clip from the scene manifest to render (implies --video)",
)
@click.option(
    "--duration",
    type=float,
    default=10.0,
    show_default=True,
    metavar="SEC",
    help="Orbit video length",
)
@click.option(
    "--fps",
    "video_fps",
    type=int,
    default=30,
    show_default=True,
    help="Video frame rate",
)
@click.option(
    "--square",
    is_flag=True,
    default=False,
    help="Square video instead of 16:9",
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
    video: bool,
    clip: str | None,
    duration: float,
    video_fps: int,
    square: bool,
) -> int:
    """Render turntable previews for built scenes via headless Blender.

    Writes view_01.png ... view_NN.png plus preview.gif into
    runtime/scenes/<weapon>/previews/.

    \b
    Examples:
        wog-dump render ak47
        wog-dump render ak74n --angles 12 --size 1600
        wog-dump render --all --image-format webp
        wog-dump render ak47 --video --duration 8
        wog-dump render ak47 --video --clip "Take 001" --square
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
                t(
                    "No weapons requested (pass names or --all); scenes come "
                    "from runtime/scenes/"
                )
            )
            sys.exit(1)

        manager = RenderManager()
        video_options = None
        if video or clip is not None:
            try:
                video_options = VideoOptions(
                    duration=duration,
                    fps=video_fps,
                    size=size,
                    square=square,
                    clip=clip,
                )
            except RenderError as e:
                logger.error(str(e))
                sys.exit(1)

        rendered = 0
        for weapon in weapon_list:
            try:
                if video_options is not None:
                    output = manager.render_video(weapon, video_options)
                    rendered += 1
                    logger.print_status(f"Video: {output}", "success")
                    continue
                frames = manager.render(weapon, options)
                rendered += 1
                logger.print_status(
                    t(
                        "Previews for {weapon}: {info}",
                        weapon=weapon,
                        info=f"{len(frames)} frames in "
                        f"{manager.scenes_dir(weapon) / 'previews'}",
                    ),
                    "success",
                )
            except RenderError as e:
                logger.error(
                    t("Render failed for {weapon}: {error}", weapon=weapon, error=e)
                )

        if not rendered:
            logger.error(t("No previews rendered"))
            sys.exit(1)

    return rendered
