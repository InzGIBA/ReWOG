"""Catalog command: per-weapon pipeline status table."""

from __future__ import annotations

import json

import click

from ..core.catalog import CATALOG_SCHEMA, STAGES, WeaponEntry, build_catalog
from .errors import error_handler, print_banner_if_standalone

STATUS_CHOICES = STAGES + ("previews",)


def _human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    units = ("B", "KB", "MB", "GB")
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "-"


def _apply_filters(
    entries: list[WeaponEntry],
    filter_text: str | None,
    status: str | None,
) -> list[WeaponEntry]:
    if filter_text:
        needle = filter_text.lower()
        entries = [e for e in entries if needle in e.name.lower()]
    if status:
        entries = [e for e in entries if getattr(e, status)]
    return entries


def _sort_entries(
    entries: list[WeaponEntry], sort_key: str, reverse: bool
) -> list[WeaponEntry]:
    if sort_key == "name":
        entries.sort(key=lambda e: e.name, reverse=reverse)
    elif sort_key == "size":
        entries.sort(key=lambda e: (e.size_bytes, e.name), reverse=reverse)
    elif sort_key == "status":
        entries.sort(key=lambda e: (-e.stages_passed, e.name), reverse=reverse)
    return entries


def _entries_to_json(entries: list[WeaponEntry]) -> dict:
    return {
        "schema": CATALOG_SCHEMA,
        "count": len(entries),
        "weapons": [
            {
                "name": e.name,
                "has_key": e.has_key,
                "downloaded": e.downloaded,
                "decrypted": e.decrypted,
                "unpacked": e.unpacked,
                "scene": e.scene,
                "previews": e.previews,
                "size_bytes": e.size_bytes,
            }
            for e in entries
        ],
    }


@click.command("catalog")
@click.option(
    "--filter", "filter_text", metavar="TEXT", help="Case-insensitive name substring"
)
@click.option(
    "--status",
    type=click.Choice(STATUS_CHOICES),
    help="Only weapons that passed the stage (key, downloaded, decrypted, "
    "unpacked, scene, previews)",
)
@click.option(
    "--sort",
    "sort_key",
    type=click.Choice(("name", "size", "status")),
    default="name",
    show_default=True,
    help="Sort column",
)
@click.option("--reverse", is_flag=True, default=False, help="Reverse the sort order")
@click.option(
    "--limit", type=int, default=None, metavar="N", help="Show the first N rows"
)
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Machine-readable output"
)
@click.pass_context
def catalog(
    ctx: click.Context,
    filter_text: str | None,
    status: str | None,
    sort_key: str,
    reverse: bool,
    limit: int | None,
    as_json: bool,
) -> None:
    """List every weapon with its pipeline status.

    Reads the local database and runtime folders only, no network.

    \b
    Examples:
        wog-dump catalog
        wog-dump catalog --filter ak --status decrypted
        wog-dump catalog --sort size --limit 20
        wog-dump catalog --json
    """
    logger = ctx.obj["logger"]

    with error_handler("Catalog"):
        print_banner_if_standalone(ctx)
        config = ctx.obj["config"]

        entries = build_catalog(config)
        if not entries:
            logger.print_status(
                "Catalog is empty: run 'wog-dump download-weapons' first", "warning"
            )
            return

        entries = _apply_filters(entries, filter_text, status)
        entries = _sort_entries(entries, sort_key, reverse)
        total = len(entries)
        if limit is not None:
            entries = entries[: max(limit, 0)]

        if as_json:
            payload = _entries_to_json(entries)
            payload["total"] = total
            click.echo(json.dumps(payload, indent=2))
            return

        if not entries:
            logger.print_status("No weapons match the filters", "warning")
            return

        rows = [
            [
                e.name,
                "key" if e.has_key else "-",
                "dl" if e.downloaded else "-",
                "dec" if e.decrypted else "-",
                "unp" if e.unpacked else "-",
                "scene" if e.scene else "-",
                "prev" if e.previews else "-",
                _human_size(e.size_bytes),
            ]
            for e in entries
        ]
        shown = f"{len(entries)} of {total}" if len(entries) != total else str(total)
        logger.print_table(
            f"Catalog ({shown})",
            ["Weapon", "Key", "DL", "DEC", "UNP", "Scene", "Prev", "Size"],
            rows,
        )
