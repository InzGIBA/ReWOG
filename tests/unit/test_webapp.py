"""Unit tests for the local web UI (task 16)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")

from wog_dump.core.config import set_config  # noqa: E402
from wog_dump.core.webapp.app import create_app  # noqa: E402


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


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    make_runtime(tmp_path)
    set_config(base_dir=tmp_path)
    app = create_app()
    return TestClient(app), app.state.token


class TestTokenGuard:
    def test_wrong_token_404(self, client: Any) -> None:
        tc, token = client
        assert tc.get(f"/t/wrongtoken/api/catalog").status_code == 404
        assert tc.get("/api/catalog").status_code == 404

    def test_right_token_serves(self, client: Any) -> None:
        tc, token = client
        assert tc.get(f"/t/{token}/api/catalog").status_code == 200


class TestCatalogEndpoint:
    def test_payload_schema(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/api/catalog")
        payload = response.json()
        assert payload["schema"] == "wog-dump.catalog/1"
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

        class FakeProcess:
            pid = 4242

            def poll(self) -> int | None:
                return None

        captured: dict[str, list[str]] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
            captured["args"] = args
            return FakeProcess()

        from wog_dump.core.webapp import app as app_module

        monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapon": "ak47", "state": "exploded"},
        )
        assert response.status_code == 200
        assert captured["args"] == [
            "wog-dump",
            "build-scene",
            "--no-progress",
            "ak47",
            "--state",
            "exploded",
        ]

    def test_second_stage_409(self, client: Any, monkeypatch: Any) -> None:
        tc, token = client

        class FakeProcess:
            pid = 1

            def poll(self) -> int | None:
                return None

        from wog_dump.core.webapp import app as app_module

        monkeypatch.setattr(
            app_module.subprocess, "Popen", lambda args, **kw: FakeProcess()
        )
        first = tc.post(
            f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "ak47"}
        )
        assert first.status_code == 200
        second = tc.post(
            f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "ak47"}
        )
        assert second.status_code == 409


class TestPagesAndData:
    def test_weapon_page_isolated_from_index_scripts(self, client: Any) -> None:
        """The weapon page loads only its own module.

        app.js is index-only: its render pass touches elements the
        weapon page lacks. The import map must point at the vendored
        runtime.
        """
        tc, token = client
        html = tc.get(f"/t/{token}/weapon/ak47").text
        assert "app.js" not in html
        assert 'src="../static/weapon-view.js"' in html
        assert '"three": "../vendor/three.module.min.js"' in html

    def test_index_loads_catalog_module(self, client: Any) -> None:
        tc, token = client
        html = tc.get(f"/t/{token}/").text
        assert '<script type="module" src="static/app.js">' in html

    def test_page_scripts_resolve(self, client: Any) -> None:
        """Every src attribute on both pages must return 200.

        Relative srcs resolve against the page URL exactly like a
        browser does. Two regressions live here: app.js was referenced
        outside the static mount, and the weapon page broke again one
        directory deeper.
        """
        import re
        from urllib.parse import urljoin

        tc, token = client
        for page_path in (f"/t/{token}/", f"/t/{token}/weapon/ak47"):
            html = tc.get(page_path).text
            for src in re.findall(r'src="([^"]+)"', html):
                if src.startswith(("http", "//", "data:")):
                    continue
                url = urljoin(page_path, src)
                status = tc.get(url).status_code
                assert status == 200, f"{src} -> {status} on {page_path}"

    def test_index_page(self, client: Any) -> None:
        tc, token = client
        response = tc.get(f"/t/{token}/")
        assert response.status_code == 200
        assert "WOG Dump catalog" in response.text

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

        class FakeStdout:
            def __init__(self) -> None:
                self.lines = ["hello from stage\n", ""]

            def readline(self) -> str:
                return self.lines.pop(0) if self.lines else ""

        class FakeProcess:
            pid = 7
            stdout = FakeStdout()

            def poll(self) -> int | None:
                return 0

        from wog_dump.core.webapp import app as app_module

        app_state = tc.app_state if hasattr(tc, "app_state") else None
        # Replace the runner's process directly through the app instance
        runner = None
        for route in tc.app.routes:
            pass
        monkeypatch.setattr(
            app_module.subprocess, "Popen", lambda args, **kw: FakeProcess()
        )
        tc.post(f"/t/{token}/api/run", json={"stage": "build-scene", "weapon": "ak47"})
        response = tc.get(f"/t/{token}/api/events")
        assert response.status_code == 200
        assert "hello from stage" in response.text
