# Architecture Overview

WOG Dump is a layered command-line pipeline. The tool downloads encrypted
Unity bundles from the game's asset servers, decrypts each bundle with a
per-asset XOR key and unpacks the 3D models inside. On top of the
pipeline sit the scene exporter (assembled and disassembly poses), the
format converters, the viewer and preview/video renderers, the print
preparer, the catalog, the protocol probe and a local web UI.

```
┌──────────────────────────────────────────────────────────────┐
│  cli/       Click commands, one module per group             │
│    main.py    top-level group, global options                │
│    errors.py  shared error handling                          │
├──────────────────────────────────────────────────────────────┤
│  core/      domain logic, no Click imports                   │
│    download.py │    └─ api.py ─┬─ auth.py                    │
│    keys.py     │────┘          └─ http.py                    │
│    decrypt.py  │    database.py (SQLite store)               │
│    unpack.py   │    config.py  (Pydantic settings)           │
│    scene.py ── scene_states.py ── scene_steps.py ── anim.py  │
│    export.py ── viewer.py ── render.py ── printprep.py       │
│    catalog.py ── probe.py ── glb.py ── transforms.py         │
├──────────────────────────────────────────────────────────────┤
│  utils/     logging, normal maps, i18n, progress             │
└──────────────────────────────────────────────────────────────┘
```

Dependency direction is strictly downwards: `cli → core → utils`. The
`core` modules never import from `cli`. Inside `core` the newer modules
keep the same rule: `scene_states.py` and `scene_steps.py` never import
from `scene.py`, they receive the tree as a structural annotation, so
the dependency stays one-way (`scene → scene_states`).

## The pipeline

1. **Download weapon list**: `spider_gen.unity3d` from the data server
   (`core/download.py`). `WeaponListProcessor` (`core/unpack.py`) parses
   the `new_banners` TextAsset inside the bundle into the weapon list and stores
   the list in the database.
2. **Fetch keys**: `KeyManager` (`core/keys.py`) asks the query API for
   a per-asset `sync` key. The API speaks BZ2-compressed `key=value`
   payloads over HTTP PUT (`core/api.py`). Authentication is your own
   game account (`core/auth.py`). Keys are cached in the database.
3. **Download assets**: each weapon's `*.unity3d` bundle
   (`core/download.py`): parallel, rate-limited, resumable via HTTP
   `Range` requests, validated against the server-declared size.
4. **Decrypt**: `AssetDecryptor` (`core/decrypt.py`) expands each key
   to an MD5 digest (`md5(key + "World of Guns: Gun Disassembly")`) and
   XORs the encrypted bundles embedded in the asset with the digest as a
   repeating key stream.
5. **Unpack**: `AssetUnpacker` (`core/unpack.py`) loads each decrypted
   bundle with UnityPy and dispatches every object to a per-type
   extractor. The registry merges built-ins with third-party plugins
   from the `wog_dump.extractors` entry-point group; a plugin wins over
   a built-in on the same type, a broken plugin only logs a warning.
6. **Build scene**: `build-scene` (`core/scene.py`) reads the
   decrypted bundle, rebuilds the in-game assembled hierarchy,
   converts Unity's left-handed Y-up coordinates to glTF's right-handed
   system and writes a self-contained GLB through the in-package writer
   (`core/glb.py`), plus a `blender_setup.py` generated from
   `resources/blender_setup.py`.
7. **Scene poses**: before emission, a state pass rewrites node
   translations (`core/scene_states.py`): `field-strip` moves parts to
   the game's own removal anchors (`<part>_out` transforms in the
   bundle), `exploded` pushes them along removal directions, `parts`
   lays them out in a grid. `core/scene_steps.py` plans the step-by-step
   build (`--assembly-steps`), and `core/anim.py` extracts the operate
   animation curves for `--animations` and the placing animation for
   `--animated`.
8. **Formats and extras**: `core/export.py` reads the written GLB back
   and converts it to STL, USD/USDZ (through the `usd-core` extra) or
   FBX (through headless Blender). `core/viewer.py` renders the offline
   HTML viewer, `core/render.py` drives headless Blender for turntable
   previews and videos, `core/printprep.py` repairs parts and writes
   slicer-ready STLs with a per-part watertight report.

## Module map

| Module | Responsibility |
|---|---|
| `cli/main.py` | `wog-dump` group, global flags (locale, no-progress), `main()` |
| `cli/download.py` | `download-weapons`, `download-assets` |
| `cli/decrypt.py` | `decrypt-assets`, decrypted-file validation |
| `cli/unpack.py` | `unpack-assets`, `--list-extractors` |
| `cli/convert.py` | `convert-normals` |
| `cli/scene.py` | `build-scene` (states, steps, animations, formats, viewer) |
| `cli/render.py` | `render` (turntable previews, `--video`) |
| `cli/print_export.py` | `print-export` |
| `cli/catalog.py` | `catalog` (status table, JSON schema) |
| `cli/serve.py` | `serve` (local web UI) |
| `cli/pipeline.py` | `full-pipeline` (chains the commands above) |
| `cli/auth.py` | `login`, `logout`, `register` |
| `cli/cache.py` | `cache` (database management) |
| `cli/info.py` | `info` (status, validation, `--protocol-probe`) |
| `cli/errors.py` | shared error handling, exit codes |
| `core/config.py` | Pydantic settings, env-var mapping, singletons |
| `core/http.py` | Shared session factory (retries, pooling) and `RateLimiter` |
| `core/api.py` | BZ2-over-HTTP query client shared by auth and keys |
| `core/auth.py` | Game-account login, session, shard redirect, device identity |
| `core/database.py` | SQLite store: weapons, keys, identity, settings |
| `core/keys.py` | Key fetching/caching + the decrypt exception hierarchy |
| `core/paths.py` | Name validation and path containment for bundle- and server-controlled names |
| `core/decrypt.py` | XOR stream decryption of embedded bundles |
| `core/download.py` | Resumable parallel asset downloads with byte-progress callbacks |
| `core/unpack.py` | Extractor registry, plugin loading, weapon-list processing |
| `core/catalog.py` | Per-weapon pipeline status (database + runtime listing) |
| `core/scene.py` | Scene export: assembled and disassembly poses, steps, annotations |
| `core/scene_states.py` | Pure pose planning: field-strip, exploded, grid |
| `core/scene_steps.py` | Step planning: counts, animation windows, node paths |
| `core/anim.py` | AnimationClip curve extraction (operate animation) |
| `core/transforms.py` | Column-major 4x4 math shared by scene and states |
| `core/glb.py` | In-package glTF 2.0 / GLB writer (meshes, materials, animations) |
| `core/export.py` | Format conversion: STL, USD/USDZ, FBX (`parse_glb` + registry) |
| `core/viewer.py` | Offline HTML viewer generation (three.js embedded) |
| `core/render.py` | Headless Blender driver: previews, GIFs, videos |
| `core/printprep.py` | Print diagnostics and repair through trimesh |
| `core/probe.py` | Protocol compatibility cascade (`info --protocol-probe`) |
| `core/key_import.py` | Parsing of MITM captures (mitmproxy/Fiddler/flat) |
| `core/webapp/app.py` | Local web UI (FastAPI, token-guarded) |
| `resources/blender_setup.py` | Template for the generated Blender setup script |
| `resources/render_turntable.py` | Template for preview renders |
| `resources/render_video.py` | Template for video renders |
| `resources/viewer/` | Vendored three.js runtime and viewer templates |
| `utils/logging.py` | stdlib `logging` + Rich handlers, performance monitor |
| `utils/normal_map.py` | Unity → standard normal map conversion |
| `utils/i18n.py` | gettext wrapper for CLI messages (`t()`, `tn()`) |
| `utils/progress.py` | Byte-level download progress tracker |

## Conventions

- **Exceptions**: each domain has its own hierarchy rooted at a single
  base (`DownloadError`, `DecryptionError`, `UnpackError`, `AuthError`,
  `ApiError`, `ExportError`, `RenderError`, `KeyImportError`). The CLI
  turns any of them into a clean one-line error and exit code 1
  (`cli/errors.py::error_handler`).
- **Singletons**: configuration (`get_config()`/`set_config()`) and the
  logger (`get_logger()`) are process-wide. Tests reset both through
  fixtures in `tests/conftest.py`.
- **No secrets in code**: credentials and keys live exclusively in the
  per-user database (see [database.md](database.md)). Nothing sensitive
  is committed or logged.
- **Typing**: full annotations, `mypy` strict-clean, Python 3.12+.
- **Tests**: unit tests mock at the `requests.Session` / `UnityPy.load`
  boundary. Integration tests exercise real bundles from the local
  cache and auto-skip when absent. Live tests (`pytest -m live`) hit
  the real servers and auto-skip without credentials. Coverage gate:
  85 percent (`.coveragerc`).
- **Localization**: user-facing CLI strings wrap English msgids through
  `utils/i18n.py`; catalogs live in `resources/locales/`. Unknown
  locales fall back to English.
