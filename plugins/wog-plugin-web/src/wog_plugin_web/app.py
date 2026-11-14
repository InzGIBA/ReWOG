"""Local web UI over the wog-dump core.

One FastAPI app, all routes under a random per-start token path
(``/t/<token>/...``), bound to 127.0.0.1 by default. Stages are queued
in a persistent SQLite job queue (see ``jobs.py``) and run sequentially
as subprocesses of the real ``wog-dump`` CLI (same behavior as the
terminal); output is stored per job and streamed to the browser over
SSE with replay on reconnect. Ships as the ``wog-plugin-web`` plugin.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

import wog_dump
from wog_dump.core.catalog import build_catalog
from wog_dump.core.config import WOGConfig, get_config
from wog_dump.core.paths import contained_path, is_safe_weapon_name
from wog_dump.core.scene_states import available_states
from wog_dump.core.spider import CARD_SUFFIX, ICON_SUFFIX, previews_dir

from .i18n import STRINGS
from .jobs import JobQueue

__all__ = ["create_app", "RUNNABLE_STAGES"]

# Stages the UI launches, with their argument shape
RUNNABLE_STAGES = (
    "download-weapons",
    "download-assets",
    "decrypt-assets",
    "unpack-assets",
    "build-scene",
    "render",
    "print-export",
)

# Extra build-scene export formats (glb is always written)
_SCENE_FORMATS = ("stl", "usd", "usdz", "fbx")

# GLB node names for print-export --part: printable, single line
_PART_NAME_RE = re.compile(r"[^\x00-\x1f\x7f]{1,200}")

# Preview filenames served to the browser ("<weapon>.png" / "<weapon>_card.png")
_PREVIEW_FILE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_-]{1,49}("
    + re.escape(CARD_SUFFIX)
    + r"|"
    + re.escape(ICON_SUFFIX)
    + r")"
)

# State select order: built-ins first, plugin-provided states after
_STATE_ORDER = ("assembled", "field-strip", "exploded", "parts")


def _state_choices() -> list[str]:
    states = available_states()
    ordered = [s for s in _STATE_ORDER if s in states]
    return ordered + sorted(states - set(ordered))


def _flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
    return value


def _weapons(
    stage: str,
    payload: dict[str, Any],
    *,
    required: bool,
    known: set[str] | None,
) -> list[str]:
    """Validated weapon selection: 'weapons' list or legacy 'weapon'."""
    raw = payload.get("weapons")
    if raw is None and payload.get("weapon") is not None:
        raw = [payload["weapon"]]
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(w, str) for w in raw):
        raise HTTPException(status_code=400, detail="weapons must be a list of names")
    names = list(dict.fromkeys(raw))
    if required and not names:
        raise HTTPException(status_code=400, detail=f"{stage} needs a weapon")
    for name in names:
        if not is_safe_weapon_name(name):
            raise HTTPException(status_code=400, detail="Invalid weapon name")
        if known is not None and name not in known:
            raise HTTPException(status_code=400, detail="Unknown weapon")
    return names


def _build_args(stage: str, payload: dict[str, Any], config: WOGConfig) -> list[str]:
    """Server-side argv from typed, validated options (list form, no shell)."""
    # --no-progress is a group option: Click expects it before the subcommand
    args = ["wog-dump", "--no-progress", stage]

    if stage == "download-weapons":
        if _flag(payload, "force"):
            args.append("--force")
        if _flag(payload, "validate"):
            args.append("--validate")
        return args

    if stage in ("download-assets", "decrypt-assets", "unpack-assets"):
        known = {entry.name for entry in build_catalog(config)}
        names = _weapons(stage, payload, required=False, known=known)
        if stage == "unpack-assets":
            # unpack-assets has no --weapons; its per-asset filter is a
            # name pattern globbed as "<pattern>.unity3d", so one weapon
            # name is an exact match for the single decrypted file
            if len(names) > 1:
                raise HTTPException(
                    status_code=400, detail="unpack-assets accepts a single weapon"
                )
            if names:
                args.extend(["--asset-filter", names[0]])
        elif names:
            args.extend(["--weapons", ",".join(names)])
        if stage == "download-assets" and _flag(payload, "update_keys"):
            args.append("--update-keys")
        return args

    if stage == "build-scene":
        args.extend(_weapons(stage, payload, required=True, known=None))
        state = payload.get("state")
        if state:
            if state not in available_states():
                raise HTTPException(status_code=400, detail=f"Unknown state: {state!r}")
            args.extend(["--state", str(state)])
        if _flag(payload, "viewer"):
            args.append("--viewer")
        if _flag(payload, "animations"):
            args.append("--animations")
        formats = payload.get("formats") or []
        if not isinstance(formats, list) or any(
            f not in _SCENE_FORMATS for f in formats
        ):
            raise HTTPException(
                status_code=400,
                detail=f"formats must be a list of {', '.join(_SCENE_FORMATS)}",
            )
        if formats:
            args.extend(["--format", ",".join(dict.fromkeys(formats))])
        return args

    if stage == "render":
        names = _weapons(stage, payload, required=False, known=None)
        render_all = _flag(payload, "all")
        if not names and not render_all:
            raise HTTPException(
                status_code=400, detail="render needs weapons or all=true"
            )
        args.extend(names)
        if render_all:
            args.append("--all")
        if _flag(payload, "video"):
            args.append("--video")
        return args

    # print-export
    args.extend(_weapons(stage, payload, required=True, known=None))
    parts = payload.get("parts") or []
    if not isinstance(parts, list):
        raise HTTPException(status_code=400, detail="parts must be a list")
    for part in parts:
        if (
            not isinstance(part, str)
            or not part.strip()
            or not _PART_NAME_RE.fullmatch(part)
        ):
            raise HTTPException(status_code=400, detail="Invalid part name")
        args.extend(["--part", part])
    return args


def create_app(config: WOGConfig | None = None) -> FastAPI:
    config = config or get_config()
    token = secrets.token_urlsafe(16)
    queue = JobQueue(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        queue.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    static_dir = Path(__file__).parent / "static"

    def guard(token_in_path: str) -> None:
        # Wrong token answers 404 like any other unknown path, so the
        # surface does not even advertise an API to scan
        if token_in_path != token:
            raise HTTPException(status_code=404)

    def shell_html(token_in_path: str) -> HTMLResponse:
        """The single-page shell, at "/" and "/weapon/<name>" alike.

        The document carries no relative asset paths: every URL is
        absolute with the token baked in, so the shell works unchanged
        at both route depths. The i18n table is injected as JSON so the
        first paint is already localized.
        """
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        strings_json = json.dumps(STRINGS, ensure_ascii=False).replace("<", "\\u003c")
        states_json = json.dumps(_state_choices()).replace("<", "\\u003c")
        html = html.replace("__TOKEN__", token_in_path)
        html = html.replace("__I18N__", strings_json)
        return HTMLResponse(html.replace("__STATES__", states_json))

    def ru_meta() -> dict[str, dict[str, Any]]:
        try:
            from wog_dump.core.database import WOGDatabase

            return WOGDatabase(config).get_weapon_meta()
        except Exception:
            return {}

    def catalog_payload() -> dict[str, Any]:
        from wog_dump.core.catalog import CATALOG_SCHEMA

        entries = build_catalog(config)
        meta = ru_meta()
        return {
            "schema": CATALOG_SCHEMA,
            "count": len(entries),
            "weapons": [
                {
                    "name": e.name,
                    "has_key": e.has_key,
                    "downloaded": e.downloaded,
                    "decrypted": e.decrypted,
                    "unpacked": e.unpacked,
                    "scene": e.scene,
                    "previews": e.previews,
                    "size_bytes": e.size_bytes,
                    "display_name": e.display_name,
                    "display_name_ru": (meta.get(e.name) or {}).get("full_name_ru"),
                    "categories": list(e.categories),
                    "num_parts": e.num_parts,
                    "since": e.since,
                    "icon": e.icon,
                    "test": e.test,
                }
                for e in entries
            ],
        }

    # -- API -------------------------------------------------------------

    @app.get("/t/{token_in_path}/api/catalog")
    def api_catalog(token_in_path: str) -> JSONResponse:
        guard(token_in_path)
        return JSONResponse(catalog_payload())

    @app.get("/t/{token_in_path}/api/i18n")
    def api_i18n(token_in_path: str) -> JSONResponse:
        guard(token_in_path)
        return JSONResponse(STRINGS)

    @app.get("/t/{token_in_path}/previews/{filename}")
    def preview_file(token_in_path: str, filename: str) -> FileResponse:
        # Extracted spider card images: "<weapon>.png" icons and
        # "<weapon>_card.png" 1024px renders
        guard(token_in_path)
        if not _PREVIEW_FILE_RE.fullmatch(filename):
            raise HTTPException(status_code=404)
        try:
            path = contained_path(previews_dir(config), filename)
        except ValueError:
            raise HTTPException(status_code=404) from None
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/t/{token_in_path}/api/weapons/{name}")
    def api_weapon(token_in_path: str, name: str) -> JSONResponse:
        guard(token_in_path)
        known = {entry.name: entry for entry in build_catalog(config)}
        entry = known.get(name)
        if entry is None:
            raise HTTPException(status_code=404)
        scene_dir = config.base_dir / "runtime" / "scenes" / name
        manifest = scene_dir / "scene.json"
        payload: dict[str, Any] = {
            "name": entry.name,
            "scene": entry.scene,
            "has_key": entry.has_key,
            "downloaded": entry.downloaded,
            "decrypted": entry.decrypted,
            "unpacked": entry.unpacked,
            "previews": entry.previews,
            "has_viewer": (scene_dir / "viewer.html").exists(),
            "icon": entry.icon,
            "display_name": entry.display_name,
            "display_name_ru": None,
            "categories": list(entry.categories),
            "num_parts": entry.num_parts,
            "since": entry.since,
            "size_bytes": entry.size_bytes,
            "test": entry.test,
            "manifest": (
                json.loads(manifest.read_text(encoding="utf-8"))
                if manifest.exists()
                else None
            ),
        }
        try:
            from wog_dump.core.database import WOGDatabase

            record = WOGDatabase(config).get_weapon_meta().get(name)
            if record:
                payload["brief_info"] = record.get("brief_info")
                payload["features"] = record.get("features")
                payload["display_name_ru"] = record.get("full_name_ru")
                payload["brief_info_ru"] = record.get("brief_info_ru")
                payload["features_ru"] = record.get("features_ru")
        except Exception:
            pass
        return JSONResponse(payload)

    @app.post("/t/{token_in_path}/api/run")
    def api_run(token_in_path: str, payload: dict) -> JSONResponse:
        guard(token_in_path)
        stage = str(payload.get("stage", ""))
        if stage not in RUNNABLE_STAGES:
            raise HTTPException(status_code=400, detail=f"Unknown stage: {stage!r}")
        args = _build_args(stage, payload, config)
        options = {k: v for k, v in payload.items() if k != "stage"}
        job_id, position = queue.enqueue(stage, options, args)
        return JSONResponse({"job_id": job_id, "position": position, "args": args})

    @app.get("/t/{token_in_path}/api/jobs")
    def api_jobs(token_in_path: str) -> JSONResponse:
        guard(token_in_path)
        return JSONResponse({"jobs": [job.to_dict() for job in queue.list_jobs()]})

    @app.get("/t/{token_in_path}/api/jobs/{job_id}")
    def api_job(token_in_path: str, job_id: int) -> JSONResponse:
        guard(token_in_path)
        job = queue.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404)
        return JSONResponse(job.to_dict(include_log=True))

    @app.post("/t/{token_in_path}/api/jobs/{job_id}/cancel")
    def api_cancel(token_in_path: str, job_id: int) -> JSONResponse:
        guard(token_in_path)
        try:
            status = queue.cancel(job_id)
        except KeyError:
            raise HTTPException(status_code=404) from None
        except ValueError:
            raise HTTPException(
                status_code=409, detail="Job already finished"
            ) from None
        return JSONResponse({"job_id": job_id, "status": status})

    @app.get("/t/{token_in_path}/api/events")
    def api_events(
        token_in_path: str, request: Request, job_id: int | None = None
    ) -> Any:
        """SSE log stream: stored lines first, then live tail until done.

        Reconnects resume through the standard Last-Event-ID header (the
        line number of the last delivered line); a fresh connection
        without it replays the whole log.
        """
        guard(token_in_path)
        if job_id is None:
            job_id = queue.current_job_id()
        elif queue.get_job(job_id) is None:
            raise HTTPException(status_code=404)
        resume_raw = request.headers.get("last-event-id", "")
        resume = int(resume_raw) if resume_raw.isdigit() else 0

        def stream() -> Iterator[str]:
            if job_id is None:
                yield f"data: {STRINGS['en']['noRunningStage']}\n\n"
                yield 'event: done\ndata: {"job_id": null, "status": "idle"}\n\n'
                return
            line_no = 0
            while True:
                job = queue.get_job(job_id)
                if job is None:
                    return
                lines = job.log.splitlines()
                for number, line in enumerate(lines[line_no:], start=line_no + 1):
                    if number <= resume:
                        continue
                    yield f"id: {number}\ndata: {line}\n\n"
                line_no = len(lines)
                if job.status in ("done", "failed", "cancelled"):
                    done = {
                        "job_id": job.id,
                        "status": job.status,
                        "exit_code": job.exit_code,
                    }
                    yield f"event: done\ndata: {json.dumps(done)}\n\n"
                    return
                queue.wait_for_change(timeout=0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # -- Pages and data ---------------------------------------------------

    @app.get("/t/{token_in_path}/")
    def index(token_in_path: str) -> HTMLResponse:
        guard(token_in_path)
        return shell_html(token_in_path)

    @app.get("/t/{token_in_path}/weapon/{name}")
    def weapon_page(token_in_path: str, name: str) -> HTMLResponse:
        # Same shell: the SPA router turns the path into its hash route
        guard(token_in_path)
        if not is_safe_weapon_name(name):
            raise HTTPException(status_code=404)
        return shell_html(token_in_path)

    @app.get("/t/{token_in_path}/view/{name}")
    def view_page(token_in_path: str, name: str) -> Any:
        """Serve the viewer over HTTP (linked mode).

        The embedded viewer.html file uses data URIs in its import map,
        which Firefox blocks; this route serves the same viewer with an
        import map pointing at the token-guarded vendor mount instead.
        """
        guard(token_in_path)
        if not is_safe_weapon_name(name):
            raise HTTPException(status_code=404)
        scene_dir = config.base_dir / "runtime" / "scenes" / name
        glb = scene_dir / f"{name}.glb"
        if not glb.exists():
            raise HTTPException(status_code=404)
        manifest_path = scene_dir / "scene.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {"weapon": name}
        )
        from wog_dump.core.viewer import render_viewer_html

        base = f"/t/{token_in_path}"
        html = render_viewer_html(
            glb.read_bytes(),
            manifest,
            linked=True,
            glb_url=f"{base}/data/scenes/{name}/{name}.glb",
            vendor_base=f"{base}/vendor",
            font_url_base=f"{base}/static",
        )
        return HTMLResponse(html)

    @app.get("/t/{token_in_path}/data/scenes/{path:path}")
    def scene_file(token_in_path: str, path: str) -> FileResponse:
        guard(token_in_path)
        scenes_dir = (config.base_dir / "runtime" / "scenes").resolve()
        target = (scenes_dir / path).resolve()
        # Same guarantee as paths.contained_path, for <weapon>/<file> paths
        if scenes_dir not in target.parents or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target)

    app.mount(
        f"/t/{token}/static",
        StaticFiles(directory=static_dir),
        name="static",
    )
    # The viewer vendor bundle ships with the core package, not the plugin
    vendor_dir = (
        Path(wog_dump.__file__).resolve().parent / "resources" / "viewer" / "vendor"
    )
    app.mount(f"/t/{token}/vendor", StaticFiles(directory=vendor_dir), name="vendor")

    # The full entry URL, printed by the serve command at startup
    app.state.token = token
    app.state.jobs = queue
    app.state.entry_url = f"http://127.0.0.1:8765/t/{token}/"
    return app
