# Local Database

Since v2.4 the weapon list, decryption keys, your game-account
credentials and the session identity live in a per-user SQLite database
outside the repository: no secrets ship with the code, and two
checkouts never fight over `runtime/keys.txt`.

## Location

| Platform | Default path |
|---|---|
| Linux | `~/.local/share/wog_dump/wog_dump.db` (honors `XDG_DATA_HOME`) |
| macOS | `~/Library/Application Support/wog_dump/wog_dump.db` |
| Windows | `%APPDATA%\wog_dump\wog_dump.db` |

Override with the `WOG_DB_PATH` environment variable (also useful to
keep a test database around). The file is created with owner-only
permissions (`0o600`) where the platform allows. Since 2.6.0,
`WOG_DB_PATH` values pointing through a symlink are refused, so
credentials never get written through a link someone else planted.

Run `wog-dump info` or `wog-dump cache` to see the exact path in use.

## Schema

```sql
-- key/value settings: account email+password, generated device id
CREATE TABLE settings (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- the session identity from the last successful login (single row, id=1)
CREATE TABLE identity (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    auth_id    TEXT NOT NULL,
    session    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- weapon names extracted from spider_gen.unity3d
CREATE TABLE weapons (
    name     TEXT PRIMARY KEY,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- per-model decryption keys ("sync" values from the query API)
CREATE TABLE keys (
    model      TEXT PRIMARY KEY,
    sync_key   TEXT NOT NULL,
    source     TEXT,            -- e.g. 'legacy_import'
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

All access goes through `WOGDatabase` (`core/database.py`), which
serializes every operation with a re-entrant lock. Key fetching runs in
a thread pool, and SQLite connections are per-call.

## What is stored where

| Data | Table | Written by |
|---|---|---|
| Weapon list | `weapons` | `download-weapons` |
| Decryption keys | `keys` | key fetch (`--update-keys`), MITM import (`scripts/extract_keys.py --merge`) |
| Account email/password | `settings` | `wog-dump login` |
| Session (auth_id/session) | `identity` | automatic on login / re-login |
| Device id | `settings` | generated once per installation |

Downloaded/decrypted/unpacked files stay in `<base>/runtime/`. Only the
small indexed data lives in the database.

## Managing the database

```bash
wog-dump cache                # statistics: counts, account, session age
wog-dump cache --show-cache   # + sample weapons with key availability
wog-dump cache --clear        # delete weapons and keys (account kept)
wog-dump logout               # remove account credentials and session
```

## Migrating from pre-2.4 installations

Old installations kept `runtime/weapons.txt` and `runtime/keys.txt`.
Import both once:

```bash
wog-dump cache --migrate
```

The import is non-destructive: existing database rows win, imported
keys are merged (never dropped). The tool also attempts this migration
automatically the first time the tool starts with an empty database.
Since 2.6.0 the import runs once per database: a second
`cache --migrate` is refused, and `--force` overrides.
