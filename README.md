<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/hero-light.svg">
  <img alt="ReWOG — World of Guns asset extraction toolkit" src="assets/hero-light.svg" width="100%">
</picture>

**WOG Dump — extract and rebuild 3D weapon models from [World of Guns: Gun Disassembly](https://store.steampowered.com/app/262410/World_of_Guns_Gun_Disassembly/).**

A command-line toolkit for 3D artists, modders and researchers who study game assets. It logs in with your own game account, downloads encrypted Unity bundles from the game servers, decrypts them and rebuilds the assembled weapon — for example, one command turns the in-game AK-47 into a Blender-ready glTF scene with the exact part hierarchy, game materials and studio lighting.

[Overview](#overview) · [Features](#features) · [Quick start](#quick-start) · [Usage](#usage) · [Configuration](#configuration) · [Development](#development) · [FAQ](#faq) · [Contributing](#contributing) · [License](#license)

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-black.svg)](https://github.com/psf/black)
[![Package Manager](https://img.shields.io/badge/package%20manager-uv-blue.svg)](https://github.com/astral-sh/uv)
[![GitHub Issues](https://img.shields.io/github/issues/InzGIBA/ReWOG)](https://github.com/InzGIBA/ReWOG/issues)
[![GitHub Stars](https://img.shields.io/github/stars/InzGIBA/ReWOG)](https://github.com/InzGIBA/ReWOG/stargazers)

</div>

## Overview

WOG Dump automates the full path from the game's servers to usable 3D data: weapon catalog, per-asset decryption keys, encrypted downloads, XOR decryption, Unity unpacking and Blender scene export. It targets the current `query2026` protocol (game `2.2.3t6`, Unity 6) and was verified live against the production API in November 2026.

- **For:** 3D artists, modders, educators and researchers studying weapon assembly and Unity asset pipelines
- **Input:** your game-account credentials (or a captured client session) and the weapons you want
- **Output:** decrypted Unity bundles, extracted models/textures (OBJ, PNG, TGA) and Blender-ready glTF scenes
- **Scope:** personal, educational and research use; extracted assets remain the property of Noble Empire Corp. (see [License](#license))

> **What's new** — v2.7 rewrites email/password login to mirror the standalone client exactly and adds `wog-dump register` for creating a game account from the CLI. Earlier releases hardened downloads and storage after a security audit (v2.6) and added one-command Blender scene export (v2.5). Full history: [CHANGELOG.md](CHANGELOG.md).

## Features

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/feature-01-dark.svg">
  <img alt="Full pipeline — one command from catalog to decrypted assets" src="assets/feature-01-light.svg" width="32%">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/feature-02-dark.svg">
  <img alt="Blender-ready scenes — game hierarchy, materials and lighting" src="assets/feature-02-light.svg" width="32%">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/feature-03-dark.svg">
  <img alt="Private by design — your own account login, keys stay in SQLite" src="assets/feature-03-light.svg" width="32%">
</picture>

- **Full pipeline** — one command goes from weapon catalog to keys, download, decryption and unpacking. Downloads are parallel, rate-limited and resumable after interruptions.
- **Blender-ready scenes** — `build-scene` rebuilds the assembled weapon exactly like in game: part hierarchy and positions, game materials with textures, camera and studio lighting, real-world scale. UI arrows, cutaway meshes and VFX are classified and skipped unless you ask for them with `--extras`.
- **Private by design** — log in with your own game account (`login` / `register`) or a captured session. Credentials, sessions, keys and the weapon list live in a per-user SQLite database with owner-only permissions; no secrets ship with the repository.

## Quick start

### Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- A game account — create one with `wog-dump register` or use your existing email/password
- Internet connection and ~10–20 GB of free disk space for a full extraction
- Optional: [AssetStudio](https://github.com/Perfare/AssetStudio) for browsing raw Unity bundles manually

### Install

From [PyPI](https://pypi.org/project/wog-dump/):

```bash
pip install wog-dump

# USD/USDZ export support:
pip install "wog-dump[usd]"
```

From source (for development):

```bash
git clone https://github.com/InzGIBA/ReWOG
cd ReWOG

# with uv (install it via https://astral.sh/uv or `pip install uv`)
uv pip install -e .

# or with plain pip
pip install -e .
```

### Run your first extraction

```bash
# 1. Create a game account and log in (once; or `wog-dump login` for an existing one)
wog-dump register

# 2. Everything: weapon list, keys, download, decrypt, unpack, normal maps
wog-dump full-pipeline --update-keys --convert-normals

# 3. Rebuild one weapon as a Blender-ready scene
wog-dump build-scene ak47
```

Expected result — data lands under `runtime/`:

```text
runtime/
├── encrypted/    # raw encrypted Unity3D bundles
├── decrypted/    # decrypted bundles
├── unpacked/     # extracted models (OBJ) and textures (PNG, TGA)
└── scenes/ak47/  # ak47.glb + blender_setup.py + scene.json + textures/
```

Open `runtime/scenes/ak47/ak47.glb` in Blender (`File > Import > glTF 2.0`), or run the generated setup script for a configured scene with collections, hidden extras and a saved `.blend`:

```bash
blender --background --python runtime/scenes/ak47/blender_setup.py
```

## Usage

### Step by step

Each pipeline stage is also available as its own command:

```bash
wog-dump info                          # check configuration and status
wog-dump download-weapons              # fetch the weapon catalog
wog-dump download-assets --update-keys # download bundles + fresh keys
wog-dump decrypt-assets                # XOR-decrypt the bundles
wog-dump unpack-assets                 # extract models and textures
wog-dump build-scene ak47              # Blender-ready glTF scene
wog-dump convert-normals ./unpacked --recursive   # optional: fix normal maps
```

Every command documents itself: `wog-dump --help` and `wog-dump <command> --help`.

### Common options

| Option | Purpose | Example |
| --- | --- | --- |
| `--weapons` | Limit a command to specific weapons | `--weapons "ak74,m4a1,glock17"` |
| `--update-keys` | Fetch fresh decryption keys first | `wog-dump download-assets --update-keys` |
| `--check-only` | Preview pending downloads without fetching | `wog-dump download-assets --check-only` |
| `--validate` | Verify decrypted files after decryption | `wog-dump decrypt-assets --validate` |
| `--extras` | Include UI arrows, cutaway meshes and VFX in a scene | `wog-dump build-scene ak47 --extras all` |
| `--convert-normals` | Convert Unity normal maps in the full pipeline | `wog-dump full-pipeline --convert-normals` |
| `--max-threads` | Worker threads (global option, default 4) | `wog-dump --max-threads 16 full-pipeline` |
| `--verbose` / `--debug` | Increase logging detail | `wog-dump --debug full-pipeline` |

### Blender scene output

`build-scene` writes everything you need into `runtime/scenes/<weapon>/`:

```text
<weapon>.glb        # full scene: hierarchy, materials, camera, studio lights
blender_setup.py    # one-click Blender import + collections + .blend save
scene.json          # manifest: counts, skipped objects, parts/field-strip lists
textures/           # source PNG textures (also embedded in the GLB)
```

### Cache management

```bash
wog-dump cache --migrate         # import legacy weapons.txt / keys.txt
wog-dump cache --show-cache      # detailed cache statistics
wog-dump cache --validate-assets # check downloads exist and are non-empty
```

### Using as a Python library

```python
from wog_dump.core.download import DownloadManager
from wog_dump.core.decrypt import KeyManager, AssetDecryptor
from wog_dump.core.config import get_config

config = get_config()

with DownloadManager(config) as downloader:
    successful, failed = downloader.download_assets(["ak74", "m4a1"])

key_manager = KeyManager(config)
keys = key_manager.load_keys()

decryptor = AssetDecryptor(config)
decrypted_files, failed_assets = decryptor.decrypt_all_assets(keys)
```

## Configuration

Settings come from a gitignored `.env` in the repository root (see [`.env.example`](.env.example)) or from the environment. Credentials, sessions, keys and the weapon list are stored in a per-user SQLite database — never in the repository.

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `WOG_ACCOUNT_EMAIL` / `WOG_ACCOUNT_PASSWORD` | No | — | Game account for automatic login (alternative to a captured session) |
| `WOG_AUTH_ID` / `WOG_AUTH_SESSION` | No | — | Captured client session from your own traffic ([guide](docs/mitm-capture.md)) |
| `WOG_DEVICE_ID` | No | generated | Device id sent to the API |
| `WOG_DB_PATH` | No | OS user data dir | Location of the per-user SQLite database |
| `WOG_API_BASE_URL` | No | `https://eu1.ultimate-disassembly.com/v/query2026.php` | Key API endpoint |
| `WOG_DATA_BASE_URL` | No | `https://data1eu.ultimate-disassembly.com/uni2018` | Asset download server |
| `WOG_GAME_VERSION` | No | `2.2.3t6` | Client version string validated by the server |
| `WOG_UNITY_VERSION` | No | `6000.0.84f1` | Unity version string |
| `WOG_API_REQUEST_DELAY` | No | `0.25` | Seconds between key-API requests (raise on 429) |
| `WOG_REQUEST_DELAY` | No | `0.1` | Seconds between asset downloads (raise on 429) |
| `WOG_MAX_THREADS` | No | `4` | Worker threads (1–16) |
| `WOG_LOG_LEVEL` | No | `INFO` | Log file verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `WOG_LOG_DIR` | No | next to the database | Log directory |

In-depth references:

- **[docs/configuration.md](docs/configuration.md)** — every environment variable, `.env` setup, rate limiting, resumable downloads
- **[docs/architecture.md](docs/architecture.md)** — how the modules fit together, the extraction pipeline
- **[docs/database.md](docs/database.md)** — where keys and credentials live, schema, migration from pre-2.4 files
- **[docs/mitm-capture.md](docs/mitm-capture.md)** — capturing keys and identity from your own game traffic

## Development

```bash
git clone https://github.com/InzGIBA/ReWOG
cd ReWOG
uv pip install -e ".[dev]"       # or: pip install -e ".[dev]"
```

### Checks

```bash
pytest                          # all tests
pytest --cov=wog_dump           # with coverage
pytest -m live                  # against the real servers (needs auth in .env)

black src/ tests/               # formatting
isort src/ tests/
mypy src/                       # type checking
flake8 src/ tests/              # linting
```

### Project structure

<details>
<summary>Directory map</summary>

```text
ReWOG/
├── src/wog_dump/          # Main package (source layout)
│   ├── core/              # config, http, api, auth, database, keys, paths,
│   │                      # download, decrypt, unpack, scene, glb, key_import
│   ├── resources/         # blender_setup.py template for generated scripts
│   ├── utils/             # logging, normal map conversion
│   └── cli/               # Click commands: download, decrypt, unpack, scene,
│                          # pipeline, auth, cache, info, convert
├── tests/                 # unit/ + integration/ (CLI end-to-end, live)
├── docs/                  # configuration, architecture, database, mitm-capture
├── scripts/               # extract_keys.py (MITM key import)
└── pyproject.toml         # PEP 621 packaging
```

</details>

## FAQ

### General

**Do I need to own the game?**
The tool downloads public asset files from the game's servers, so it works without ownership — but we recommend owning the game to support the developers.

**What formats does it export?**
Blender-ready glTF 2.0 scenes (`build-scene`), OBJ models, PNG/TGA textures, materials and converted normal maps.

**Which operating systems are supported?**
Windows, macOS and Linux. The tool is pure Python (no C dependencies since v2).

**How much disk space do I need?**
Encrypted ~2–5 GB, decrypted ~2–5 GB, extracted ~3–8 GB. Plan for **10–20 GB** free.

### Troubleshooting

**`No game account configured` when fetching keys**
The key API needs authentication. Run `wog-dump register` (new account), `wog-dump login` (existing one), or put `WOG_ACCOUNT_EMAIL`/`WOG_ACCOUNT_PASSWORD` — or a captured `WOG_AUTH_ID`/`WOG_AUTH_SESSION` — into `.env` (see [docs/mitm-capture.md](docs/mitm-capture.md)). Check what is configured with `wog-dump cache`.

**`Authentication failed (result=100)`**
The API rejected the credentials. Verify the email/password pair in the game client itself, then `wog-dump login` again. A captured session may have expired — capture a fresh one or switch to account login.

**`Session rejected (result=1000)`**
The stored session expired. WOG Dump re-logins automatically; if the error persists, run `wog-dump logout && wog-dump login`.

**`429` / rate-limit errors**
Requests are too frequent. Raise the throttles in `.env`: `WOG_API_REQUEST_DELAY=2.0` (key API) and `WOG_REQUEST_DELAY=0.5` (downloads).

**`No weapon list found. Run 'download-weapons' first.`**
Run `wog-dump download-weapons`. Coming from a pre-2.4 installation with `runtime/weapons.txt`? Import it once with `wog-dump cache --migrate`.

**`No decryption keys found. Use --update-keys to fetch them.`**
Run `wog-dump decrypt-assets --update-keys` (auth must be configured).

**Key API stopped working after a game update**
The client version strings are validated server-side. Update them in `.env` to match the current client (find the values in the game's installation or a fresh capture), e.g. `WOG_GAME_VERSION=2.2.3u9` and `WOG_UNITY_VERSION=6000.0.84f1`.

**Interrupted download — do I have to start over?**
No. Partial downloads are kept as `runtime/assets/*.tmp` and resumed via HTTP `Range` requests on the next run. If a `.tmp` file refuses to go away, delete it to force a fresh download.

**The tool seems stuck or slow**
Check your internet connection, reduce the thread count (`wog-dump --max-threads 2 download-assets`), enable `--verbose` (or `--debug`), and remember that downloads are throttled by design (see `WOG_REQUEST_DELAY`).

## Contributing

Issues and pull requests are welcome — bug fixes, features, documentation, tests and localization all help. For larger changes, open an issue first to discuss the approach.

1. Fork and clone the repository, then `uv pip install -e ".[dev]"`.
2. Create a feature branch and make a focused change.
3. Add or update relevant tests and docs; run `pytest` and `black src/ tests/`.
4. Open a pull request.

- [Contributing Guidelines](CONTRIBUTING.md) — the detailed contribution process
- [Issue Tracker](https://github.com/InzGIBA/ReWOG/issues) — bug reports and feature requests
- [Discussions](https://github.com/InzGIBA/ReWOG/discussions) — questions and general help
- [Code of Conduct](CODE_OF_CONDUCT.md) — community standards

## Support

Where to get help, in the order we recommend:

1. The [FAQ](#faq) above
2. **[SUPPORT.md](SUPPORT.md)** — diagnostics and common solutions
3. **[GitHub Issues](https://github.com/InzGIBA/ReWOG/issues)** — bug reports and feature requests (public, searchable)
4. **[GitHub Discussions](https://github.com/InzGIBA/ReWOG/discussions)** — questions and general help

Please use the public channels so answers remain useful to others. For security-sensitive reports only, email the maintainer directly: [inzgiba@gmail.com](mailto:inzgiba@gmail.com).

## License

Released under the MIT License — see [LICENSE](LICENSE).

**Legal disclaimer.** This tool is intended for educational and research purposes only. Extracted assets remain the intellectual property of Noble Empire Corp.; ensure compliance with the game's Terms of Service, use extracted content only for personal, educational or research purposes, and do not redistribute game assets or use them commercially. The authors and contributors are not responsible for any misuse of this software or violation of terms of service. For questions about asset usage rights, contact Noble Empire Corp. directly.

## Acknowledgements

- [DeadZoneGarry](https://github.com/DeadZoneLuna) — original decryption research
- [Noble Empire Corp.](https://noble-empire.com/news.php) — the game and its assets
- Contributors to the original WOG Dump project
