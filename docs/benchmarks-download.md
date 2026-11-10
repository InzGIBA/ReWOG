# Download transport benchmark (task 11)

Decision record for the asyncio/httpx port question. Measured live
against the game's data server in November 2026, single run on a WSL2
desktop connection, 8 assets / 200 MB per round, 4 workers, identical
chunk size, no rate limiting, bytes verified per round.

## Numbers

| round | mode | wall time |
| --- | --- | --- |
| warm-up (1 asset) | threaded | 2.02 s |
| full run 1 | threaded | 5.50 s |
| full run 2 | threaded | 5.12 s |
| full run 1 | async | 5.22 s |
| full run 2 | async | 5.03 s |

Best-of: threaded 5.12 s, async 5.03 s. Async gain: **+1.9 percent**.

Reproduce with `python scripts/bench_download.py [N_ASSETS] [WORKERS]`
(httpx required; the script picks reachable assets from the local
cache and downloads every byte twice per mode).

## Decision

The port does not happen. The task's stop criterion demanded at least
a 15 percent gain; the measured gain is under 2 percent. The download
path is bandwidth-bound, not thread-scheduling bound: at 4 workers the
GIL-waiting requests pipeline already saturates the connection. The
threaded implementation keeps its proven resume, validation and
security paths (the `.tmp.meta` validator machinery from v2.6.0) and
avoids a rewrite of the most regression-sensitive module in exchange
for nothing measurable.

Revisit triggers:

- a future server-side transport change (HTTP/2-only endpoints,
  aggressive per-connection throttling) that makes connection reuse
  the bottleneck;
- a default `--max-threads` above 16, where thread overhead starts to
  matter;
- a request to embed the downloader in an async application, which can
  wrap the synchronous calls in `asyncio.to_thread` without a port.

The benchmark script stays in `scripts/` so the next decision starts
from data, not guesses.
