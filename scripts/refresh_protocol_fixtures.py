"""Refresh the protocol fixtures against the live game servers.

Manual maintainer tool: captures the anonymous query response and the
weapon list HEAD, scrubs anything that looks like a credential and
rewrites ``tests/fixtures/protocol/``. Run it when the game protocol
drifts and the fixtures need to match the new shapes.

Usage: python scripts/refresh_protocol_fixtures.py
"""

from __future__ import annotations

import base64
import bz2
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from wog_dump.core.api import WOGApiClient  # noqa: E402
from wog_dump.core.config import get_config  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "protocol"

# Anything credential-shaped gets neutered before it lands in git
SECRET_PATTERNS = re.compile(
    r"(pass|hash|session|acc_id|auth_id|device)=[^&\s]*", re.IGNORECASE
)


def scrub(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    text = SECRET_PATTERNS.sub(lambda m: f"{m.group(1)}=SCRUBBED", text)
    return text.encode("utf-8")


def fixture(kind: str, status: int, data: bytes, note: str) -> dict:
    return {
        "kind": kind,
        "status": status,
        "data_b64": base64.b64encode(scrub(data)).decode(),
        "note": note,
    }


def main() -> int:
    config = get_config()
    client = WOGApiClient(config)
    FIXTURES.mkdir(parents=True, exist_ok=True)

    params = client.build_common_params()
    response = requests.put(  # noqa: S113
        config.api_base_url, data=client.compress_request(params), timeout=20
    )
    body = None
    try:
        body = bz2.decompress(response.content)
    except Exception:
        pass
    (FIXTURES / "anonymous_query.json").write_text(
        json.dumps(
            fixture(
                "bz2" if body is not None else "raw",
                response.status_code,
                response.content,
                "anonymous query2026 PUT response (no session), captured live",
            ),
            indent=2,
        )
    )

    from wog_dump.core.download import SUBDIRECTORY_ASSETS  # noqa: E402

    subdirectory = SUBDIRECTORY_ASSETS.get("spider_gen")
    base = config.data_base_url.rstrip("/")
    spider_url = (
        f"{base}/{subdirectory}/spider_gen.unity3d"
        if subdirectory
        else f"{base}/spider_gen.unity3d"
    )
    head = requests.head(  # noqa: S113
        spider_url,
        timeout=20,
        allow_redirects=True,
    )
    (FIXTURES / "weapon_list_head.json").write_text(
        json.dumps(
            {
                "kind": "head",
                "status": head.status_code,
                "content_length": int(head.headers.get("Content-Length", 0)),
                "note": "weapon list asset HEAD, captured live",
            },
            indent=2,
        )
    )

    # The auth shape fixture stays synthetic: no real account data in git
    print("fixtures refreshed:", sorted(p.name for p in FIXTURES.iterdir()))
    return 0


if __name__ == "__main__":
    import requests  # noqa: E402

    sys.exit(main())
