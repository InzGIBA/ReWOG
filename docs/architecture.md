# Architecture Overview

WOG Dump is a layered command-line pipeline. The tool downloads encrypted
Unity bundles from the game's asset servers, decrypts each bundle with a
per-asset XOR key and unpacks the 3D models inside.

```
┌─────────────────────────────────────────────────────┐
│  cli/          Click commands, one module per group │
│    main.py     top-level group, global options      │
│    errors.py   shared error handling                │
├─────────────────────────────────────────────────────┤
│  core/         domain logic, no Click imports       │
│    download.py │    └─ api.py ─┬─ auth.py           │
│    keys.py     │────┘          └─ http.py           │
│    decrypt.py  │    database.py (SQLite store)      │
│    unpack.py   │    config.py  (Pydantic settings)  │
├─────────────────────────────────────────────────────┤
│  utils/        logging (stdlib + Rich), normal maps │
└─────────────────────────────────────────────────────┘
```

Dependency direction is strictly downwards: `cli → core → utils`. The
`core` modules never import from `cli`.

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
   extractor registered in `EXTRACTORS` (`TextAssetExtractor`,
   `Texture2DExtractor`, `MeshExtractor`, ...). Adding support for a new
   Unity type means adding one subclass with an `obj_type`. No changes
   to the unpacker itself are needed.
6. **Build scene** (optional): `build-scene` (`core/scene.py`) reads the
   decrypted bundle again, rebuilds the in-game assembled hierarchy,
   converts Unity's left-handed Y-up coordinates to glTF's right-handed
   system and writes a self-contained GLB through the in-package writer
   (`core/glb.py`), plus a `blender_setup.py` generated from
   `resources/blender_setup.py`.

## Module map

| Module | Responsibility |
|---|---|
| `cli/main.py` | `wog-dump` group, global flags, `main()` |
| `cli/download.py` | `download-weapons`, `download-assets` |
| `cli/decrypt.py` | `decrypt-assets`, decrypted-file validation |
| `cli/unpack.py` | `unpack-assets` |
| `cli/convert.py` | `convert-normals` |
| `cli/scene.py` | `build-scene` |
| `cli/pipeline.py` | `full-pipeline` (chains the commands above) |
| `cli/auth.py` | `login`, `logout` |
| `cli/cache.py` | `cache` (database management) |
| `cli/info.py` | `info` (status and validation) |
| `core/config.py` | Pydantic settings, env-var mapping, singletons |
| `core/http.py` | Shared session factory (retries, pooling) and `RateLimiter` |
| `core/api.py` | BZ2-over-HTTP query client shared by auth and keys |
| `core/auth.py` | Game-account login, session and device identity |
| `core/database.py` | SQLite store: weapons, keys, identity, settings |
| `core/keys.py` | Key fetching/caching + the decrypt exception hierarchy |
| `core/paths.py` | Name validation and path containment for bundle- and server-controlled names |
| `core/decrypt.py` | XOR stream decryption of embedded bundles |
| `core/download.py` | Resumable parallel asset downloads |
| `core/unpack.py` | Extractor registry and weapon-list processing |
| `core/scene.py` | Blender scene export (game hierarchy to glTF) |
| `core/glb.py` | In-package glTF 2.0 / GLB writer |
| `core/key_import.py` | Parsing of MITM captures (mitmproxy/Fiddler/flat) |
| `resources/blender_setup.py` | Template for the generated Blender setup script |
| `utils/logging.py` | stdlib `logging` + Rich handlers, performance monitor |
| `utils/normal_map.py` | Unity → standard normal map conversion |

## Conventions

- **Exceptions**: each domain has its own hierarchy rooted at a single
  base (`DownloadError`, `DecryptionError`, `UnpackError`, `AuthError`,
  `ApiError`). The CLI turns any of them into a clean one-line error and
  exit code 1 (`cli/errors.py::error_handler`).
- **Singletons**: configuration (`get_config()`/`set_config()`) and the
  logger (`get_logger()`) are process-wide. Tests reset both through
  fixtures in `tests/conftest.py`.
- **No secrets in code**: credentials and keys live exclusively in the
  per-user database (see [database.md](database.md)). Nothing sensitive
  is committed or logged.
- **Typing**: full annotations, `mypy` strict-clean, Python 3.12+.
- **Tests**: unit tests mock at the `requests.Session` / `UnityPy.load`
  boundary. Live tests (`pytest -m live`) exercise the real servers and
  auto-skip without credentials.
