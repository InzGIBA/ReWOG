"""Low-level client for the WOG query API.

The API speaks BZ2-compressed ``key=value`` payloads over HTTP PUT:
each body is a 4-byte little-endian length prefix followed by the
compressed text. This module owns that transport so both the auth
flow and the key flow share it.
"""

from __future__ import annotations

import bz2
import hashlib
import re
import time
from urllib.parse import quote

import requests

from ..utils.logging import get_logger
from .config import WOGConfig
from .http import RateLimiter, create_session

# Decompression ceiling for server/capture bodies: API responses are a
# few KiB, so anything larger signals a decompression bomb
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024


class ApiError(Exception):
    """Raised when the API returns an error response."""

    def __init__(self, result_code: int, message: str) -> None:
        super().__init__(message)
        self.result_code = result_code


class ApiNetworkError(Exception):
    """Raised when the API cannot be reached or returns garbage."""


def decompress_bz2_capped(
    data: bytes, max_bytes: int = MAX_DECOMPRESSED_BYTES
) -> bytes:
    """Decompress BZ2 data with an output-size ceiling."""
    decompressor = bz2.BZ2Decompressor()
    chunks: list[bytes] = []
    produced = 0
    try:
        while decompressor.needs_input and data:
            chunk = decompressor.decompress(data, max_length=max_bytes - produced + 1)
            if not chunk:
                break
            produced += len(chunk)
            if produced > max_bytes:
                raise ApiNetworkError(f"Decompressed data exceeds {max_bytes} bytes")
            chunks.append(chunk)
            data = decompressor.unused_data
    except (OSError, EOFError, ValueError) as e:
        raise ApiNetworkError(f"Failed to decompress response: {e}") from e
    return b"".join(chunks)


def parse_response_fields(text: str) -> dict[str, str]:
    """Parse the ``key=value&...`` API response into a dict.

    Debug entries (``_1=...``) and the leading length echo are skipped.
    """
    fields: dict[str, str] = {}
    for segment in text.split("&"):
        if "=" in segment:
            key, _, value = segment.partition("=")
            if key and not key.startswith("_"):
                fields[key] = value
    return fields


def form_encode(value: str) -> str:
    """Encode a value the way the Unity client does (WWWForm/EscapeURL).

    Same as ``quote(value, safe='')`` but with lowercase percent hex
    and ``+`` for spaces, matching the captured request bodies.
    """
    encoded = quote(value, safe="").replace("%20", "+")
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), encoded)


def sign_query(params: str) -> str:
    """Append the ``hash=`` signature every client query carries.

    The real client signs each request with an MD5 we cannot reproduce
    (salted inside the Unity binary), but the server does not verify it
    (verified live: requests with no/arbitrary hash succeed). A
    well-formed 32-hex digest keeps the request shape identical to the
    game client's.
    """
    return f"{params}&hash={hashlib.md5(params.encode()).hexdigest()}"


class WOGApiClient:
    """Client for the BZ2-over-HTTP query API."""

    def __init__(
        self, config: WOGConfig, session: requests.Session | None = None
    ) -> None:
        self.config = config
        self.logger = get_logger()
        self.session = session or create_session(self.config)
        self._rate_limiter = RateLimiter(config.api_request_delay)

    def compress_request(self, params: str) -> bytes:
        """Compress request params with the 4-byte length prefix."""
        compressed = bz2.compress(params.encode())
        return len(compressed).to_bytes(4, "little") + compressed

    def decompress_response(self, content: bytes) -> str:
        """Decompress an API response body."""
        if len(content) < 4:
            raise ApiNetworkError("Invalid response length")
        return decompress_bz2_capped(content[4:]).decode("utf-8", errors="replace")

    def call(self, params: str) -> str:
        """Send a query and return the decompressed response text.

        Raises ApiNetworkError on transport failures. API-level errors
        are for the caller to interpret via ``parse_response_fields``.
        """
        payload = self.compress_request(params)

        headers = self.config.get_api_headers()
        headers["Content-Length"] = str(len(payload))

        self._rate_limiter.wait()
        try:
            # The standalone client PUTs to the bare script URL; the
            # ?soc=steam marker is a Steam-client trait and breaks
            # email/password queries
            response = self.session.put(
                self.config.api_base_url,
                data=payload,
                headers=headers,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise ApiNetworkError(f"Network error: {e}") from e

        return self.decompress_response(response.content)

    def build_common_params(self) -> str:
        """Client parameters shared by all queries."""
        return (
            f"ver={self.config.game_version}"
            f"&uver={self.config.unity_version}"
            f"&time={int(time.time())}"
        )
