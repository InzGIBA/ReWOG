# Architecture Overview

WOG Dump is a stable core plus plugins. The core is a layered
command-line pipeline: it downloads encrypted Unity bundles from the
game's asset servers, decrypts each bundle with a per-asset XOR key and
unpacks the 3D models inside, then builds scenes (assembled and
disassembly poses), converts formats and generates the offline viewer.
Optional features — the web UI, preview/video rendering, print
preparation, MITM key import — ship as plugins: separate pip packages
that register through entry points. Built-in exporters, scene states
and the default viewer register through the same mechanism from the
core distribution itself.

```
┌──────────────────────────────────────────────────────────────┐
│  plugins/    pip packages, entry points → core registry      │
│    wog-plugin-web │ wog-plugin-render │ wog-plugin-print     │
│    wog-plugin-mitm │ wog-plugin-3mf │ wog-plugin-gltf        │
│    wog-plugin-showcase │ wog-plugin-report │ (third-party)   │
├──────────────────────────────────────────────────────────────┤
│  cli/       Click commands, one module per group             │
│    main.py    top-level group + plugin command registration  │
│    errors.py  shared error handling                          │
├──────────────────────────────────────────────────────────────┤
│  core/      domain logic, no Click imports                   │
│    download.py │    └─ api.py ─┬─ auth.py                    │
│    keys.py     │────┘          └─ http.py                    │
│    decrypt.py  │    database.py (SQLite store)               │
│    unpack.py   │    config.py  (Pydantic settings)           │
│    scene.py ── scene_states.py ── scene_steps.py ── anim.py  │
│    export.py ── viewer.py ── extensions.py (plugin registry) │
│    catalog.py ── probe.py ── glb.py ── transforms.py         │
├──────────────────────────────────────────────────────────────┤
│  utils/     logging, normal maps, i18n, progress             │
└──────────────────────────────────────────────────────────────┘
```

Dependency direction is strictly downwards: `plugins → cli → core →
utils`. The `core` modules never import from `cli` or from plugins;
plugins reach the core only through its public API. Inside `core` the
newer modules keep the same rule: `scene_states.py` and
`scene_steps.py` never import from `scene.py`, they receive the tree as
a structural annotation, so the dependency stays one-way
(`scene → scene_states`).

## Plugin architecture

Five entry-point groups (`core/extensions.py`, contract version
`PLUGIN_API_VERSION`):

| Group | Contract | Built-in entries |
|---|---|---|
| `wog_dump.extractors` | per-Unity-type `AssetExtractor` | Texture2D, Mesh, … |
| `wog_dump.exporters` | `name`, `extensions`, `convert(glb_path, output_dir, **options)` | stl, fbx, usd, usdz |
| `wog_dump.commands` | Click command/group | — (optional features) |
| `wog_dump.scene_states` | `name`, `layout(root, unit_scale, stats, **options)` | field-strip, exploded, parts |
| `wog_dump.viewers` | `name`, `render_html(glb, manifest, *, linked, …)` | default |

Entries shipped by the `rewog` distribution are reported as `built-in`;
a third-party plugin overriding a built-in wins with a warning naming
its package. Broken plugins log a warning and never break the pipeline.
The contract is pinned by `tests/unit/test_plugin_api_contract.py`;
see [plugins.md](plugins.md) for authoring guidance and the scaffold
(`scripts/new_plugin.py`).

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
   and dispatches the target format through the exporter registry:
   built-in STL, USD/USDZ (through the `usd-core` extra) and FBX
   (through headless Blender), plus any plugin format (3MF, separate
   glTF, …). `core/viewer.py` renders the offline HTML viewer (the
   `default` viewer provider; alternatives plug in through
   `wog_dump.viewers`). Preview/video rendering (`wog-plugin-render`)
   and print preparation (`wog-plugin-print`) are plugins driving
   headless Blender and trimesh respectively.

## Module map

| Module | Responsibility |
|---|---|
| `cli/main.py` | `wog-dump` group, global flags (locale, no-progress), `main()` |
| `cli/download.py` | `download-weapons`, `download-assets` |
| `cli/decrypt.py` | `decrypt-assets`, decrypted-file validation |
| `cli/unpack.py` | `unpack-assets`, `--list-extractors` |
| `cli/convert.py` | `convert-normals` |
| `cli/scene.py` | `build-scene` (states, steps, animations, formats, viewer) |
| `cli/catalog.py` | `catalog` (status table, JSON schema) |
| `cli/plugins.py` | `plugins` (installed extensions, `--json`) |
| `cli/update.py` | `update` (hash/ETag asset refresh) |
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
| `core/extensions.py` | Plugin registry: five entry-point groups, API version |
| `core/optimize.py` | Optional gltfpack compression (`--optimize`) |
| `core/probe.py` | Protocol compatibility cascade (`info --protocol-probe`) |
| `core/spider.py` | System catalog bundle: icons, cards, bilingual metadata |
| `resources/blender_setup.py` | Template for the generated Blender setup script |
| `resources/viewer/` | Vendored three.js runtime and viewer templates |
| `utils/logging.py` | stdlib `logging` + Rich handlers, performance monitor |
| `utils/normal_map.py` | Unity → standard normal map conversion |
| `utils/i18n.py` | gettext wrapper for CLI messages (`t()`, `tn()`) |
| `utils/progress.py` | Byte-level download progress tracker |

### Plugins (separate packages in `plugins/`)

| Package | Registers | Responsibility |
|---|---|---|
| `wog-plugin-web` | `serve` command | Local web UI (FastAPI, token-guarded, job queue) |
| `wog-plugin-render` | `render` command | Headless Blender driver: previews, GIFs, videos |
| `wog-plugin-print` | `print-export` command | Print diagnostics, repair, orientation, estimates |
| `wog-plugin-mitm` | `import-keys` command | Parsing of MITM captures (mitmproxy/Fiddler/flat) |
| `wog-plugin-3mf` | `3mf` exporter | Print-ready 3MF packages |
| `wog-plugin-gltf` | `gltf` exporter | Separate `.gltf` + `.bin` + textures |
| `wog-plugin-showcase` | `showcase` state | Circular carousel layout |
| `wog-plugin-report` | `scene-report` command | Markdown/JSON scene reports |

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
