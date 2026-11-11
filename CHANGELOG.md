# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.3.0] - 2026-11-11

### Added

- Server-side test markers are visible now. The spider metadata
  carries `flags` and `released` fields, parsed into the
  `weapon_meta` table (existing databases gain the columns through
  an additive migration). Six live weapons are flagged `test`
  (beretta_92x, foss_mg9, hudson_h9, ppk_42, type_b, uru): the game
  hides them and their asset bundles are not deployed, so downloads
  return 404. The catalog, the web UI and the weapon API surface a
  `test` flag, and the web card and detail pages show a test badge
- Catalog JSON schema v3 (`wog-dump.catalog/3`). Entries add
  `test`. The Blender addon client pins the new schema

### Changed

- `download-assets` drops test-flagged weapons before the batch with
  a clear warning instead of a raw 404 line, and the summary counts
  skips separately. A request that downloads nothing and skips one
  weapon now reports "0 up to date, 1 skipped (not on server)" as
  info instead of a green "All 1 assets up to date"

## [3.2.0] - 2026-11-11

### Added

- Weapon cards come from the game now. `download-weapons` reads the
  spider bundle and extracts a 256x256 icon and a 1024x1024 studio
  render per weapon into `runtime/previews/`. The same pass parses
  the per-model metadata: display names in English and Russian,
  categories, parts count, year, brief info and links. Records land
  in a new `weapon_meta` database table. Verified against the live
  bundle: 288 icons, 288 renders, 288 records
- Catalog JSON schema v2 (`wog-dump.catalog/2`). Entries add
  `display_name`, `categories`, `num_parts`, `since` and `icon`. The
  Blender addon client pins the new schema and ships with this
  release

### Changed

- The serve web UI is one Alpine.js shell over REST. Vendored
  Alpine 3.14 drives three hash-routed views: the catalog card
  grid, the weapon detail page and the 3D viewer. A persistent
  sidebar holds the search, the spider categories with counts, the
  per-weapon jump links and the collapsible stage log. Every
  rendered string goes through `x-text`, so game-controlled text
  never reaches `innerHTML`. Stage runs stay REST `POST /api/run`
  plus the SSE log stream
- The weapon detail page shows the extraction pipeline as a
  circle stepper: key, download, decrypt, unpack, scene, renders.
  Circles and labels share one grid cell, so the centers line up.
  The wire into a stage lights up once the previous stage is done.
  The weapon API payload carries every stage flag
- `/weapon/<name>` serves the same shell as `/`. The weapon page
  and its per-page module are gone. The annotated viewer mounts
  in-view as an iframe
- `spider_gen` is a system asset now. The catalog, the web UI and
  the stage targets exclude it, so it never shows up as a weapon

### Fixed

- The serve "unpack" button failed with `No such option:
  --weapons`. The web runner passed `--weapons` to every stage,
  while `unpack-assets` filters by `--asset-filter`. The runner
  picks the flag per stage now
- `serve` printed `gio: ...: Operation not supported` on headless
  Linux (WSL, servers). The auto-open fell back to desktop helpers
  whose subprocesses inherit stdio and fail noisily. The open runs
  with stdio parked at devnull (`open_browser_quietly`), and a
  "no browser found" hint appears when nothing could launch

## [3.1.0] - 2026-11-11

### Changed

- **The serve web UI uses the smui theme.** A port of
  [InzGIBA/smui](https://github.com/InzGIBA/smui): Nord-inspired
  palette in dark and light modes, Source Code Pro everywhere, zero
  border radius, uppercase controls and 5px status dots. The catalog
  page gains a header with weapon counts, aurora status dots for the
  stage flags, a "no weapons match" empty state and a `/` shortcut
  for the filter field. The weapon page panel becomes a bordered card
  with a primary build button and outline stage buttons. The full
  viewer shares the palette. The `/view` route loads the vendored
  font through `render_viewer_html(..., font_url_base=...)`, while
  offline viewer files fall back to the system monospace stack. The
  theme ships as one shared stylesheet (`webapp/static/smui.css`)
  with the Source Code Pro variable font vendored under
  `webapp/static/fonts/` (90 KB woff2, OFL license).

## [3.0.1] - 2026-11-11

### Fixed

- **Images now render on the PyPI project page.** PyPI strips
  `<picture>` blocks, blocks SVG and cannot resolve repository-relative
  paths, so the hero banner and feature cards vanished from
  pypi.org/project/rewog. The release workflow now rewrites the
  long description before the build (`scripts/build_pypi_readme.py`,
  same approach as ReString): the hero becomes one image with an
  absolute URL (new committed `assets/social-preview.png`), feature
  cards collapse into the bullet list, and every repository-relative
  link becomes an absolute GitHub URL. The repository README keeps the
  dark/light picture rendering for GitHub.

## [3.0.0] - 2026-11-11

The PyPI distribution is named rewog; the CLI stays wog-dump.

A major release built around one idea: the toolkit now covers the whole
life of an extracted weapon, from download to disassembly poses,
viewers, print parts and video. Everything ships without breaking the
old commands. New capabilities switch on through flags and extras, so
existing pipelines keep running unchanged. The test suite grew from 445
to 569 tests and a coverage gate now holds at 85 percent.

### Added

- **Disassembly states in `build-scene`**: `--state field-strip` puts
  parts where the game itself puts them after removal (the bundles
  carry per-part `<part>_out` anchors, documented in
  `docs/research-disassembly-data.md`), `--state exploded` pushes them
  along removal directions, `--state parts` lays them out in a grid.
  Non-assembled scenes go to `<weapon>-<state>` folders.
- **Assembly steps**: `--assembly-steps N` writes N GLBs with more
  parts placed at each step plus `steps.json`. `--animated` adds
  `assembly.glb`, one animation placing part after part.
- **Operate animations**: `--animations` embeds the bundle's legacy
  clip as glTF animation tracks (344 channels for ak47).
  `--animation <name>` limits the export to one clip.
- **HTML viewer**: `--viewer` produces one self-contained
  `viewer.html` (GLB and three.js embedded, works offline).
  `--annotate` adds part markers with size cards. `--viewer-link`
  writes a folder variant for big scenes.
- **Export formats**: `--format stl,usd,usdz,fbx` converts a built
  scene. STL is self-contained with unit baking and a per-part mode.
  USD/USDZ needs the new `rewog[usd]` extra. FBX goes through
  headless Blender (`WOG_BLENDER_PATH`).
- **`wog-dump print-export`** (extra `rewog[print]`): repairs parts
  with trimesh, reports watertight status per part and writes
  millimeter-true STLs, single or merged.
- **`wog-dump render`**: turntable previews through headless Blender
  (transparent PNGs plus GIF). `--video` encodes an orbit MP4 or one
  animation clip. `--all` sweeps every built scene.
- **`wog-dump catalog`**: per-weapon pipeline status with filters,
  sorting and a stable JSON schema (`wog-dump.catalog/1`).
- **`wog-dump serve`** (extra `rewog[web]`): local web UI with the
  catalog, a 3D viewer per weapon and stage buttons streaming live
  logs. Token-guarded, bound to 127.0.0.1.
- **`wog-dump info --protocol-probe`**: checks endpoint reachability,
  the anonymous query, the authenticated key query, the weapon list
  and the client version. Exits 2 on any failure. Golden protocol
  fixtures and a drift issue template ship with the repo.
- **Extractor plugins**: third-party packages register extractors
  through the `wog_dump.extractors` entry-point group. A plugin wins
  over a built-in, a broken plugin only logs. Example plugin in
  `examples/plugin-sprite/`, guide in `docs/plugins.md`.
- **Localization**: `--locale ru` (or `WOG_LOCALE`) switches CLI
  messages to Russian through gettext. Unknown locales fall back to
  English. Pure-stdlib `.po` compiler in `scripts/`.
- **Docker**: image with the CLI as entrypoint and `/data` state
  volume. GHCR publishing on release tags.
- **Blender addon** (`blender-addon/`): catalog panel, scene import and
  batch building inside Blender 4.2+, talking to the CLI only.
- **Documentation site**: mkdocs-material with an API reference
  (mkdocstrings), deployed to GitHub Pages.
- **Coverage gate**: 85 percent enforced through `.coveragerc`.

### Changed

- **Downloads report byte progress** through a new optional callback.
  The CLI shows one aggregate bar with speed and ETA. `--no-progress`
  (or `WOG_NO_PROGRESS`, or any piped output) switches every bar off.
- **Scene emission refactored** into one shared helper, so single
  scenes, steps and annotations use the same pipeline. The assembled
  ak47 GLB stays byte-identical. A mesh-cache bug found during the
  quality pass (id reuse across builds could hand one scene another
  part's geometry) is fixed and guarded by a test.
- **`key_import` error handling**: unreadable mitmproxy dumps now raise
  `KeyImportError` with an actionable message for both keys and
  identity extraction, and identity extraction warns when the winning
  capture came from a non-game host.
- **flake8 got a config** (`setup.cfg`, black-compatible) and the
  docs gained a mkdocs site. Both enforce in CI.

### Evaluated and declined

- **Async downloads**: a live benchmark measured a 1.9 percent gain
  for asyncio/httpx against a 15 percent threshold. The threaded
  implementation stays. `scripts/bench_download.py` and the numbers in
  `docs/benchmarks-download.md` keep the decision reproducible.
- **Content-addressed storage**: a full duplicate audit over 87,163
  unpacked files found 3.4 percent reclaimable volume against a
  10 percent threshold. `scripts/audit_duplicates.py` stays for a
  re-run when the cache grows.

## [2.7.1] - 2026-11-09

### Fixed

- **`wog-dump login` failed with `result=777` for accounts on a non-EU1
  shard** (#2): game accounts live on one regional server (eu1/eu2/us3/
  us4), and a login sent to the wrong one is answered with `result=777`
  plus a `server=` field naming the home host. The tool now follows that
  redirect exactly like the standalone client (one hop, only to a host
  under the game's own domain), remembers the home shard in the local
  database and goes straight there on later runs. `WOG_API_BASE_URL` is
  the probe endpoint for the first login (and after logout); once the
  server has confirmed the account's home shard, the remembered shard
  takes over and a conflicting `WOG_API_BASE_URL` is reported in the
  log. `logout` clears the remembered shard with the account
- Login error messages are URL-decoded for display: the raw server
  narration `..shard+found+%21` now reads `..shard found !` instead of
  `..shard found %21`

## [2.7.0] - 2026-11-08

Email/password login rewritten to match the standalone game client, from a
fresh mitmproxy capture of its registration and login flows. Every change was
verified live against the production API (login and key fetch, Nov 2026).

### Fixed

- **`wog-dump login` (email + password) never worked against the query2026
  API**: the request did not match the standalone client's protocol at any
  point. It now replicates the captured client exactly
  - the password travels as its MD5 hex digest under `pass=` (was: plain
    text under `password=`, which the server does not read)
  - `session=0&id=0` pre-session markers, `lang=`, `sys_info=` and the
    standalone `platform=` field are sent (were: missing)
  - requests PUT to the bare `/v/query2026.php`; the `?soc=steam` marker is
    gone (a Steam-client trait that breaks email/password queries)
  - every query carries a 32-hex `hash=` signature like the game client's
    (the server does not verify it, but the request shape now matches)
  - values are form-encoded the way Unity's client does it: lowercase
    percent hex, spaces as `+`
- **Successful logins were rejected anyway**: the response parser expected
  `id=`, but the API answers with `acc_id=` - so even a correct request
  failed with "Login response did not contain id/session". The parser now
  reads `acc_id` (with `id=` kept as a legacy fallback) and the numeric
  session counter
- **Key queries aligned with the captured client**: the `query=3` sync-key
  request uses the client's field order and carries the `hash=` signature

### Added

- **`wog-dump register`**: creates a new game account through the
  standalone client's registration flow (`query=39`, plain-text password
  exactly as captured, server sends a confirmation email) and logs in right
  away, so key fetching works without further steps
- `WOG_LANG` setting (default `en`): the two-letter language code sent with
  login queries
- `AuthManager.register()` and the standalone login shape are covered by
  unit tests built purely from synthetic fixtures (dummy accounts, no
  credentials from any capture)

### Changed

- Default `WOG_GAME_VERSION` is `2.2.3t6` (the standalone client build from
  the capture; was `2.2.3u9` from the Steam client)

## [2.6.2] - 2026-11-02

Documentation-only release. No functional changes.

### Documentation

- **All markdown files reworked for a plainer style**: short active sentences, direct address ("you"), semicolons and dash punctuation removed everywhere
- **README**: "What's New" updated from v2.5 to v2.6 (security hardening in 2.6.0, CLI fixes in 2.6.1), `core/paths.py` added to the project structure, FAQ questions moved to the "How do I ..." form
- **docs/configuration.md**: `.env` files documented as `WOG_*`-only, resume documented as requiring the `.tmp.meta` validator sidecar (behavior from 2.6.0)
- **docs/database.md**: symlinked `WOG_DB_PATH` refusal and the once-per-database `cache --migrate` (with `--force`) documented (behavior from 2.6.0)
- **docs/architecture.md**: `core/paths.py` added to the module map
- **CONTRIBUTING, SUPPORT and the GitHub templates**: wording tightened, filler words removed

## [2.6.1] - 2026-11-02

Bug-fix release from a full pass over every CLI command (info, cache, login,
logout, download-weapons, download-assets, decrypt-assets, unpack-assets,
convert-normals, build-scene, full-pipeline and the standalone
wog-convert-normals entry point), each exercised against real game data.

### Fixed

- **`download-weapons` failed with "No weapon list found in 'new_banners'"**:
  the server now ships the `new_banners` TextAsset as a texture-atlas index
  (`<name>.png<tab>new_bannersN.dds, ...`) instead of a plain one-name-per-line
  list. The parser takes the first whitespace-separated token of each line and
  supports both formats, so the weapon list imports again (288 weapons verified
  against the live server)
- **`unpack-assets` silently extracted zero materials** (visible only in debug
  logs as `'PPtr' object has no attribute 'get'`): the material dumper assumed
  dict-shaped data, while current UnityPy returns typed objects (PPtr shader
  references, `(name, value)` tuples, ColorRGBA). Both shapes are now handled
  and `<name>.material.json` dumps (colors, floats, texture references) are
  written again
- **`decrypt-assets --weapons` ignored the filter** and decrypted every asset
  in `runtime/assets/`. The weapon list is now passed through to the decryptor:
  only the requested assets are processed, and requested-but-not-downloaded
  names produce a warning instead of being silently dropped

### Improved

- **`download-assets` reports "All N assets up to date"** when nothing needs
  downloading. Previously the command finished silently with no output at all
- **`info --validate-files` says "No decrypted files to validate"** instead of
  printing a bare section header when the decrypted directory is empty

### Tests

- New unit tests for the atlas-format weapon list parser, the typed-UnityPy
  material extraction, and the `decrypt-assets` weapons filter. The full
  suite (308 tests) passes

## [2.6.0] - 2026-11-02

Security-hardening release based on a full deep-profile audit of the codebase
(21 coverage units, 3 hunting waves, every finding independently verified with
sandboxed reproductions). **Some changes are deliberately breaking**: stricter
validation and safer defaults replace behavior the audit confirmed as
exploitable. Read the "Breaking" section before upgrading. Automation or
scripts which relied on the old permissive behavior need adjustments.

### Breaking

- **Working-directory `.env` files now apply only `WOG_*` keys.** Variables
  like `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `REQUESTS_CA_BUNDLE`,
  `CURL_CA_BUNDLE` or `NETRC` in a `.env` are ignored: a `.env` planted in an
  unpacked shared "weapon pack" previously redirected all credential-
  bearing traffic through an attacker proxy or swapped the TLS trust anchor.
  Set transport variables in your real shell environment if you need them
- **Ambient transport trust is disabled for the tool's own sessions.**
  `create_session()` now pins `trust_env = False`, so even real environment
  variables no longer steer proxies/CA bundles/netrc for the game API and CDN
  traffic. If you legitimately routed `wog-dump` through a proxy, configure
  the proxy at the OS level (transparent proxy, VPN) instead
- **Asset and weapon names must match the vendor alphabet**
  (`letters, digits, _ and -`, 2-50 chars). `--weapons '../foo'`,
  absolute paths, or names with `/`, `\`, `?`, `&`, spaces etc. are rejected
  with an error instead of being downloaded to a path outside
  `runtime/assets/`. The server-parsed weapon list (`spider_gen`) is filtered
  by the same allowlist, and `runtime/weapons.txt` lines failing the allowlist
  are skipped (with a warning)
- **Cross-run download resume now requires a validator.** A partial
  `runtime/assets/<name>.tmp` is resumed only when its sidecar
  `<name>.tmp.meta` records the ETag/Last-Modified of the original response
  (replayed as `If-Range`). Partials without a validator, or where the server
  answers 200 (representation changed), restart from scratch. Partials created
  by versions before 2.6.0 have no sidecar and will be re-downloaded once
- **Legacy `runtime/*.txt` import is once per database.** `cache --migrate`
  refuses to re-import after the first successful run (use the new
  `--force` flag to override), legacy keys never overwrite keys already
  fetched from the API, and the weapons side merges additively instead of
  replacing the stored list. `cache --clear` is now terminal: the next
  command run from the same directory no longer silently resurrects the
  cleared weapons/keys from `runtime/*.txt`
- **`extract_keys.py --identity` refuses to overwrite an existing stored
  identity** unless the new `--force` flag is passed. Captured identity
  extraction also prefers requests on a genuine `ultimate-disassembly.com`
  host over shape-only matches, regardless of order in the capture
- **Symlinked database paths are refused** (`WOG_DB_PATH` pointing through a
  symlink now errors out instead of writing credentials through the link), and
  `logout` also clears the stored `device_id`
- Unity bundle object names (`m_Name`) containing `/`, `\`, NUL, leading
  dots or traversal segments are skipped with a warning instead of being
  written wherever the names point (previously: arbitrary file write outside
  `runtime/` at the invoking user's privilege)

### Security

- **Fixed: ambient environment took control of transport trust** (proxies,
  CA bundle, netrc) for every authenticated request. The account
  email/password, session/id/dev tokens and key responses were interceptable
  by a planted `.env`. Both clients (`WOGApiClient`, `DownloadManager`) now
  use a pinned session (finding `http.trustenv-proxy-ca-injection`, medium)
- **Fixed: path traversal via bundle object names.** `m_Name` values from
  crafted `.unity3d` bundles were joined verbatim into output paths in
  decrypt, all seven unpack extractors and scene texture export, writing
  attacker-chosen bytes outside `runtime/` (`bundle-object-name-path-traversal`,
  medium). All sinks now go through the new `core/paths.py`
  (`safe_object_name`/`contained_path`)
- **Fixed: planted `.tmp` symlink redirected downloads** to any
  victim-chosen file (overwrite via HTTP 200, append via 206 resume).
  Temp files are opened with `O_NOFOLLOW` and non-regular temps are
  discarded (`download.tmp-symlink-follow`, medium)
- **Fixed: resumed downloads certified foreign prefixes.** A stale or
  planted `.tmp` prefix became part of a "validated" final file, and the
  size-only freshness check then treated the mixed file as valid forever.
  Resume is now bound to the server representation via `If-Range`/ETag
  (`download.resume-prefix-unbound`, medium)
- **Fixed: unvalidated weapon names placed downloads outside
  `runtime/assets/`** via traversal or absolute names from `--weapons`,
  `weapons.txt` or the server-parsed list. URL building is also confined
  (`download.asset-name-path-traversal`, medium). Note: the pinned HTTP
  client normalizes dot segments before sending, so traversal names
  requested CDN-root paths on the wire
- **Fixed: planted `.env` silently poisoned the credential store**: a
  foreign `WOG_AUTH_ID`/`WOG_AUTH_SESSION` pair was persisted as the live
  identity, and a planted `WOG_DB_PATH` relocated the store (including
  through a symlink). Besides the `.env` allowlist, applying credential-affecting keys
  from a working-directory file now prints a prominent warning
  (`env.cwd-dotenv-credential-store-trust`, medium)
- **Fixed: scene export wrote through pre-existing symlinks**
  (`blender_setup.py`, `<weapon>.glb`, `scene.json`, `textures/`) in a
  provenance-unknown output tree, clobbering files outside `runtime/`. All
  scene writes now use temp-file + `os.replace`, and symlinked output or
  textures directories are refused (`scene.symlink-output-write`, low)
- **Fixed: duplicate asset names raced on the shared temp path** and
  sometimes produced a size-correct corrupt final file which passed
  validation and was cached as up-to-date. Batches are de-duplicated before
  the thread pool (`download.duplicate-name-temp-interleave`, low)
- **Fixed: `wog.log` was world-readable (0644) while the database got 0600**,
  leaking the account email and auth id to other local users on default
  umask. The log directory is now 0700 and every log file (including
  rotations) 0600 (`log.account-identity-world-readable`, low)
- **Fixed: shape-matched capture flows overwrote the live identity.**
  The first query-shaped request in a capture won identity selection even
  on unrelated hosts. A genuine-domain flow later in the same file now
  takes precedence, and overwrites require `--force`
  (`key-import.shape-match-identity-import`, low)
- New `core/paths.py` module centralizes name validation and path
  containment for everything building paths from bundle-, server- or
  file-controlled names

### Fixed

- **`result=1000` (session rejected) now triggers the documented automatic
  re-login.** Only `result=100` raised `AuthenticationError` before, so a
  routine session expiry silently misreported every weapon as keyless until
  a manual `wog-dump login`
- Login and key-fetch query values are URL-encoded: an `&` or `=` inside
  the email, password or model name no longer rewrites the request's own
  fields
- A non-numeric `Content-Length` header no longer crashes the batch with an
  uncaught `ValueError`
- `full-pipeline --continue-on-error` no longer reports a clean finish (and
  exit 0) after a step failed internally. The console says "finished with
  errors" and the exit code is 1
- Full tracebacks of unexpected errors go to the log file only. The console
  shows the one-line error (traceback on screen with `-d`, as the comments
  always claimed)

### Changed

- API response decompression is capped (64 MiB) to stop decompression-bomb
  input from exhausting memory
- `cache --show-cache` / `info` statistics no longer expose the raw session
  token (`get_stats()` reports `has_session` instead), and the stored keys'
  per-row source provenance survives `--update-keys` writes
- `wog-dump login --password ...` prints a warning about the value being
  visible in the process list (e.g. `/proc/<pid>/cmdline` on shared hosts).
  Prefer the interactive prompt

## [2.5.1] - 2026-11-01

Documentation-only release. No functional changes.

### Documentation rework

- **All markdown files reworked**: emojis removed everywhere, em dashes replaced with plain punctuation, formatting made consistent across README, CHANGELOG, CONTRIBUTING, SUPPORT and the `docs/` pages
- **README**: table-of-contents anchors fixed, new "Who this tool is for" and "Support" sections (recommended help channels, maintainer status), broken links to non-existent files removed, instructions referencing non-existent tooling corrected, project structure updated with `resources/` and the single version source, new project banner
- **CONTRIBUTING**: new "Ways to Contribute" (including non-code paths and the `good first issue` label), "Project Scope" and "What to Expect" (response times, review criteria) sections. Outdated instructions fixed (release process uses the single version source, CLI commands live in per-group modules, no pre-commit/tox/docker references)
- **SUPPORT**: log locations corrected to the single rotating `wog.log` next to the database, reset instructions replaced with real commands (`cache --clear`, `logout`), removed references to non-existent `requirements.txt` and `config.json`
- **CHANGELOG**: emoji headers removed, verbose historical entries condensed while keeping all facts
- **docs/architecture.md**: scene export step and the `scene.py`/`glb.py`/`resources` modules added to the pipeline and module map
- **Old changelog quotes of console output paraphrased** so the documentation no longer reproduces typographic dashes

### Added

- **CODE_OF_CONDUCT.md** (Contributor Covenant 2.1) with a private reporting address
- **GitHub issue templates** (bug report, feature request) and a **pull request template**

## [2.5.0] - 2026-11-01

### Blender scene export

- **New `wog-dump build-scene <weapons>` command**: rebuilds the in-game assembled weapon as a glTF 2.0 scene ready for Blender. The scene contains the full GameObject/Transform hierarchy with the exact local positions and rotations the game uses, every part covered with its game materials (atlas textures and flat colors), a fitted camera and a three-point studio light rig, all in one `runtime/scenes/<weapon>/<weapon>.glb`
- **Only real weapon geometry by default**: the game bundle's UI objects, meaning red/green assembly arrows (`arrow_in`/`arrow_out`), cutaway (`*_cut`) meshes, muzzle-flash/gas effects and 246 empty `transformN` dummies, are classified by material and skipped. `--extras arrows|cut|vfx|all` exports them alongside for anyone who wants them
- **Correct handedness, units and UVs**: Unity's left-handed Y-up coordinates are mirrored to glTF's right-handed system (positions `(x, y, -z)`, quaternions `(-x, -y, z, w)`, triangle winding flipped, UV V inverted), and centimeters are baked to meters so the weapon imports at true size (assembled AK-47 with bayonet spans 1.36 m)
- **Generated `blender_setup.py` next to every scene**: one click inside Blender (or `blender -b -P blender_setup.py`) imports the GLB, sorts objects into `Gun`/`Arrows`/`Cutaway`/`VFX`/`Studio` collections, hides the extras exactly like the game's default view, sets the active camera and saves a `.blend`
- **`scene.json` manifest** records mesh/material/texture counts, what was skipped, and the game's own removable-parts (`ak47_parts`) and field-strip (`ak47_field_strip`) lists extracted from the bundle's TextAssets
- **Zero new dependencies**: the GLB writer (buffer views, accessors, PBR materials, embedded PNGs, KHR_lights_punctual) is implemented in-package. Geometry comes from UnityPy's `MeshHandler`
- Verified on real bundles: AK-47 (173 meshes, 22 materials, 8 textures, world bounds match a real AK-47, vertex data matches the reference OBJ exports bit-for-bit on extents) and Colt 1911 (79 meshes). 35 new unit/integration tests cover the GLB writer, coordinate conversion and the end-to-end export

### Fixed: scene export

- **`build-scene` GLB now imports into Blender**: light nodes referenced the KHR_lights_punctual extension as a bare index (`"KHR_lights_punctual": 0`), which Blender's strict `gltf_from_dict` rejects with "Couldn't parse glTF. Check that the file is valid". The spec (and Blender's own exporter) requires an extension object (`"KHR_lights_punctual": {"light": 0}`). Both exported scenes now parse cleanly through Blender's actual converter code (fetched and run locally as the acceptance test), and regression tests pin the extension object shape

### Console redesign

- **One aligned line per pipeline step**: a single line with a green check mark, the step name, the result summary and the duration (for example a download step reporting 237 assets and 2 missing on the server) replaces the nested "Starting/Completed" pairs printing up to six lines per logical step. A failed step prints a red cross line
- **Progress bars are transient**: they disappear when finished, so a completed run leaves only step lines, warnings and the final summary (total time plus the output location) instead of a wall of 100%-complete bars
- **Batched downloads run under one aggregate bar** for the whole list instead of a "Checking updates" + "Downloading assets" bar pair per 50-asset batch
- **Warnings are compact one-liners** without the `[11/01/26 01:57:57] WARNING` developer formatting. Timestamps, logger names and locations stay in the log file. Wording starts with the weapon name (`foss_mg9: key API error (code 2)`, `beretta_92x: not found on server, skipped`), and the redundant end-of-run key-fetch summary moved to the file
- **`full-pipeline --update-keys` no longer fetches all 288 keys twice**: the download and decrypt steps each ran their own ~72s key update. Decrypt now reuses the keys fetched moments earlier (and only updates the keys itself when `--skip-download` is set)
- **No more 900-row performance dump**: the timing table is opt-in (`full-pipeline --performance`), aggregates per-item metrics into one row per operation (`download (287 items)`) and is capped to the 12 slowest
- Slim one-line banner instead of the full-width panel. Dropped the fake "Pipeline Overview" checklist, the "Sample weapons (first 10)" listing and the duplicated final messages. The 2-minute file validation pass now shows a progress bar instead of running silently between two status lines

### Fixed: unpacking and textures

- **Meshes export as real OBJ geometry instead of empty stubs**: Unity 6 stores vertices in packed `m_VertexData` while the legacy `m_Vertices` array is `None`, so every exported `.obj` was a 2-line header (all 175 Colt 1911 meshes). Extraction now renders OBJ text through UnityPy's mesh exporter (verified on real data: 175/175 files with vertices, UVs and faces). The old manual writer remains as a fallback for meshes with legacy arrays
- **Swizzled normal maps no longer come out semi-transparent**: Unity (DXT5nm-style) keeps the X component in the alpha channel, so a raw RGBA export looks washed-out and ~50% transparent in viewers. Extraction now detects such textures and rewrites them to standard opaque RGB (X from alpha, Y from green, Z reconstructed as `sqrt(1 - x^2 - y^2)`)
- **Detection is strict enough to leave real game data alone**: the check requires the constant-white red signature, varying green/alpha channels and valid unit normals. The game's genuine RGBA data (the `cuts` cutaway overlay baked at ~32% opacity, atlas masks, the 1D heat ramp in `gas`) is exported unchanged (verified on Colt 1911)
- **`convert-normals` works on the game's real file names**: the old default pattern `*_n*.png` matched none of the game's textures (`gas.png`, `cuts.png`, ...). The command now scans `*.png` by content, so re-runs are idempotent. The command also fixes Y being read from blue instead of green and reconstructs Z instead of hardcoding 255

### Logging

- **The log file now always captures INFO and above**: previously the CLI's console verbosity (`WARNING` by default) also silenced the file, so most runs left an almost empty log. Console status lines are mirrored into the file as `[status] message` INFO records, so the log is a complete history of a run
- **DEBUG in the file via `.env`**: `WOG_LOG_LEVEL=DEBUG` (default `INFO`). Console verbosity stays on the `-v`/`-d` flags, and `-d` now shows DEBUG on the console
- **One rotating log file** `wog.log` (5 MB x 3 backups) instead of a new `wog_dump_<timestamp>.log` on every run. Full tracebacks of unexpected errors always land in the file
- **Logs moved out of the working directory**: they now live next to the per-user database (`~/.local/share/wog_dump/logs/`), overridable with `WOG_LOG_DIR`. `wog-dump info` shows the path

### Interface and feedback

- **Progress bars with ETA for every long operation**: fetching keys (`query=3` per weapon) previously ran for minutes with no output at all. Key fetching, update checks and bulk decryption now show a live progress bar (count, percentage, remaining time), with failed/missing counters in the bar itself
- **A missing asset no longer aborts the batch**: an HTTP 404 is now a per-asset skip with one clear warning ("Skipping asset not found on server") instead of an ERROR stopping all remaining batches. One bad name used to silently cut the run from 288 assets down to 49
- **No repeated banners in `full-pipeline`**: sub-commands invoked by the pipeline no longer re-print the big banner (the banner appeared 4+ times)
- **No misleading "No decryption keys found"** during `--update-keys` on a fresh database (the warning came from the pre-merge load of an empty store)
- **Lower key-API throttle default**: `WOG_API_REQUEST_DELAY` 0.5s to 0.25s. 288 keys now take ~72s instead of ~144s while staying at/below the request rate the unthrottled 4-thread fetch used to produce

### Reliability

- **Resumable downloads**: interrupted transfers keep their partial data (`runtime/assets/*.tmp`) and the next run continues via an HTTP `Range` request instead of starting over. If the server file changed since, the download restarts automatically
- **Parallel asset downloads**: `download-assets` now downloads with a thread pool (`--max-threads`) and a live progress bar instead of a sequential loop
- **Rate limiting**: all requests go through a thread-safe `RateLimiter`. `WOG_REQUEST_DELAY` (downloads, default 0.1s) and `WOG_API_REQUEST_DELAY` (key API, default 0.5s) enforce a minimum interval to avoid overloading the game servers
- **Strict download validation**: a downloaded asset must match the server-declared `Content-Length` exactly. The old "accept anything >= 20 bytes" heuristic now only applies when the size is unknown

### Refactoring

- **CLI split into modules**: the 935-line `cli/main.py` became one module per command group (`cli/download.py`, `cli/decrypt.py`, `cli/unpack.py`, `cli/pipeline.py`, `cli/auth.py`, `cli/cache.py`, `cli/info.py`, `cli/convert.py`, `cli/errors.py`). `cli/main.py` keeps the group, global options and `main()`
- **`core/keys.py`**: `KeyManager` and the decrypt exception hierarchy moved out of `core/decrypt.py`, which now contains only the XOR asset decryption (`decrypt.py` re-exports the old names, imports keep working)
- **Extractor registry in `core/unpack.py`**: one `AssetExtractor` subclass per Unity object type (`EXTRACTORS`) replaces the method-name dispatch table. Supporting a new Unity type is now a self-contained subclass
- Removed unused `WOGLogger.create_download_progress`/`print_error_summary`

### Testing

- **Unit tests for previously untested modules**: `core/unpack.py` (0% to 92% coverage: all six extractors, weapon-list parsing, filtering, stats), `core/api.py`, `core/http.py`, `utils/logging.py`
- **Download resume/parallel tests**: Range-request resume, server ignoring ranges, size-mismatch handling, thread-safe rate limiter
- **Coverage floor**: `fail_under = 75` in `[tool.coverage.report]` keeps regressions visible

### Documentation

- **`docs/configuration.md`**: every environment variable, auth options, endpoints, rate limiting and resume behavior
- **`docs/architecture.md`**: module map, pipeline stages, conventions
- **`docs/database.md`**: SQLite schema, storage locations, migration from pre-2.4 files
- **README**: realistic Troubleshooting section (auth failures, `result=1000`, rate limits, stale sessions, replacing advice referencing non-existent CLI options), updated project structure, links to the new docs

## [2.4.0] - 2026-11-01

### Protocol update (verified against the live client capture, Nov 2026)

- **The key API moved to `query2026.php`**: the old `query2018.php` endpoint answers every request with `result=1000`. Default `api_base_url`, `game_version` (`2.2.3u9`) and `unity_version` (`6000.0.84f1`, Unity 6) now match the current `disasm64.exe` client
- **`result=1000` reinterpreted**: the code means "session rejected", not a generic server error, and key fetching now re-logins automatically on the code
- Verified live: the `hash=` request parameter the client sends is not enforced by the server. The `dev` device id is not validated (generated per installation). Email login (`query=1`) still exists on the new endpoint

### Breaking Changes

- **No secrets in the repository**: the 275 bundled decryption keys (`runtime/keys.txt`), the bundled weapon list (`runtime/weapons.txt`) and the captured auth constants (`auth_id`/`auth_session`/`device_id`) were removed from the repo. Existing installations import their old files with `wog-dump cache --migrate`
- **Local SQLite database** replaces `runtime/data.json`: weapons, keys, account credentials and sessions now live in a per-user database (`~/.local/share/wog_dump/wog_dump.db`, `%APPDATA%\wog_dump\` on Windows, owner-only permissions, override with `WOG_DB_PATH`)
- `DataStorageManager` (JSON storage) was replaced by `WOGDatabase`

### Added

- **`wog-dump login` / `wog-dump logout`**: log in with your own World of Guns game account (email + password, same as the game client). A fresh session is obtained automatically whenever needed. Verified live: the login endpoint accepts the request format, invalid accounts return `result=100` with a readable message
- **Generated device identity**: a random 40-hex device id is generated per installation and stored in the local database (no captured device id in the code)
- `core/api.py` (shared BZ2-over-HTTP API client), `core/auth.py` (login/session management), `core/database.py` (SQLite storage)
- Key fetching now fails fast with one clear message when no account is configured, and re-logins automatically once when a stored session is rejected
- **MITM key capture workflow** ([#1](https://github.com/InzGIBA/ReWOG/issues/1)): the key API rejects requests not coming from the real game client, so fresh keys now have to be captured from your own game traffic
  - `scripts/extract_keys.py` extracts `model`/`sync` pairs from mitmproxy `.flows` dumps, Fiddler `.saz` archives, or loose request/response dumps, and merges them into the local key store (`--merge`) or exports a shareable bundle (`-o`)
  - `docs/mitm-capture.md`: step-by-step instructions for capturing keys with mitmproxy or Fiddler
  - new `[mitm]` extra installs `mitmproxy` for reading `.flows` captures
- `wog_dump.core.key_import` module with the capture parsers (unit-tested, including mitmproxy/SAZ/flat-file formats)
- **`extract_keys.py --identity`**: imports the captured client identity (id/session/dev) from a mitmproxy dump into the local database and prints ready-to-copy `.env` lines
- **Capture parsing handles IP-connected traffic**: the game client connects to the API by raw IP address, so captures are now matched by query-endpoint path/payload instead of hostname
- **`.env` support** (python-dotenv): `WOG_AUTH_ID`/`WOG_AUTH_SESSION`/`WOG_DEVICE_ID` (captured session) or `WOG_ACCOUNT_EMAIL`/`WOG_ACCOUNT_PASSWORD` (email login), plus `WOG_API_BASE_URL`/`WOG_DATA_BASE_URL` overrides. `.env.example` documents everything and `.env` is gitignored
- **Live tests** (`pytest -m live`): fetch a real key, download an asset, decrypt the file and load the bundle in UnityPy. The tests skip automatically without credentials in `.env`. Both live tests pass against the real servers

### Fixed

- **`--update-keys` no longer drops existing keys**: a partial key fetch used to overwrite the whole key store, losing the bundled keys. Fetched keys are now merged into the existing set (`download-assets` and `decrypt-assets`)

### Refactoring

- **Fixed broken CLI paths**: `cache --show-cache` no longer crashes with `KeyError`. `convert-normals` on a directory no longer crashes with `TypeError` (the command passed a `batch_size` the converter never accepted). `decrypt-assets --validate` no longer crashes on a missing `UnityPy` import
- **Completed the 2.3.2 hash removal**: deleted the leftover `--refresh-hashes`/`--clear-hashes` options (the options referenced methods which no longer exist). `--validate-hashes` renamed to `--validate-assets` to match the real behavior
- **Single version source**: the version now lives only in `wog_dump/__init__.py`. `pyproject.toml` (hatchling dynamic version), the CLI `--version` flag and the data-store metadata all read the value from there
- **Deduplication**: one shared download-to-temp-then-rename implementation for all downloads, one shared HTTP session factory (`core/http.py`), one XOR implementation instead of three
- **XOR decryption is ~18x faster** thanks to big-integer XOR while staying bit-for-bit compatible with the previous output
- **Removed test-only hacks from production code**: the special-cased `"test.unity3d"` filename, mock-object chunk handling, and the accept-any-file validation branch
- **Fixed a latent crash**: `except bz2.BZ2Error` referenced a non-existent exception and would have raised `AttributeError` on the first decompression error
- **Type safety**: `mypy --strict` passes with zero errors (was 87). Config path fields are no longer `Optional`. Fixed an operator-precedence bug in weapon-name parsing
- **Removed dead code**: unused exceptions, the no-op `--parallel` flag, duplicate legacy-file parsing, the disabled hash test file
- **README**: removed documentation of non-existent CLI options (`--resume`, `--dry-run`, `--validate-only`, `--export-config`)

---

## [2.3.2] - 2025-08-29

### Breaking Changes

- **Removed Unity3D hash validation**: the `.hash` files used hash algorithms which do not match the file content, so all hash validation functionality was removed. Asset validation now relies on file size comparison for update detection, and the hash-related CLI options (`--refresh-hashes`, `--validate-hashes`, `--clear-hashes`) are gone.

### Added: JSON storage and download management

- **`DataStorageManager`** replaces `weapons.txt`/`keys.txt` with a single JSON store: Pydantic validation, rich metadata (timestamps, versions, source), automatic backups, and background migration from the legacy files with fallback and verification
- **Cache metadata**: `CacheMetadata` model with per-asset tracking, configurable expiration and configuration snapshots
- **Download methods**: `download_weapon_list()` (spider_gen.unity3d), `check_for_updates()` (parallel via ThreadPoolExecutor), `download_assets()` (success/failure tracking), `download_assets_batched()` (configurable batches with progress)
- **Update detection** based on server-declared file size, with metadata stored on successful downloads
- **Tests** for the storage system (migration scenarios, real file operations, corruption recovery) and the new download methods (parallel, batched, error handling)

### Changed

- Atomic JSON operations with backup and rollback, better temporary-file cleanup, and an enhanced `cache` command with metadata operations and status reporting
- Parallel update checking and batched downloads with ThreadPoolExecutor and configurable worker pools

### Fixed

- Seven failing tests resolved (Mock object handling in download operations), proper temporary-file cleanup on errors, and better handling of network timeouts and server response edge cases

### Migration

Legacy `weapons.txt` and `keys.txt` are migrated automatically on first use. The originals are preserved as backups. `DataStorageManager` keeps backward-compatible methods, so existing APIs remain functional.

---

## [2.3.1] - 2025-08-29

Quality and reliability release. Full backward compatibility with v2.3.0.

### Changed

- **Configuration**: environment variable support for all major settings, singleton pattern, permission-error resilience on directory creation, stricter validation ranges (max_threads 1-16, chunk_size validation)
- **Error handling**: custom exception hierarchies, graceful network timeout/retry handling, better validation messages
- **Downloads**: connection pooling, flexible header detection, exponential backoff retries
- **Decryption**: more efficient XOR chunk processing, key caching for fewer API calls
- **Logging**: operation timing, better progress bars, structured error summaries
- **Normal map converter**: content-based format analysis instead of filename-only detection

### Fixed

- `m_Script` data type handling for bytes and string formats, surrogate character encoding in asset extraction, asset size detection for update checking, HEAD request timeout management

### Removed

- numpy and psutil dependencies, unused configuration options and dead code paths

---

## [2.3.0] - 2025-08-29

Documentation and community-standards release.

### Added

- **SUPPORT.md**: step-by-step help-seeking process, FAQ, diagnostic commands, response expectations
- **README.md**: complete restructure (table of contents, installation methods, pro tips, expanded contributing section)
- **CONTRIBUTING.md**: advanced development sections (performance, security, testing strategies, release process)

### Changed

- Consistent formatting across all documentation files, cross-references between documents, copy-paste-ready command examples

---

## [2.2.0] - 2024-08-28

Pure Python release: the XOR C binary dependency is removed.

### Changed

- **XOR decryption in pure Python**: 8KB chunked processing replaces the C binary and the subprocess overhead. No C compiler required. Works identically on all platforms

### Removed

- `xor.c`, the whole `bin/` folder with compiled binaries, platform-specific binary path logic, the `xor_binary_path` configuration option and the XOR status in `info`

### Migration

No user action required. For developers: `decrypt_with_xor_binary()` and the `xor_binary_path` property no longer exist, and the XOR binary tests were removed.

---

## [2.1.0] - 2024-08-28

Modernization release: uv for package management and Python 3.12+ type annotations.

### Added

- **uv support** as the recommended package manager. Hatchling replaces setuptools as the build backend. Unused dependencies removed (pathlib-mate, typing-extensions)

### Changed

- **Modern type annotations**: all `typing` module imports removed in favor of native syntax

  Before (v2.0):
  ```python
  def process_data(items: List[Dict[str, Any]]) -> Optional[Dict[str, Union[str, int]]]:
      pass
  ```

  After (v2.1):
  ```python
  def process_data(items: list[dict[str, object]]) -> dict[str, str | int] | None:
      pass
  ```

### Removed

- Legacy compatibility scripts (`wog_dump.py`, `convert_normal_map.py`), unused imports and deprecated patterns

### Migration (developers)

Old setup: `pip install -e ".[dev]"`. New setup: `uv pip install -e ".[dev]"`.

---

## [2.0.0] - 2024-08-28

Major release: complete rewrite for Python 3.12+.

### Added

- **Modern `src/` layout** with focused modules: `core.config`, `core.download`, `core.decrypt`, `core.unpack`, `utils.logging`, `utils.normal_map`, `cli.main`
- **Click CLI with subcommands**: `download-weapons`, `download-assets`, `decrypt-assets`, `unpack-assets`, `convert-normals`, `full-pipeline`, `info`
- **Rich console output**: colors, progress bars, verbose/debug modes
- **Pydantic configuration** with validation and automatic directory creation
- **Parallel processing**, streaming downloads and error recovery
- **Test suite**: unit and integration tests with coverage reporting
- **Modern packaging**: PEP 621 `pyproject.toml`, dev dependency groups, strict mypy, black/isort/flake8 integration

### Changed

- **Python 3.12+ required** (was 3.10+): uses `|` union syntax and other modern features
- **Installation** via `pip install -e .` with a `wog-dump` console script instead of running scripts directly

### Migration from v1.x

Old usage:
```bash
python wog_dump.py
python convert_normal_map.py <path>
```

New usage:
```bash
wog-dump full-pipeline
wog-convert-normals <path>
```

Breaking changes: Python 3.12+ is required, the package must be installed (no direct script execution), and the command structure is new.

### Dependencies

Core: `requests`, `UnityPy`, `click`, `pydantic`, `Pillow`, `rich`. Development: `pytest`, `pytest-cov`, `black`, `isort`, `mypy`, `flake8`.

---

## [1.x.x] - Previous Versions

See git history for changes in previous versions. The v1.x series used a simple script-based approach with basic functionality.
