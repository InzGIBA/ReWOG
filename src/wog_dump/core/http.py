"""Shared HTTP session helpers for WOG Dump."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import WOGConfig

RETRY_STATUS_CODES = (429, 500, 502, 503, 504)


def create_session(config: WOGConfig, pool_size: int | None = None) -> requests.Session:
    """Create a session with retries, connection pooling and WOG API headers."""
    pool = pool_size or max(config.max_threads, 4)

    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=["PUT", "GET", "HEAD"],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=pool,
        pool_maxsize=pool * 2,
    )

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(config.get_api_headers())
    return session
