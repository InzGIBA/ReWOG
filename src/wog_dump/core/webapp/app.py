"""Local web UI over the wog-dump core (task 16).

One FastAPI app, all routes under a random per-start token path
(``/t/<token>/...``), bound to 127.0.0.1 by default. Stages run as
subprocesses of the real ``wog-dump`` CLI (same behavior as the
terminal), streamed to the browser over SSE. Install with
``pip install rewog[web]``.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..catalog import build_catalog
from ..config import WOGConfig, get_config
from ..paths import contained_path, is_safe_weapon_name
from ..scene_states import SCENE_STATES
from ..spider import CARD_SUFFIX, ICON_SUFFIX, previews_dir

__all__ = ["create_app"]

# Stages the UI launches, with their argument shape
RUNNABLE_STAGES = ("download-assets", "decrypt-assets", "unpack-assets", "build-scene")

# Preview filenames served to the browser ("<weapon>.png" / "<weapon>_card.png")
_PREVIEW_FILE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_-]{1,49}(" + re.escape(CARD_SUFFIX) + r"|"
    + re.escape(ICON_SUFFIX) + r")"
)


@dataclass
class JobRunner:
    """Runs one CLI stage at a time; extra requests get a 409."""

    config: WOGConfig
    process: Any | None = field(default=None, init=False)

    def start(self, args: list[str]) -> int:
        if self.process is not None and self.process.poll() is None:
            raise HTTPException(
                status_code=409, detail="Another stage is already running"
            )
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(self.config.base_dir),
        )
        return self.process.pid


def create_app(config: WOGConfig | None = None) -> FastAPI:
    config = config or get_config()
    token = secrets.token_urlsafe(16)
    runner = JobRunner(config)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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
        at both route depths.
        """
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__TOKEN__", token_in_path))

    def catalog_payload() -> dict:
        from ..catalog import CATALOG_SCHEMA

        entries = build_catalog(config)
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
        payload = {
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
            "categories": list(entry.categories),
            "num_parts": entry.num_parts,
            "since": entry.since,
            "size_bytes": entry.size_bytes,
            "test": entry.test,
            "manifest": (
                __import__("json").loads(manifest.read_text(encoding="utf-8"))
                if manifest.exists()
                else None
            ),
        }
        try:
            from ..database import WOGDatabase

            record = WOGDatabase(config).get_weapon_meta().get(name)
            if record:
                payload["brief_info"] = record.get("brief_info")
                payload["features"] = record.get("features")
        except Exception:
            pass
        return JSONResponse(payload)

    @app.post("/t/{token_in_path}/api/run")
    def api_run(token_in_path: str, payload: dict) -> JSONResponse:
        guard(token_in_path)
        stage = str(payload.get("stage", ""))
        if stage not in RUNNABLE_STAGES:
            raise HTTPException(status_code=400, detail=f"Unknown stage: {stage!r}")

        # --no-progress is a group option: Click expects it before the
        # subcommand
        args = ["wog-dump", "--no-progress", stage]
        weapon = payload.get("weapon")
        if stage == "build-scene":
            if not weapon:
                raise HTTPException(
                    status_code=400, detail="build-scene needs a weapon"
                )
            if not is_safe_weapon_name(str(weapon)):
                raise HTTPException(status_code=400, detail="Invalid weapon name")
            args.append(str(weapon))
            state = payload.get("state")
            if state:
                if state not in SCENE_STATES:
                    raise HTTPException(
                        status_code=400, detail=f"Unknown state: {state!r}"
                    )
                args.extend(["--state", state])
        else:
            known = {entry.name for entry in build_catalog(config)}
            if weapon:
                if weapon not in known:
                    raise HTTPException(status_code=400, detail="Unknown weapon")
                # unpack-assets has no --weapons; its per-asset filter is a
                # name pattern globbed as "<pattern>.unity3d", so the weapon
                # name is an exact match for the single decrypted file
                if stage == "unpack-assets":
                    args.extend(["--asset-filter", weapon])
                else:
                    args.extend(["--weapons", weapon])

        pid = runner.start(args)
        return JSONResponse({"pid": pid, "args": args})

    @app.get("/t/{token_in_path}/api/events")
    def api_events(token_in_path: str) -> Any:
        guard(token_in_path)
        from fastapi.responses import StreamingResponse

        def stream() -> Any:
            import queue
            import threading

            lines: queue.Queue = queue.Queue()
            process = runner.process

            def pump() -> None:
                if process is None or process.stdout is None:
                    lines.put("no running stage\n")
                    lines.put(None)
                    return
                for line in iter(process.stdout.readline, ""):
                    lines.put(line)
                try:
                    code = process.wait()
                except Exception:
                    code = -1
                lines.put("exit=" + str(code) + "\n")
                lines.put(None)

            threading.Thread(target=pump, daemon=True).start()
            while True:
                line = lines.get()
                if line is None:
                    yield f"event: done\ndata: end\n\n"
                    break
                yield f"data: {line.rstrip()}\n\n"

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
        from ..viewer import render_viewer_html

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
    vendor_dir = Path(__file__).resolve().parents[2] / "resources" / "viewer" / "vendor"
    app.mount(f"/t/{token}/vendor", StaticFiles(directory=vendor_dir), name="vendor")

    # The full entry URL, printed by the serve command at startup
    app.state.token = token
    app.state.entry_url = f"http://127.0.0.1:8765/t/{token}/"
    return app
