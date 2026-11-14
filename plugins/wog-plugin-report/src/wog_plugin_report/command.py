"""CLI command: scene-report (Markdown/JSON reports for built scenes)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from wog_dump.core.config import get_config
from wog_dump.core.paths import is_safe_weapon_name
from wog_dump.utils.logging import WOGLogger, get_logger

from .report import build_report, render_markdown


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_print_report(path: Path, logger: WOGLogger) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Ignoring unreadable print report {path}: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _render(report: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(report, indent=2, ensure_ascii=False)
    return render_markdown(report)


def _emit(
    reports: dict[str, dict[str, Any]],
    weapons: tuple[str, ...],
    as_json: bool,
    output: Path | None,
) -> None:
    if output is None:
        if as_json:
            payload: Any = reports[weapons[0]] if len(weapons) == 1 else reports
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            texts = [render_markdown(reports[w]) for w in weapons if w in reports]
            click.echo("\n---\n\n".join(texts), nl=False)
        return
    if len(weapons) == 1:
        weapon = weapons[0]
        if weapon in reports:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(_render(reports[weapon], as_json), encoding="utf-8")
        return
    output.mkdir(parents=True, exist_ok=True)
    suffix = "json" if as_json else "md"
    for weapon in weapons:
        if weapon in reports:
            path = output / f"{weapon}-report.{suffix}"
            path.write_text(_render(reports[weapon], as_json), encoding="utf-8")


@click.command("scene-report")
@click.argument("weapons", nargs=-1, required=True)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Machine-readable JSON dict per weapon instead of Markdown",
)
@click.option(
    "--output",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=None,
    help="Write to a file (one weapon) or a directory of "
    "<weapon>-report.md/json files (several weapons)",
)
def scene_report(weapons: tuple[str, ...], as_json: bool, output: Path | None) -> None:
    """Report on built scenes: counts, parts, placements, print diagnostics.

    Reads runtime/scenes/<weapon>/scene.json (plus print/print_report.json
    when present) and prints a Markdown report, or JSON with --json.

    \b
    Examples:
        wog-dump scene-report ak47
        wog-dump scene-report ak47 m4a1 --json
        wog-dump scene-report ak47 m4a1 --output reports/
    """
    logger = get_logger()
    for weapon in weapons:
        if not is_safe_weapon_name(weapon):
            raise click.BadParameter(
                f"Unsafe weapon name: {weapon!r}", param_hint="WEAPONS"
            )

    config = get_config()
    scenes_dir = config.base_dir / "runtime" / "scenes"
    reports: dict[str, dict[str, Any]] = {}
    failed = False
    for weapon in weapons:
        scene_dir = scenes_dir / weapon
        manifest_path = scene_dir / "scene.json"
        if not manifest_path.is_file():
            click.echo(
                f"{weapon}: scene.json not found at {manifest_path}; "
                f"run 'wog-dump build-scene {weapon}' first",
                err=True,
            )
            failed = True
            continue
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            click.echo(f"{weapon}: cannot read {manifest_path}", err=True)
            failed = True
            continue
        print_report = _load_print_report(
            scene_dir / "print" / "print_report.json", logger
        )
        reports[weapon] = build_report(manifest, print_report)

    _emit(reports, weapons, as_json, output)
    if failed:
        raise SystemExit(1)
