"""Benchmark: threaded requests downloads vs asyncio + httpx.

Downloads the same real assets twice (same chunk size, same worker
count, no rate limiting, temp output) and reports wall time and
throughput per mode. Feeds the task-11 stop criterion: the async port
only happens when the measured gain is at least 15 percent.

Usage: python scripts/bench_download.py [N_ASSETS] [WORKERS]
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from wog_dump.core.config import get_config  # noqa: E402


def pick_assets(count: int) -> list[tuple[str, int]]:
    """Known-good asset names from the local cache with server sizes."""
    config = get_config()
    local = sorted(p.stem for p in config.encrypted_dir.glob("*.bytes"))
    local = [name for name in local if name != "spider_gen"][: count * 2]
    chosen: list[tuple[str, int]] = []
    session = requests.Session()
    for name in local:
        if len(chosen) >= count:
            break
        url = f"{config.data_base_url.rstrip('/')}/{name}.unity3d"
        try:
            head = session.head(url, timeout=15)
            size = int(head.headers.get("Content-Length", 0))
        except requests.RequestException:
            continue
        if head.status_code == 200 and 1_000_000 < size < 80_000_000:
            chosen.append((url, size))
    return chosen


def fetch_threaded(
    urls: list[str], workers: int, chunk_size: int, out_dir: Path
) -> float:
    """ThreadPoolExecutor + requests.Session, one session per thread."""
    thread_local = threading_local()

    def fetch(url: str) -> int:
        session = getattr(thread_local, "session", None)
        if session is None:
            session = requests.Session()
            thread_local.session = session
        received = 0
        with session.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            target = out_dir / (url.rsplit("/", 1)[-1] + ".part")
            with open(target, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        received += len(chunk)
        return received

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        total = sum(executor.map(fetch, urls))
    elapsed = time.perf_counter() - started
    print(f"threaded: {total / 1e6:.1f} MB in {elapsed:.2f}s")
    return elapsed


def threading_local() -> Any:
    import threading

    return threading.local()


async def fetch_async(
    urls: list[str], workers: int, chunk_size: int, out_dir: Path
) -> float:
    import httpx

    limits = httpx.Limits(max_connections=workers)
    started = time.perf_counter()
    total = 0
    async with httpx.AsyncClient(
        limits=limits, timeout=httpx.Timeout(60.0)
    ) as client:
        semaphore = asyncio.Semaphore(workers)

        async def fetch(url: str) -> int:
            async with semaphore:
                received = 0
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    target = out_dir / (url.rsplit("/", 1)[-1] + ".part")
                    with open(target, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size):
                            if chunk:
                                f.write(chunk)
                                received += len(chunk)
                return received

        results = await asyncio.gather(*(fetch(url) for url in urls))
        total = sum(results)
    elapsed = time.perf_counter() - started
    print(f"async:    {total / 1e6:.1f} MB in {elapsed:.2f}s")
    return elapsed


def main(argv: list[str]) -> int:
    from typing import Any

    count = int(argv[1]) if len(argv) > 1 else 8
    workers = int(argv[2]) if len(argv) > 2 else 4
    config = get_config()
    chunk_size = config.chunk_size

    assets = pick_assets(count)
    if len(assets) < count:
        print(f"only {len(assets)} assets reachable, need {count}")
        return 1
    total_mb = sum(size for _, size in assets) / 1e6
    urls = [url for url, _ in assets]
    print(f"{len(assets)} assets, {total_mb:.1f} MB total, {workers} workers")

    with tempfile.TemporaryDirectory(prefix="wog-bench-") as tmp:
        out = Path(tmp)
        # Warm the TLS/CDN path once so neither mode pays cold-start cost
        fetch_threaded(urls[:1], 1, chunk_size, out)
        threaded = fetch_threaded(urls, workers, chunk_size, out)
        threaded_repeat = fetch_threaded(urls, workers, chunk_size, out)
        asyncio.run(fetch_async(urls, workers, chunk_size, out))
        async_repeat = asyncio.run(fetch_async(urls, workers, chunk_size, out))

    best_threaded = min(threaded, threaded_repeat)
    best_async = min(async_repeat, float("inf"))
    gain = (best_threaded - best_async) / best_threaded * 100
    print(f"\nthreaded best {best_threaded:.2f}s | async best {best_async:.2f}s")
    print(f"async gain: {gain:+.1f}% (port threshold: +15%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
