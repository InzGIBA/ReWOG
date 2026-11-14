"""CLI command: import-keys (keys/identity from captured traffic)."""

from __future__ import annotations

import json
from pathlib import Path

import click

from wog_dump.core.config import get_config
from wog_dump.utils.logging import get_logger

from .key_import import (
    KeyImportError,
    dedupe_keys,
    extract_identity_from_flows,
    extract_keys,
    import_identity,
    merge_keys,
    write_keys_file,
)


@click.command("import-keys")
@click.argument("capture", type=click.Path(path_type=Path))
@click.option(
    "-o",
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="write the extracted keys to a shareable keys.txt-format file",
)
@click.option(
    "--identity",
    is_flag=True,
    help="also import the captured client identity (id/session/dev) "
    "into the local database (mitmproxy .flows captures only)",
)
@click.option(
    "--force",
    is_flag=True,
    help="with --identity: replace an already stored identity instead " "of refusing",
)
@click.option(
    "--merge",
    is_flag=True,
    help="merge the extracted keys into the local key store; " "existing keys are kept",
)
@click.option("--json", "as_json", is_flag=True, help="print the result as JSON")
def import_keys(
    capture: Path,
    out: Path | None,
    identity: bool,
    force: bool,
    merge: bool,
    as_json: bool,
) -> None:
    """Extract WOG decryption keys from a captured traffic dump.

    CAPTURE is a mitmproxy .flows dump, a Fiddler .saz archive, a single
    dumped request/response file, or a directory containing dumps.

    \b
    Examples:
        wog-dump import-keys capture.flows
        wog-dump import-keys capture.flows --merge --identity
        wog-dump import-keys capture.flows -o new_keys.txt
        wog-dump import-keys dumps/ --json
    """
    logger = get_logger()
    config = get_config()

    try:
        captured = extract_keys(capture)
    except KeyImportError as e:
        click.echo(f"error: {e}", err=True)
        raise SystemExit(2)

    keys = dedupe_keys(captured)
    logger.info(f"import-keys: extracted {len(keys)} key(s) from {capture}")

    if as_json:
        click.echo(json.dumps(keys, indent=2, sort_keys=True))
    elif keys:
        click.echo(f"Found {len(keys)} key(s) in {capture}:\n")
        for model in sorted(keys):
            click.echo(f"  {model:<30} {keys[model]}")
    else:
        click.echo(
            f"No model/key pairs found in {capture}.\n"
            "Hints: the capture must contain requests to the key API "
            "(ultimate-disassembly.com) with their responses - open the "
            "weapon models in game while capturing. See docs/mitm-capture.md."
        )

    if out and keys:
        write_keys_file(out, keys)
        click.echo(f"\nWrote {len(keys)} key(s) to {out}")

    if identity:
        try:
            captured_identity = extract_identity_from_flows(capture)
            import_identity(captured_identity, config=config, force=force)
            device = captured_identity.device_id or "<generated>"
            click.echo(
                f"\nImported client identity (id={captured_identity.auth_id}) "
                f"from {captured_identity.source}:\n"
                f"  WOG_AUTH_ID={captured_identity.auth_id}\n"
                f"  WOG_AUTH_SESSION={captured_identity.session}\n"
                f"  WOG_DEVICE_ID={device}\n"
                "Add these lines to your .env to use them directly."
            )
        except KeyImportError as e:
            click.echo(f"error: {e}", err=True)
            raise SystemExit(2)

    if merge and keys:
        result = merge_keys(keys, config=config)
        click.echo(
            f"\nMerged into the key store: {result.added} new, "
            f"{result.updated} updated, {result.total} total."
        )

    raise SystemExit(0 if keys else 1)
