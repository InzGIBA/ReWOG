"""Update command: hash/validator-based asset update detection."""

from __future__ import annotations

import sys

import click

from ..core.config import WOGConfig
from ..core.paths import is_safe_weapon_name
from ..core.update import UpdateChecker, UpdateReport
from ..utils.i18n import t
from ..utils.logging import WOGLogger
from .errors import error_handler, print_banner_if_standalone


def _parse_weapons(weapons: str | None) -> list[str] | None:
    """Resolve --weapons into a validated, de-duplicated name list."""
    if not weapons:
        return None
    names = [name.strip() for name in weapons.split(",") if name.strip()]
    invalid = [name for name in names if not is_safe_weapon_name(name)]
    if invalid:
        raise click.BadParameter(
            "Invalid weapon name(s): " + ", ".join(repr(name) for name in invalid)
        )
    return list(dict.fromkeys(names))


def _format_size(size: int | None) -> str:
    return "-" if size is None else f"{size:,}"


def _print_report(logger: WOGLogger, report: UpdateReport) -> None:
    rows = [
        [
            check.asset,
            check.status,
            check.reason,
            _format_size(check.local_size),
            _format_size(check.server_size),
        ]
        for check in report.checks
    ]
    logger.print_table(
        t("Asset update check"),
        [t("Weapon"), t("Status"), t("Reason"), t("Local size"), t("Server size")],
        rows,
    )
    summary = report.summary()
    logger.print_table(
        t("Summary"),
        [t("Status"), t("Count")],
        [[status, str(count)] for status, count in summary.items()],
    )


@click.command("update")
@click.option(
    "--apply",
    is_flag=True,
    help="Re-download changed assets, then re-decrypt and re-unpack "
    "only the affected weapons",
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check for updates, do not download (the default behavior)",
)
@click.option(
    "--weapons", type=str, help="Comma-separated list of specific weapons to check"
)
@click.pass_context
def update(
    ctx: click.Context, apply: bool, check_only: bool, weapons: str | None
) -> int:
    """Check downloaded assets for server-side updates.

    Compares ETag/Last-Modified/Content-Length against the stored asset
    state; when the server sends no validators, local files are
    re-hashed to detect changes and corruption. The first run backfills
    state from existing files without re-downloading.

    \b
    Examples:
        wog-dump update                     # check only (default)
        wog-dump update --weapons ak74n,m4a1
        wog-dump update --apply             # re-download changed assets
    """
    logger = ctx.obj["logger"]
    config: WOGConfig = ctx.obj["config"]

    if apply and check_only:
        raise click.UsageError("--apply and --check-only are mutually exclusive")

    with error_handler("Asset update check"):
        print_banner_if_standalone(ctx)
        weapon_filter = _parse_weapons(weapons)

        checker = UpdateChecker(config)
        with logger.operation_context("update_check", "asset update check"):
            report = checker.check_assets(weapons=weapon_filter)

        if not report.checks:
            logger.print_status(
                t("No downloaded assets to check; run 'download-assets' first"),
                "warning",
            )
            return 0

        _print_report(logger, report)

        if not apply:
            if report.changed:
                logger.print_status(
                    t(
                        "{count} asset(s) changed; run 'update --apply' to refresh them",  # noqa: E501
                        count=len(report.changed),
                    ),
                    "info",
                )
            else:
                logger.print_status(t("All checked assets are up to date"), "success")
            if report.errors:
                logger.print_status(
                    t(
                        "{count} asset(s) could not be checked",
                        count=len(report.errors),
                    ),
                    "error",
                )
                sys.exit(1)
            return 0

        result = checker.apply_updates(report)
        if result.downloaded:
            logger.print_status(
                t(
                    "Updated {count} asset(s): {decrypted} decrypted, {unpacked} unpacked",  # noqa: E501
                    count=len(result.downloaded),
                    decrypted=len(result.decrypted_files),
                    unpacked=result.unpacked_files,
                ),
                "success",
            )
        if result.missing:
            logger.print_status(
                t("{count} asset(s) no longer on the server", count=result.missing),
                "warning",
            )
        if result.failed or result.decrypt_failed or report.errors:
            logger.print_status(
                t(
                    "Update failed for {failed} download(s), {decrypt} decryption(s)",
                    failed=len(result.failed),
                    decrypt=len(result.decrypt_failed),
                ),
                "error",
            )
            sys.exit(1)
        return 0
