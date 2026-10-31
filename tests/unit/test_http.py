"""Unit tests for shared HTTP session helpers and rate limiting."""

from __future__ import annotations

import threading
import time

from wog_dump.core.config import WOGConfig
from wog_dump.core.http import RateLimiter, create_session


class TestCreateSession:
    def test_retries_and_pooling_configured(self, test_config: WOGConfig) -> None:
        session = create_session(test_config)

        adapter = session.get_adapter("http://example.com")
        retry = adapter.max_retries
        assert retry.total == 3
        assert 429 in retry.status_forcelist
        assert set(retry.allowed_methods) == {"PUT", "GET", "HEAD"}
        assert adapter._pool_maxsize >= test_config.max_threads * 2

    def test_api_headers_set(self, test_config: WOGConfig) -> None:
        session = create_session(test_config)

        assert (
            session.headers["User-Agent"] == f"UnityPlayer/{test_config.unity_version} "
            "(UnityWebRequest/1.0, libcurl/8.10.1-DEV)"
        )
        assert session.headers["X-Unity-Version"] == test_config.unity_version


class TestRateLimiter:
    def test_zero_interval_is_free(self) -> None:
        limiter = RateLimiter(0.0)

        start = time.monotonic()
        for _ in range(100):
            limiter.wait()

        assert time.monotonic() - start < 0.1

    def test_enforces_min_interval(self) -> None:
        limiter = RateLimiter(0.05)

        start = time.monotonic()
        limiter.wait()
        limiter.wait()
        limiter.wait()
        elapsed = time.monotonic() - start

        assert elapsed >= 0.09  # at least two full gaps between three calls

    def test_thread_safe(self) -> None:
        """Concurrent wait() calls never run two requests in the same slot."""
        limiter = RateLimiter(0.01)
        timestamps: list[float] = []
        lock = threading.Lock()

        def worker() -> None:
            for _ in range(5):
                limiter.wait()
                with lock:
                    timestamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        timestamps.sort()
        for earlier, later in zip(timestamps, timestamps[1:]):
            assert later - earlier >= 0.008  # ~min_interval, with tolerance
