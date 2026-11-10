"""Info command for the WOG Dump CLI."""

from __future__ import annotations

import click

from ..core.auth import AuthManager
from ..core.config import WOGConfig
from ..core.database import WOGDatabase
from ..core.export import find_blender
from ..utils.logging import WOGLogger
from .decrypt import validate_decrypted_files
from .errors import print_banner_if_standalone


@click.command()
@click.option("--show-performance", is_flag=True, help="Show performance metrics")
@click.option("--validate-files", is_flag=True, help="Validate existing files")
@click.option(
    "--protocol-probe",
    is_flag=True,
    help="Check game API compatibility (run this first when everything "
    "breaks after a game update); exits 2 on any failed check",
)
@click.option(
    "--probe-json",
    is_flag=True,
    default=False,
    help="With --protocol-probe: print the machine-readable report "
    "(schema wog-dump.probe/1) instead of the table",
)
@click.pass_context
def info(
    ctx: click.Context,
    show_performance: bool,
    validate_files: bool,
    protocol_probe: bool,
    probe_json: bool,
) -> None:
    """Show comprehensive configuration and status information.

    Displays current configuration, file status, and optionally performance
    metrics and file validation results.
    """
    logger = ctx.obj["logger"]
    config = ctx.obj["config"]

    print_banner_if_standalone(ctx)

    if protocol_probe:
        run_protocol_probe(logger, probe_json)
        return

    # Configuration info
    config_data = [
        ["Base Directory", str(config.base_dir)],
        ["Database", str(config.db_path)],
        ["Account", AuthManager(config).get_account_email() or "not logged in"],
        ["Assets Directory", str(config.assets_dir)],
        ["Encrypted Directory", str(config.encrypted_dir)],
        ["Decrypted Directory", str(config.decrypted_dir)],
        ["Log File", str(logger.log_file or "console only")],
        ["Max Threads", str(config.max_threads)],
        ["Chunk Size", f"{config.chunk_size // 1024} KB"],
        ["Strict Mode", "Enabled" if config.strict_mode else "Disabled"],
    ]

    logger.print_table("Configuration", ["Setting", "Value"], config_data)

    # Status info
    status_data = collect_status_info(config)
    logger.print_table("Status", ["Component", "Status"], status_data)

    # Performance metrics
    if show_performance:
        logger.print_performance_summary()

    # File validation
    if validate_files:
        perform_file_validation(config, logger)


def run_protocol_probe(logger: WOGLogger, as_json: bool) -> None:
    """Run the protocol cascade and print or emit the report."""
    import json
    import sys

    from ..core.probe import PROBE_SCHEMA, ProtocolProbe, probe_verdict

    results = ProtocolProbe().run()
    verdict = probe_verdict(results)
    if as_json:
        logger.console.print(
            json.dumps(
                {
                    "schema": PROBE_SCHEMA,
                    "verdict": verdict,
                    "checks": [
                        {
                            "name": r.name,
                            "verdict": r.verdict,
                            "detail": r.detail,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        rows = [
            [
                r.name,
                {"ok": "OK", "fail": "FAIL", "skip": "SKIP", "warn": "WARN"}[r.verdict],
                r.detail or "-",
            ]
            for r in results
        ]
        logger.print_table(
            f"Protocol probe ({verdict.upper()})", ["Check", "Result", "Detail"], rows
        )
        if verdict == "fail":
            logger.print_status(
                "Protocol drift detected: capture a fresh session per "
                "docs/mitm-capture.md and open a protocol-drift issue",
                "error",
            )

    if verdict == "fail":
        sys.exit(2)


def collect_status_info(config: WOGConfig) -> list[list[str]]:
    """Collect system status information."""
    status_data = []

    db = WOGDatabase(config)
    stats = db.get_stats()

    weapons_count = stats["weapons"]["count"]
    keys_count = stats["keys"]["count"]

    if weapons_count > 0:
        weapons_status = f"{weapons_count} weapons"
    elif config.weapons_file and config.weapons_file.exists():
        weapons_status = "Legacy weapons.txt found (run 'wog-dump cache --migrate')"
    else:
        weapons_status = "Not found"

    status_data.append(["Weapon List", weapons_status])
    status_data.append(["Decryption Keys", f"{keys_count} keys"])
    status_data.append(["Account", stats["account"]["email"] or "not logged in"])
    status_data.append(["Database", stats["database"]])

    assets = list(config.assets_dir.glob("*.unity3d"))
    status_data.append(["Downloaded Assets", f"{len(assets)} files"])

    decrypted = list(config.decrypted_dir.glob("*.unity3d"))
    status_data.append(["Decrypted Assets", f"{len(decrypted)} files"])

    unpacked_dir = config.base_dir / "runtime" / "unpacked"
    if unpacked_dir.exists():
        unpacked_count = sum(1 for f in unpacked_dir.rglob("*") if f.is_file())
        status_data.append(["Unpacked Files", f"{unpacked_count} files"])
    else:
        status_data.append(["Unpacked Files", "0 files"])

    blender = find_blender()
    status_data.append(
        ["Blender (previews, FBX)", str(blender) if blender else "not found"]
    )

    return status_data


def perform_file_validation(config: WOGConfig, logger: WOGLogger) -> None:
    """Perform comprehensive file validation."""
    decrypted_files = list(config.decrypted_dir.glob("*.unity3d"))
    if not decrypted_files:
        logger.console.print("\n[bold]File Validation Results:[/bold]")
        logger.console.print("  No decrypted files to validate")
        return

    logger.console.print("\n[bold]File Validation Results:[/bold]")

    validation_failures = validate_decrypted_files(decrypted_files)
    valid_count = len(decrypted_files) - len(validation_failures)
    logger.console.print(
        f"  Decrypted Assets: {valid_count}/{len(decrypted_files)} valid"
    )

    if validation_failures:
        logger.console.print("  Invalid files:")
        for file_path in validation_failures[:5]:
            logger.console.print(f"    - {file_path.name}")
        if len(validation_failures) > 5:
            logger.console.print(f"    ... and {len(validation_failures) - 5} more")
