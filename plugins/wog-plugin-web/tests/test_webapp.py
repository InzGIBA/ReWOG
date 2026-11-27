"""Unit tests for the local web UI (task 16)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")

from wog_plugin_web.app import create_app  # noqa: E402

from wog_dump.core.config import set_config  # noqa: E402


def make_runtime(base: Path) -> None:
    encrypted = base / "runtime" / "encrypted"
    scenes = base / "runtime" / "scenes" / "ak47"
    encrypted.mkdir(parents=True)
    (encrypted / "ak47.bytes").write_bytes(b"x" * 1000)
    scenes.mkdir(parents=True)
    (scenes / "ak47.glb").write_bytes(b"glb-bytes")
    (scenes / "scene.json").write_text(
        json.dumps({"weapon": "ak47", "meshes": 3, "parts": ["bolt"]}),
        encoding="utf-8",
    )


class FakeProcess:
    """Scripted stdout, controllable exit; stands in for Popen."""

    def __init__(
        self,
        lines: list[str] | None = None,
        release: threading.Event | None = None,
        returncode: int = 0,
    ) -> None:
        self.pid = 4242
        self.stdout = self
        self._lines = list(lines or [])
        self._release = release if release is not None else threading.Event()
        if release is None:
            self._release.set()
        self._returncode = returncode
        self.returncode: int | None = None
        self.terminated = False

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        self._release.wait(timeout=10)
        return ""

    def poll(self) -> int | None:
        return self.returncode

    def wait(self) -> int:
        self._release.wait(timeout=10)
        self.returncode = -15 if self.terminated else self._returncode
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self._release.set()


def wait_job(
    tc: TestClient,
    token: str,
    job_id: int,
    statuses: tuple[str, ...] = ("done", "failed", "cancelled"),
    timeout: float = 10.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = tc.get(f"/t/{token}/api/jobs/{job_id}").json()
        if payload.get("status") in statuses:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck: {payload}")


@pytest.fixture
def client(tmp_path: Path) -> Any:
    make_runtime(tmp_path)
    set_config(base_dir=tmp_path, db_path=tmp_path / "wog_dump.db")
    app = create_app()
    yield TestClient(app), app.state.token
    app.state.jobs.stop()


class TestTokenGuard:
    def test_wrong_token_404(self, client: Any) -> None:
        tc, token = client
        assert tc.get("/t/wrongtoken/api/catalog").status_code == 404
        assert tc.get("/api/catalog").status_code == 404

    def test_right_token_serves(self, client: Any) -> None:
        tc, token = client
        assert tc.get(f"/t/{token}/api/catalog").status_code == 200


class TestCatalogEndpoint:
    def test_payload_schema(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/api/catalog")
        payload = response.json()
        assert payload["schema"] == "wog-dump.catalog/3"
        assert payload["weapons"][0]["name"] == "ak47"
        assert payload["weapons"][0]["downloaded"] is True
        assert payload["weapons"][0]["scene"] is True


class TestWeaponEndpoint:
    def test_manifest_included(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/api/weapons/ak47")
        payload = response.json()
        assert payload["scene"] is True
        assert payload["manifest"]["meshes"] == 3
        assert payload["has_viewer"] is False

    def test_unknown_weapon_404(self, client: Any) -> None:
        tc, token = client
        assert tc.get(f"/t/{token}/api/weapons/nope").status_code == 404


class TestRunWhitelist:
    def test_unknown_stage_400(self, client: Any) -> None:
        tc, token = client
        response = tc.post(f"/t/{token}/api/run", json={"stage": "rm -rf /"})
        assert response.status_code == 400

    def test_unsafe_weapon_400(self, client: Any) -> None:
        tc, token = client
        response = tc.post(
            f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "../nope"}
        )
        assert response.status_code == 400

    def test_unknown_state_400(self, client: Any) -> None:
        tc, token = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapon": "ak47", "state": "bogus"},
        )
        assert response.status_code == 400

    def test_valid_stage_launches_popen(self, client: Any, monkeypatch: Any) -> None:
        tc, token = client
        captured: dict[str, list[str]] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
            captured["args"] = args
            return FakeProcess()

        from wog_plugin_web import jobs as jobs_module

        # The CLI is on PATH, so the job spawns it by name as recorded
        monkeypatch.setattr(
            jobs_module.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(jobs_module.subprocess, "Popen", fake_popen)
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapon": "ak47", "state": "exploded"},
        )
        assert response.status_code == 200
        job = wait_job(tc, token, response.json()["job_id"])
        assert job["status"] == "done"
        assert captured["args"] == [
            "wog-dump",
            "--no-progress",
            "build-scene",
            "ak47",
            "--state",
            "exploded",
        ]

    def test_second_stage_queued_not_409(self, client: Any, monkeypatch: Any) -> None:
        """Concurrency no longer conflicts: the second request queues."""
        tc, token = client
        release = threading.Event()

        from wog_plugin_web import jobs as jobs_module

        monkeypatch.setattr(
            jobs_module.subprocess,
            "Popen",
            lambda args, **kw: FakeProcess(release=release),
        )
        first = tc.post(
            f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "ak47"}
        )
        assert first.status_code == 200
        second = tc.post(
            f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "ak47"}
        )
        assert second.status_code == 200
        assert second.json()["job_id"] == first.json()["job_id"] + 1
        assert second.json()["position"] == 2
        release.set()
        wait_job(tc, token, first.json()["job_id"])
        wait_job(tc, token, second.json()["job_id"])


class TestPagesAndData:
    def test_single_shell_everywhere(self, client: Any) -> None:
        """ "/" and "/weapon/<name>" serve the same SPA shell.

        The shell carries the token in absolute asset URLs (it must
        work at both route depths) and no per-page scripts exist
        anymore.
        """
        tc, token = client
        index_html = tc.get(f"/t/{token}/").text
        weapon_html = tc.get(f"/t/{token}/weapon/ak47").text
        assert index_html == weapon_html
        assert f"/t/{token}/static/app.js" in index_html
        assert f"/t/{token}/static/vendor/alpine.min.js" in index_html
        assert "__TOKEN__" not in index_html
        assert "__I18N__" not in index_html

    def test_unknown_weapon_page_404(self, client: Any) -> None:
        tc, token = client
        assert tc.get(f"/t/{token}/weapon/not-a-weapon!!").status_code == 404

    def test_page_scripts_resolve(self, client: Any) -> None:
        """Every literal src attribute on the shell must return 200.

        Relative srcs resolve against the page URL exactly like a
        browser does; Alpine :src bindings are templates, not URLs, so
        the lookahead skips them.
        """
        import re
        from urllib.parse import urljoin

        tc, token = client
        for page_path in (f"/t/{token}/", f"/t/{token}/weapon/ak47"):
            html = tc.get(page_path).text
            for src in re.findall(r'(?<![:@])src="([^"]+)"', html):
                if src.startswith(("http", "//", "data:")):
                    continue
                url = urljoin(page_path, src)
                status = tc.get(url).status_code
                assert status == 200, f"{src} -> {status} on {page_path}"

    def test_index_page(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/")
        assert response.status_code == 200
        assert "wog dump" in response.text

    def test_theme_assets_resolve(self, client: Any) -> None:
        """The smui theme stylesheet and its vendored font are served."""
        tc, token = client
        assert tc.get(f"/t/{token}/static/smui.css").status_code == 200
        assert (
            tc.get(f"/t/{token}/static/fonts/source-code-pro.woff2").status_code == 200
        )

    def test_pages_link_theme(self, client: Any) -> None:
        tc, token = client
        for page_path in (f"/t/{token}/", f"/t/{token}/weapon/ak47"):
            assert f'href="/t/{token}/static/smui.css"' in tc.get(page_path).text

    def test_view_page_loads_theme_font(self, client: Any) -> None:
        tc, token = client
        html = tc.get(f"/t/{token}/view/ak47").text
        assert "@font-face" in html
        assert f"/t/{token}/static/fonts/source-code-pro.woff2" in html

    def test_scene_file_served(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/data/scenes/ak47/ak47.glb")
        assert response.status_code == 200
        assert b"glb-bytes" in response.content

    def test_scene_traversal_blocked(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/data/scenes/../wog_dump.db")
        assert response.status_code in (400, 404)

    def test_static_vendor_mounted(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/vendor/three.module.min.js")
        assert response.status_code == 200


class TestEvents:
    def test_events_stream_on_mock_process(self, client: Any, monkeypatch: Any) -> None:
        tc, token = client

        from wog_plugin_web import jobs as jobs_module

        monkeypatch.setattr(
            jobs_module.subprocess,
            "Popen",
            lambda args, **kw: FakeProcess(lines=["hello from stage\n"]),
        )
        tc.post(f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "ak47"})
        response = tc.get(f"/t/{token}/api/events")
        assert response.status_code == 200
        assert "hello from stage" in response.text
