"""SQLite repository. All writes are transactional and append-only for observations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from usie.db.migrations import apply_migrations
from usie.models import ArtifactDescriptor, Completeness, Observation, Status


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SqliteRepository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        apply_migrations(self._conn)
        if self.path.exists():
            self.path.chmod(0o600)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # --- audit -------------------------------------------------------------------------
    def audit(
        self,
        action: str,
        *,
        actor: str = "system",
        subject_type: str | None = None,
        subject_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.tx() as c:
            self._audit(c, action, actor, subject_type, subject_id, details)

    @staticmethod
    def _audit(
        c: sqlite3.Connection,
        action: str,
        actor: str,
        subject_type: str | None,
        subject_id: str | None,
        details: dict[str, Any] | None,
    ) -> None:
        c.execute(
            "INSERT INTO audit_events(occurred_at,actor,action,subject_type,subject_id,"
            "details_json) VALUES(?,?,?,?,?,?)",
            (utc_now(), actor, action, subject_type, subject_id, _dumps(details or {})),
        )

    # --- runs / jobs -------------------------------------------------------------------
    def create_run(
        self, input_count: int, config: dict[str, object], tool_versions: dict[str, str]
    ) -> str:
        run_id = str(uuid.uuid4())
        with self.tx() as c:
            c.execute(
                "INSERT INTO runs(run_id,started_at,status,input_count,config_json,"
                "tool_versions_json) VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    utc_now(),
                    Status.RUNNING,
                    input_count,
                    _dumps(config),
                    _dumps(tool_versions),
                ),
            )
            self._audit(c, "run_started", "system", "run", run_id, {"input_count": input_count})
        return run_id

    def finish_run(self, run_id: str, status: Status, summary: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET status=?, finished_at=?, summary_json=? WHERE run_id=?",
                (status, utc_now(), _dumps(summary), run_id),
            )
            self._audit(c, "run_finished", "system", "run", run_id, {"status": status.value})

    def ensure_source(self, submitted_url: str, normalized_url: str, platform: str) -> str:
        """Source identity = normalized URL. Never overwrites the first-seen record."""
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, normalized_url))
        with self.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO sources(source_id,source_url,normalized_url,platform,"
                "first_seen_at) VALUES(?,?,?,?,?)",
                (source_id, submitted_url, normalized_url, platform, utc_now()),
            )
        return source_id

    def create_job(self, run_id: str, source_id: str, submitted_url: str) -> tuple[str, bool]:
        """Idempotent per (run, source). Returns (job_id, created)."""
        with self.tx() as c:
            existing = c.execute(
                "SELECT job_id FROM jobs WHERE run_id=? AND source_id=?", (run_id, source_id)
            ).fetchone()
            if existing:
                return str(existing["job_id"]), False
            job_id = str(uuid.uuid4())
            c.execute(
                "INSERT INTO jobs(job_id,run_id,source_id,submitted_url,status,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (job_id, run_id, source_id, submitted_url, Status.QUEUED, utc_now()),
            )
            return job_id, True

    def start_job(self, job_id: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status=?, started_at=COALESCE(started_at,?) WHERE job_id=?",
                (Status.RUNNING, utc_now(), job_id),
            )

    def finish_job(
        self, job_id: str, status: Status, reason: str | None, error_code: str | None
    ) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status=?, status_reason=?, error_code=?, finished_at=? "
                "WHERE job_id=?",
                (status, reason, error_code, utc_now(), job_id),
            )

    # --- captures ----------------------------------------------------------------------
    def start_capture(
        self,
        capture_id: str,
        job_id: str,
        run_id: str,
        source_id: str,
        attempt: int,
        submitted_url: str,
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO captures(capture_id,job_id,run_id,source_id,attempt,captured_at,"
                "status,submitted_url) VALUES(?,?,?,?,?,?,?,?)",
                (
                    capture_id,
                    job_id,
                    run_id,
                    source_id,
                    attempt,
                    utc_now(),
                    Status.RUNNING,
                    submitted_url,
                ),
            )
            c.execute("UPDATE jobs SET attempts=? WHERE job_id=?", (attempt, job_id))

    def add_artifact(self, capture_id: str, a: ArtifactDescriptor) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO artifacts(artifact_id,capture_id,kind,relative_path,sha256,"
                "byte_size,media_type,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    a.artifact_id,
                    capture_id,
                    a.kind.value,
                    a.relative_path,
                    a.sha256,
                    a.byte_size,
                    a.media_type,
                    a.description,
                    utc_now(),
                ),
            )

    def finish_capture(
        self,
        capture_id: str,
        status: Status,
        reason: str,
        error_code: str | None,
        obs: Observation | None,
        completeness: Completeness | None,
        issues: list[str],
    ) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE captures SET status=?, status_reason=?, error_code=?, finished_at=?, "
                "final_url=?, http_status=?, page_title=?, redirect_chain_json=?, "
                "completeness_json=?, tool_versions_json=?, issues_json=? WHERE capture_id=?",
                (
                    status,
                    reason,
                    error_code,
                    utc_now(),
                    obs.final_url if obs else None,
                    obs.http_status if obs else None,
                    obs.title if obs else None,
                    _dumps([h.model_dump() for h in obs.redirect_chain]) if obs else "[]",
                    _dumps(completeness.model_dump()) if completeness else "{}",
                    _dumps(obs.tool_versions) if obs else "{}",
                    _dumps(issues),
                    capture_id,
                ),
            )

    # --- recovery ----------------------------------------------------------------------
    def recover_interrupted(self) -> dict[str, int]:
        """Mark captures/jobs/runs left 'running' or 'queued' by a crash as failed(interrupted).

        Evidence already on disk is kept. Nothing is deleted; a later run creates new captures.
        """
        with self.tx() as c:
            caps = c.execute(
                "UPDATE captures SET status='failed', status_reason='process_interrupted', "
                "error_code='interrupted', finished_at=? WHERE status='running'",
                (utc_now(),),
            ).rowcount
            jobs = c.execute(
                "UPDATE jobs SET status='failed', status_reason='process_interrupted', "
                "error_code='interrupted', finished_at=? WHERE status IN ('running','queued')",
                (utc_now(),),
            ).rowcount
            runs = c.execute(
                "UPDATE runs SET status='failed', finished_at=? WHERE status='running'",
                (utc_now(),),
            ).rowcount
            if caps or jobs or runs:
                self._audit(
                    c,
                    "recovered_interrupted",
                    "system",
                    None,
                    None,
                    {"captures": caps, "jobs": jobs, "runs": runs},
                )
        return {"captures": caps, "jobs": jobs, "runs": runs}

    def cancel_unfinished(self, run_id: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE captures SET status='cancelled', status_reason='cancelled', "
                "finished_at=? WHERE run_id=? AND status='running'",
                (utc_now(), run_id),
            )
            c.execute(
                "UPDATE jobs SET status='cancelled', status_reason='cancelled', "
                "finished_at=? WHERE run_id=? AND status IN ('queued','running')",
                (utc_now(), run_id),
            )

    # --- reporting ---------------------------------------------------------------------
    def status_report(self) -> list[dict[str, Any]]:
        return self.query(
            "SELECT s.platform AS platform, c.status AS status, COUNT(*) AS count "
            "FROM captures c JOIN sources s ON s.source_id=c.source_id "
            "GROUP BY s.platform, c.status ORDER BY s.platform, c.status"
        )

    def artifacts_for_verification(self) -> list[dict[str, Any]]:
        return self.query(
            "SELECT artifact_id,capture_id,kind,relative_path,sha256,byte_size "
            "FROM artifacts ORDER BY created_at"
        )
