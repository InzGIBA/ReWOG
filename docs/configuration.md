# Configuration Reference

WOG Dump is configured through three layers, applied in this order
(later layers win):

1. **Built-in defaults** (`wog_dump/core/config.py`)
2. **Environment variables / `.env` file** in the working directory
   (`python-dotenv`, never overriding variables already set in the shell).
   Since 2.6.0 a `.env` file applies only `WOG_*` keys: transport
   variables like `HTTPS_PROXY` or `REQUESTS_CA_BUNDLE` belong in your
   real shell environment.
3. **CLI global options** (`wog-dump --max-threads 8 ...`)

Copy `.env.example` to `.env` and fill in what you need. `.env` is
gitignored, so credentials never end up in the repository.

## Authentication

The key API only serves authenticated clients. Pick **one** of the
following options:

| Variable | Meaning |
|---|---|
| `WOG_ACCOUNT_EMAIL` / `WOG_ACCOUNT_PASSWORD` | Your game account (same login as the game client). A session is fetched automatically and stored in the local database. Preferred. |
| `WOG_AUTH_ID` / `WOG_AUTH_SESSION` | A captured session from your own traffic (see [mitm-capture.md](mitm-capture.md)). |
| `WOG_DEVICE_ID` | Optional device id to send with a captured session. Generated per installation when unset. |

The recommended flow is a single `wog-dump login` run: the command
stores the account in the local database, and no environment variables
are needed at all.

## Endpoints and protocol

| Variable | Default | Meaning |
|---|---|---|
| `WOG_API_BASE_URL` | `https://eu1.ultimate-disassembly.com/v/query2026.php` | Key API endpoint |
| `WOG_DATA_BASE_URL` | `https://data1eu.ultimate-disassembly.com/uni2018` | Asset download server |
| `WOG_GAME_VERSION` | `2.2.3u9` | Game version reported to the API |
| `WOG_UNITY_VERSION` | `6000.0.84f1` | Unity version reported to the API |

The game version strings must match the current `disasm64.exe` client.
Wrong values are rejected server-side. Update the strings when the game
updates.

## Storage

| Variable | Default | Meaning |
|---|---|---|
| `WOG_BASE_DIR` | current directory | Base directory. Runtime data lives in `<base>/runtime/` |
| `WOG_DB_PATH` | per-user data dir (see [database.md](database.md)) | Location of the SQLite database |

## Performance and reliability

| Variable | Default | Meaning |
|---|---|---|
| `WOG_MAX_THREADS` | `4` | Worker threads for parallel downloads/key fetches (1-16) |
| `WOG_REQUEST_DELAY` | `0.1` | Minimum interval in seconds between asset-server requests |
| `WOG_API_REQUEST_DELAY` | `0.25` | Minimum interval in seconds between key-API requests |
| `WOG_ENABLE_VALIDATION` | `true` | Validate files after download/decryption |
| `WOG_ENABLE_BACKUP` | `true` | Keep backups during destructive operations |
| `WOG_STRICT_MODE` | `false` | Fail the whole run on the first error instead of continuing |

## Logging

The console and the log file have independent verbosity:

- **Console**: quiet by default (warnings/errors only). `wog-dump -v`
  shows INFO, `wog-dump -d` shows DEBUG.
- **Log file**: always captures INFO and above (including full
  tracebacks) no matter how quiet the console is. Set
  `WOG_LOG_LEVEL=DEBUG` to include debug output in the file.

| Variable | Default | Meaning |
|---|---|---|
| `WOG_LOG_LEVEL` | `INFO` | Log file verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `WOG_LOG_DIR` | `<user data dir>/logs` | Directory for log files |

Logs go to a single rotating file `wog.log` (5 MB × 3 backups) in the
user data directory next to the database, for example
`~/.local/share/wog_dump/logs/wog.log` on Linux. `wog-dump info` shows
the exact path in use.

### Rate limiting

The two delay settings throttle every request (downloads and API calls
alike) across all worker threads. The defaults keep request rates low.
Increase the delays if you see `429` rate-limit errors, decrease to zero
for maximum speed on a connection you trust.

### Resumable downloads

Interrupted downloads keep their partial data in `runtime/assets/*.tmp`.
The next run sends an HTTP `Range` request and continues from where the
transfer stopped, instead of starting over. Since 2.6.0 a resume also
needs the `<name>.tmp.meta` sidecar written during the original
download, which records the ETag/Last-Modified validator. Partials
without a sidecar, or where the server answers 200 because the file
changed, restart from scratch.

## CLI equivalents

Every environment variable has a CLI option on the main group where such
an option makes sense:

```bash
wog-dump --max-threads 8 --chunk-size 64 download-assets
wog-dump --strict-mode full-pipeline
```

Run `wog-dump info` at any time to print the effective configuration,
where your database lives and what is logged in.
