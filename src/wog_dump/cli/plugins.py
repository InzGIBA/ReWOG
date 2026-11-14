"""Plugins command: list registered extensions across all groups."""

from __future__ import annotations

import json

import click

from ..core.extensions import describe_extensions
from ..utils.i18n import t
from .errors import error_handler, print_banner_if_standalone

_GROUP_TITLES = {
    "extractors": "Unity extractors (wog_dump.extractors)",
    "exporters": "Export formats (wog_dump.exporters)",
    "commands": "CLI commands (wog_dump.commands)",
    "scene_states": "Scene states (wog_dump.scene_states)",
    "viewers": "Viewer providers (wog_dump.viewers)",
}


@click.command("plugins")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
@click.pass_context
def plugins(ctx: click.Context, as_json: bool) -> None:
    """List installed plugins across all extension groups.

    \b
    Examples:
        wog-dump plugins
        wog-dump plugins --json
    """
    logger = ctx.obj["logger"]

    with error_handler("Plugin listing"):
        if not as_json:
            print_banner_if_standalone(ctx)
        groups = describe_extensions()

        if as_json:
            click.echo(json.dumps(groups, indent=2, ensure_ascii=False))
            return

        total = 0
        for group, title in _GROUP_TITLES.items():
            entries = {
                name: meta
                for name, meta in groups.get(group, {}).items()
                if meta.get("package") != "built-in"
            }
            headers = ["Name", "Package", "Class"]
            if group == "exporters":
                headers.append("Files")
            rows = []
            for name, meta in sorted(entries.items()):
                row = [name, meta.get("package", "?"), meta.get("class", "?")]
                if group == "exporters":
                    row.append(", ".join(meta.get("extensions", [])))
                rows.append(row)
            if rows:
                logger.print_table(title, headers, rows)
            total += len(rows)

        if not total:
            logger.print_status(t("No plugins installed"), "info")
