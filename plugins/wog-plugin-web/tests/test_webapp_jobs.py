"""Persistent job queue tests: ordering, restart recovery, cancel, SSE."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")

from wog_plugin_web.app import create_app  # noqa: E402

from wog_dump.core.config import set_config  # noqa: E402


@pytest.fixture
def env(tmp_path: Path) -> Path:
    encrypted = tmp_path / "runtime" / "encrypted"
    encrypted.mkdir(parents=True)
    (encrypted / "ak47.bytes").write_bytes(b"x" * 100)
    set_config(base_dir=tmp_path, db_path=tmp_path / "wog_dump.db")
    return tmp_path


@pytest.fixture
def client(env: Path) -> Any:
    app = create_app()
    yield TestClient(app), app.state.token, app
    app.state.jobs.stop()


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


@pytest.fixture
def popen(monkeypatch: Any) -> Any:
    """Patch Popen in the jobs module; records every argv and process."""
    from wog_plugin_web import jobs as jobs_module

    captured: dict[str, Any] = {
        "argv": [],
        "processes": [],
        "factory": lambda args: FakeProcess(),
    }

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        process = captured["factory"](args)
        captured["argv"].append(args)
        captured["processes"].append(process)
        return process

    monkeypatch.setattr(jobs_module.subprocess, "Popen", fake_popen)
    return captured


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


def run(tc: TestClient, token: str, body: dict[str, Any]) -> dict[str, Any]:
    response = tc.post(f"/t/{token}/api/run", json=body)
    assert response.status_code == 200, response.text
    return response.json()


class TestQueue:
    def test_enqueue_returns_id_and_position(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        release = threading.Event()
        popen["factory"] = lambda args: FakeProcess(release=release)
        first = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        second = run(tc, token, {"stage": "build-scene", "weapon": "colt_1911"})
        assert first["job_id"] == 1
        assert first["position"] == 1
        assert second["job_id"] == 2
        assert second["position"] == 2
        release.set()
        wait_job(tc, token, 1)
        wait_job(tc, token, 2)

    def test_jobs_run_sequentially_in_order(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        run(tc, token, {"stage": "build-scene", "weapon": "colt_1911"})
        wait_job(tc, token, 1)
        wait_job(tc, token, 2)
        assert [argv[3] for argv in popen["argv"]] == ["ak47", "colt_1911"]
        jobs = tc.get(f"/t/{token}/api/jobs").json()["jobs"]
        assert [job["id"] for job in jobs] == [2, 1]
        assert all(job["status"] == "done" for job in jobs)

    def test_job_detail_includes_log_and_exit_code(
        self, client: Any, popen: Any
    ) -> None:
        tc, token, app = client
        popen["factory"] = lambda args: FakeProcess(lines=["alpha\n", "beta\n"])
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        job = wait_job(tc, token, started["job_id"])
        assert job["status"] == "done"
        assert job["exit_code"] == 0
        assert "alpha\nbeta\n" in job["log"]
        assert "exit=0" in job["log"]
        assert job["started_at"] and job["finished_at"]

    def test_failed_job_records_exit_code(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        popen["factory"] = lambda args: FakeProcess(returncode=3)
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        job = wait_job(tc, token, started["job_id"])
        assert job["status"] == "failed"
        assert job["exit_code"] == 3

    def test_history_omits_log(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        popen["factory"] = lambda args: FakeProcess(lines=["noise\n"])
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"])
        jobs = tc.get(f"/t/{token}/api/jobs").json()["jobs"]
        assert "log" not in jobs[0]

    def test_unknown_job_404(self, client: Any) -> None:
        tc, token, app = client
        assert tc.get(f"/t/{token}/api/jobs/999").status_code == 404
        assert tc.post(f"/t/{token}/api/jobs/999/cancel").status_code == 404

    def test_cancel_finished_job_409(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"])
        response = tc.post(f"/t/{token}/api/jobs/{started['job_id']}/cancel")
        assert response.status_code == 409


class TestPersistence:
    def test_history_survives_restart(self, env: Path, popen: Any) -> None:
        popen["factory"] = lambda args: FakeProcess(lines=["persisted line\n"])
        app1 = create_app()
        tc1 = TestClient(app1)
        token1 = app1.state.token
        started = run(tc1, token1, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc1, token1, started["job_id"])
        app1.state.jobs.stop()

        app2 = create_app()
        tc2 = TestClient(app2)
        token2 = app2.state.token
        try:
            jobs = tc2.get(f"/t/{token2}/api/jobs").json()["jobs"]
            assert len(jobs) == 1
            detail = tc2.get(f"/t/{token2}/api/jobs/{started['job_id']}").json()
            assert detail["status"] == "done"
            assert "persisted line" in detail["log"]
        finally:
            app2.state.jobs.stop()

    def test_stale_running_marked_interrupted(self, env: Path) -> None:
        app1 = create_app()
        app1.state.jobs.stop()
        db_path = env / "wog_dump.db"
        with sqlite3.connect(db_path) as db:
            db.execute(
                "INSERT INTO webapp_jobs "
                "(stage, options, argv, status, created_at, log) "
                "VALUES ('build-scene', '{}', '[]', 'running', "
                "'2026-01-01T00:00:00+00:00', 'partial output\n')"
            )
        app2 = create_app()
        tc2 = TestClient(app2)
        token2 = app2.state.token
        try:
            detail = tc2.get(f"/t/{token2}/api/jobs/1").json()
            assert detail["status"] == "failed"
            assert detail["finished_at"]
            assert "partial output" in detail["log"]
            assert "interrupted" in detail["log"]
        finally:
            app2.state.jobs.stop()

    def test_queued_jobs_resume_after_restart(self, env: Path, popen: Any) -> None:
        app1 = create_app()
        app1.state.jobs.stop()
        db_path = env / "wog_dump.db"
        with sqlite3.connect(db_path) as db:
            db.execute(
                "INSERT INTO webapp_jobs "
                "(stage, options, argv, status, created_at) "
                "VALUES ('build-scene', '{\"weapons\": [\"ak47\"]}', "
                '\'["wog-dump", "--no-progress", "build-scene", "ak47"]\', '
                "'queued', '2026-01-01T00:00:00+00:00')"
            )
        app2 = create_app()
        tc2 = TestClient(app2)
        token2 = app2.state.token
        try:
            job = wait_job(tc2, token2, 1)
            assert job["status"] == "done"
            assert popen["argv"] == [
                ["wog-dump", "--no-progress", "build-scene", "ak47"]
            ]
        finally:
            app2.state.jobs.stop()


class TestCancel:
    def test_cancel_queued_job(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        release = threading.Event()
        popen["factory"] = lambda args: FakeProcess(release=release)
        first = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        second = run(tc, token, {"stage": "build-scene", "weapon": "colt_1911"})
        wait_job(tc, token, first["job_id"], statuses=("running",))
        response = tc.post(f"/t/{token}/api/jobs/{second['job_id']}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        job = wait_job(tc, token, second["job_id"])
        assert job["status"] == "cancelled"
        assert job["finished_at"]
        release.set()
        wait_job(tc, token, first["job_id"])
        assert len(popen["argv"]) == 1

    def test_cancel_running_job_terminates_process(
        self, client: Any, popen: Any
    ) -> None:
        tc, token, app = client
        release = threading.Event()
        popen["factory"] = lambda args: FakeProcess(lines=["boot\n"], release=release)
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"], statuses=("running",))
        response = tc.post(f"/t/{token}/api/jobs/{started['job_id']}/cancel")
        assert response.status_code == 200
        job = wait_job(tc, token, started["job_id"])
        assert job["status"] == "cancelled"
        assert popen["processes"][0].terminated is True
        assert "boot" in job["log"]


class TestEvents:
    def test_replay_finished_job(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        popen["factory"] = lambda args: FakeProcess(
            lines=["line-one\n", "line-two\n", "line-three\n"]
        )
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"])
        response = tc.get(f"/t/{token}/api/events?job_id={started['job_id']}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = response.text
        assert "data: line-one" in text
        assert "data: line-three" in text
        assert "event: done" in text
        assert '"status": "done"' in text

    def test_reconnect_resumes_after_last_event_id(
        self, client: Any, popen: Any
    ) -> None:
        tc, token, app = client
        popen["factory"] = lambda args: FakeProcess(
            lines=["line-one\n", "line-two\n", "line-three\n"]
        )
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"])
        url = f"/t/{token}/api/events?job_id={started['job_id']}"
        first = tc.get(url).text
        assert "id: 3" in first
        resumed = tc.get(url, headers={"Last-Event-ID": "2"}).text
        assert "line-one" not in resumed
        assert "line-two" not in resumed
        assert "data: line-three" in resumed
        assert "event: done" in resumed

    def test_events_default_targets_last_job(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        popen["factory"] = lambda args: FakeProcess(lines=["last job line\n"])
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"])
        response = tc.get(f"/t/{token}/api/events")
        assert "last job line" in response.text

    def test_events_without_jobs(self, client: Any) -> None:
        tc, token, app = client
        response = tc.get(f"/t/{token}/api/events")
        assert response.status_code == 200
        assert "no running stage" in response.text
        assert "event: done" in response.text

    def test_events_unknown_job_404(self, client: Any) -> None:
        tc, token, app = client
        assert tc.get(f"/t/{token}/api/events?job_id=999").status_code == 404

    def test_live_tail_until_done(self, client: Any, popen: Any) -> None:
        tc, token, app = client
        release = threading.Event()
        popen["factory"] = lambda args: FakeProcess(lines=["boot\n"], release=release)
        started = run(tc, token, {"stage": "build-scene", "weapon": "ak47"})
        wait_job(tc, token, started["job_id"], statuses=("running",))
        result: dict[str, Any] = {}

        def consume() -> None:
            result["response"] = tc.get(
                f"/t/{token}/api/events?job_id={started['job_id']}"
            )

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        time.sleep(0.3)
        release.set()
        reader.join(timeout=10)
        assert not reader.is_alive()
        text = result["response"].text
        assert "data: boot" in text
        assert "event: done" in text


class TestValidation:
    def test_unsafe_weapon_in_list_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapons": ["ak47", "../evil"]},
        )
        assert response.status_code == 400

    def test_weapons_must_be_list_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapons": "ak47"},
        )
        assert response.status_code == 400

    def test_build_scene_needs_weapon_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(f"/t/{token}/api/run", json={"stage": "build-scene"})
        assert response.status_code == 400

    def test_unknown_format_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapon": "ak47", "formats": ["obj"]},
        )
        assert response.status_code == 400

    def test_non_boolean_flag_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "build-scene", "weapon": "ak47", "viewer": "yes"},
        )
        assert response.status_code == 400

    def test_render_needs_weapons_or_all_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(f"/t/{token}/api/run", json={"stage": "render"})
        assert response.status_code == 400

    def test_print_export_needs_weapon_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(f"/t/{token}/api/run", json={"stage": "print-export"})
        assert response.status_code == 400

    def test_invalid_part_name_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "print-export", "weapon": "ak47", "parts": ["bad\nname"]},
        )
        assert response.status_code == 400

    def test_unpack_single_weapon_only_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "unpack-assets", "weapons": ["ak47", "colt_1911"]},
        )
        assert response.status_code == 400

    def test_download_unknown_weapon_400(self, client: Any) -> None:
        tc, token, app = client
        response = tc.post(
            f"/t/{token}/api/run",
            json={"stage": "download-assets", "weapon": "not_in_catalog"},
        )
        assert response.status_code == 400


class TestArgv:
    """POST api/run returns the validated argv without starting a job."""

    def test_download_weapons_flags(self, client: Any) -> None:
        tc, token, app = client
        started = run(
            tc, token, {"stage": "download-weapons", "force": True, "validate": True}
        )
        assert started["args"] == [
            "wog-dump",
            "--no-progress",
            "download-weapons",
            "--force",
            "--validate",
        ]

    def test_download_assets_update_keys_and_weapons(self, client: Any) -> None:
        tc, token, app = client
        started = run(
            tc,
            token,
            {"stage": "download-assets", "weapon": "ak47", "update_keys": True},
        )
        assert started["args"] == [
            "wog-dump",
            "--no-progress",
            "download-assets",
            "--weapons",
            "ak47",
            "--update-keys",
        ]

    def test_build_scene_full_options(self, client: Any) -> None:
        tc, token, app = client
        started = run(
            tc,
            token,
            {
                "stage": "build-scene",
                "weapon": "ak47",
                "state": "parts",
                "viewer": True,
                "animations": True,
                "formats": ["stl", "usdz"],
            },
        )
        assert started["args"] == [
            "wog-dump",
            "--no-progress",
            "build-scene",
            "ak47",
            "--state",
            "parts",
            "--viewer",
            "--animations",
            "--format",
            "stl,usdz",
        ]

    def test_render_video(self, client: Any) -> None:
        tc, token, app = client
        started = run(tc, token, {"stage": "render", "weapon": "ak47", "video": True})
        assert started["args"] == [
            "wog-dump",
            "--no-progress",
            "render",
            "ak47",
            "--video",
        ]

    def test_render_all(self, client: Any) -> None:
        tc, token, app = client
        started = run(tc, token, {"stage": "render", "all": True})
        assert started["args"] == ["wog-dump", "--no-progress", "render", "--all"]

    def test_print_export_parts(self, client: Any) -> None:
        tc, token, app = client
        started = run(
            tc,
            token,
            {"stage": "print-export", "weapon": "ak47", "parts": ["bolt", "receiver"]},
        )
        assert started["args"] == [
            "wog-dump",
            "--no-progress",
            "print-export",
            "ak47",
            "--part",
            "bolt",
            "--part",
            "receiver",
        ]

    def test_unpack_asset_filter(self, client: Any) -> None:
        tc, token, app = client
        started = run(tc, token, {"stage": "unpack-assets", "weapon": "ak47"})
        assert started["args"] == [
            "wog-dump",
            "--no-progress",
            "unpack-assets",
            "--asset-filter",
            "ak47",
        ]


class TestNewEndpointsGuard:
    def test_wrong_token_404_everywhere(self, client: Any) -> None:
        tc, token, app = client
        for path in (
            "/api/jobs",
            "/api/jobs/1",
            "/api/jobs/1/cancel",
            "/api/events",
            "/api/i18n",
        ):
            method = tc.post if path.endswith("/cancel") else tc.get
            assert method(f"/t/wrongtoken{path}").status_code == 404
