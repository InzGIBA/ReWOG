#!/usr/bin/env python3
"""Extract WOG decryption keys from a captured traffic dump.

The key API only serves the real game client, so fresh keys have to be
captured from your own game traffic (see docs/mitm-capture.md). This
script parses the capture, prints the model -> key pairs it finds and
can merge them into the local key store.

Supported capture formats:
  - mitmproxy flow dumps (*.flows)          - requires the [mitm] extra
  - Fiddler SAZ archives (*.saz)
  - loose files: a request/response pair saved into one text file,
    or a directory containing such files

Usage:
    uv run python scripts/extract_keys.py capture.flows
    uv run python scripts/extract_keys.py capture.flows --merge
    uv run python scripts/extract_keys.py capture.flows -o new_keys.txt
    uv run python scripts/extract_keys.py dumps/ --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from a source checkout without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wog_dump.core.key_import import (  # noqa: E402
    KeyImportError,
    dedupe_keys,
    extract_identity_from_flows,
    extract_keys,
    import_identity,
    merge_keys,
    write_keys_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract WOG decryption keys from a captured traffic dump."
    )
    parser.add_argument(
        "capture",
        type=Path,
        help="mitmproxy .flows dump, Fiddler .saz archive, a dump file "
        "or a directory containing dumps",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="write the extracted keys to a shareable keys.txt-format file",
    )
    parser.add_argument(
        "--identity",
        action="store_true",
        help="also import the captured client identity (id/session/dev) "
        "into the local database (mitmproxy .flows captures only)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="merge the extracted keys into the local key store "
        "(runtime/data.json and runtime/keys.txt); existing keys are kept",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()

    try:
        captured = extract_keys(args.capture)
    except KeyImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    keys = dedupe_keys(captured)

    if args.json:
        print(json.dumps(keys, indent=2, sort_keys=True))
    elif keys:
        print(f"Found {len(keys)} key(s) in {args.capture}:\n")
        for model in sorted(keys):
            print(f"  {model:<30} {keys[model]}")
    else:
        print(
            f"No model/key pairs found in {args.capture}.\n"
            "Hints: the capture must contain requests to the key API "
            "(ultimate-disassembly.com) with their responses - open the "
            "weapon models in game while capturing. See docs/mitm-capture.md."
        )

    if args.out and keys:
        write_keys_file(args.out, keys)
        print(f"\nWrote {len(keys)} key(s) to {args.out}")

    if args.identity:
        try:
            identity = extract_identity_from_flows(args.capture)
            import_identity(identity)
            device = identity.device_id or "<generated>"
            print(
                f"\nImported client identity (id={identity.auth_id}):\n"
                f"  WOG_AUTH_ID={identity.auth_id}\n"
                f"  WOG_AUTH_SESSION={identity.session}\n"
                f"  WOG_DEVICE_ID={device}\n"
                "Add these lines to your .env to use them directly."
            )
        except KeyImportError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    if args.merge and keys:
        result = merge_keys(keys)
        print(
            f"\nMerged into the key store: {result.added} new, "
            f"{result.updated} updated, {result.total} total."
        )

    return 0 if keys else 1


if __name__ == "__main__":
    sys.exit(main())
