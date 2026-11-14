"""Persistent job queue for the web UI.

Jobs are rows in the ``webapp_jobs`` table of the shared local SQLite
store (the config's ``db_path``, same file as keys and weapon metadata,
but an own connection — ``core.database`` stays untouched). One worker
thread runs jobs sequentially as CLI subprocesses, exactly like the
terminal; every output line is appended to the job's log column so
history and SSE replay survive an app restart. On startup, jobs left
``running`` by a crash are marked failed/interrupted; ``queued`` jobs
were never started, so the new worker simply picks them up.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wog_dump.core.config import WOGConfig

__all__ = ["Job", "JobQueue", "TERMINAL_STATUSES"]

TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webapp_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,
    options     TEXT NOT NULL DEFAULT '{}',
    argv        TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'queued',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    exit_code   INTEGER,
    log         TEXT NOT NULL DEFAULT ''
);
"""

_COLUMNS = (
    "id",
    "stage",
    "options",
    "argv",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "exit_code",
    "log",
)
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM webapp_jobs"

_INTERRUPT_NOTE = "interrupted: the server restarted while this job was running\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """One queued/finished CLI run, including its full replayable log."""

    id: int
    stage: str
    options: dict[str, Any]
    argv: list[str]
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    log: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Job:
        values = dict(zip(_COLUMNS, row))
        return cls(
            id=int(values["id"]),
            stage=str(values["stage"]),
            options=json.loads(values["options"]),
            argv=json.loads(values["argv"]),
            status=str(values["status"]),
            created_at=str(values["created_at"]),
            started_at=values["started_at"],
            finished_at=values["finished_at"],
            exit_code=values["exit_code"],
            log=str(values["log"]),
        )

    def to_dict(self, include_log: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "stage": self.stage,
            "options": self.options,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
        }
        if include_log:
            payload["log"] = self.log
        return payload


class JobQueue:
    """SQLite-backed FIFO queue with a single subprocess worker thread."""

    def __init__(self, config: WOGConfig) -> None:
        self.config = config
        self.db_path = Path(config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._cancelling: set[int] = set()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "UPDATE webapp_jobs SET status = 'failed', finished_at = ?, "
                "log = log || ? WHERE status = 'running'",
                (_now(), _INTERRUPT_NOTE),
            )
        self._worker = threading.Thread(
            target=self._work, name="webapp-jobs", daemon=True
        )
        self._worker.start()

    # -- lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        """Stop the worker and close the connection (app shutdown)."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
            self._changed.notify_all()
        self._worker.join(timeout=5)
        with self._lock:
            self._conn.close()

    def wait_for_change(self, timeout: float = 0.5) -> None:
        """Block until a log line or status changed (SSE tail wakeup)."""
        with self._changed:
            self._changed.wait(timeout)

    def _notify(self) -> None:
        with self._changed:
            self._changed.notify_all()

    # -- queries -----------------------------------------------------------

    def enqueue(
        self, stage: str, options: dict[str, Any], argv: list[str]
    ) -> tuple[int, int]:
        """Append a job; returns (job id, 1-based queue position)."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO webapp_jobs (stage, options, argv, status, created_at) "
                "VALUES (?, ?, ?, 'queued', ?)",
                (stage, json.dumps(options), json.dumps(argv), _now()),
            )
            job_id = int(cursor.lastrowid or 0)
            position = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM webapp_jobs "
                    "WHERE status IN ('queued', 'running') AND id <= ?",
                    (job_id,),
                ).fetchone()[0]
            )
        self._wake.set()
        self._notify()
        return job_id, position

    def get_job(self, job_id: int) -> Job | None:
        with self._lock:
            row = self._conn.execute(_SELECT + " WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row is not None else None

    def list_jobs(self, limit: int = 200) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                _SELECT + " ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Job.from_row(row) for row in rows]

    def current_job_id(self) -> int | None:
        """The running job, else the newest one (SSE default target)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM webapp_jobs WHERE status = 'running' "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT id FROM webapp_jobs ORDER BY id DESC LIMIT 1"
                ).fetchone()
        return int(row[0]) if row is not None else None

    # -- cancellation ------------------------------------------------------

    def cancel(self, job_id: int) -> str:
        """Cancel a queued job or terminate the running process.

        Returns the resulting status ('running' until the terminated
        process is reaped). Raises KeyError for unknown ids and
        ValueError for already finished jobs.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM webapp_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            status = str(row[0])
            if status in TERMINAL_STATUSES:
                raise ValueError(status)
            if status == "queued":
                with self._conn:
                    self._conn.execute(
                        "UPDATE webapp_jobs SET status = 'cancelled', "
                        "finished_at = ? WHERE id = ? AND status = 'queued'",
                        (_now(), job_id),
                    )
                status = "cancelled"
            else:
                self._cancelling.add(job_id)
                if self._process is not None and self._process.poll() is None:
                    self._process.terminate()
        self._notify()
        return status

    # -- worker ------------------------------------------------------------

    def _work(self) -> None:
        while not self._stop.is_set():
            job = self._claim()
            if job is None:
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue
            self._run(job)

    def _claim(self) -> Job | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                _SELECT + " WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job = Job.from_row(row)
            self._conn.execute(
                "UPDATE webapp_jobs SET status = 'running', started_at = ? "
                "WHERE id = ?",
                (_now(), job.id),
            )
            job.status = "running"
        self._notify()
        return job

    def _run(self, job: Job) -> None:
        try:
            process = subprocess.Popen(
                job.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(self.config.base_dir),
            )
        except OSError as error:
            self._append_log(job.id, f"failed to start: {error}\n")
            self._finish(job.id, "failed", None)
            return
        with self._lock:
            self._process = process
        stdout = process.stdout
        if stdout is not None:
            for line in iter(stdout.readline, ""):
                if self._stop.is_set():
                    process.terminate()
                self._append_log(job.id, line if line.endswith("\n") else line + "\n")
        code = process.wait()
        with self._lock:
            self._process = None
            cancelled = job.id in self._cancelling
            self._cancelling.discard(job.id)
        self._append_log(job.id, f"exit={code}\n")
        status = "cancelled" if cancelled else ("done" if code == 0 else "failed")
        self._finish(job.id, status, code)

    def _append_log(self, job_id: int, text: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE webapp_jobs SET log = log || ? WHERE id = ?",
                (text, job_id),
            )
        self._notify()

    def _finish(self, job_id: int, status: str, exit_code: int | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE webapp_jobs SET status = ?, finished_at = ?, "
                "exit_code = ? WHERE id = ?",
                (status, _now(), exit_code, job_id),
            )
        self._notify()
