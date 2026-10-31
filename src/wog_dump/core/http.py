"""Shared HTTP session helpers for WOG Dump."""

from __future__ import annotations

import threading
import time

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


class RateLimiter:
    """Thread-safe throttle enforcing a minimum interval between requests.

    The game servers reject or ban abusive clients; call ``wait()`` before
    every request and the limiter serializes them to at most one per
    ``min_interval`` seconds across all threads. A zero interval disables
    throttling.
    """

    def __init__(self, min_interval: float = 0.0) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last_request = 0.0

    def wait(self) -> None:
        """Block until the next request may be sent."""
        if self.min_interval <= 0:
            return

        with self._lock:
            now = time.monotonic()
            next_slot = self._last_request + self.min_interval
            delay = next_slot - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._last_request = now
