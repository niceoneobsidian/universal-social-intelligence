from __future__ import annotations

import uuid

import pytest

from usie.db import SqliteRepository
from usie.models import ArtifactDescriptor, ArtifactKind, ExtractionResult, Observation, Status

H = "a" * 64


def make_job(repo: SqliteRepository, url: str = "https://a.test/p") -> tuple[str, str, str]:
    run = repo.create_run(1, {}, {})
    src = repo.ensure_source(url, url, "UNIVERSAL")
    job, _ = repo.create_job(run, src, url)
    return run, src, job


def test_source_identity_is_stable_and_not_overwritten(repo: SqliteRepository) -> None:
    a = repo.ensure_source("https://a.test/p?utm_source=x", "https://a.test/p", "UNIVERSAL")
    b = repo.ensure_source("https://a.test/p", "https://a.test/p", "UNIVERSAL")
    assert a == b
    rows = repo.query("SELECT source_url FROM sources")
    assert rows == [{"source_url": "https://a.test/p?utm_source=x"}]  # first-seen preserved


def test_job_is_idempotent_per_run(repo: SqliteRepository) -> None:
    run, src, job = make_job(repo)
    again, created = repo.create_job(run, src, "https://a.test/p")
    assert again == job and not created


def test_reprocessing_creates_new_captures_and_keeps_history(repo: SqliteRepository) -> None:
    run, src, job = make_job(repo)
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    repo.start_capture(c1, job, run, src, 1, "https://a.test/p")
    repo.finish_capture(c1, Status.FAILED, "http_503", "http_server_error", None, None, [])
    repo.start_capture(c2, job, run, src, 2, "https://a.test/p")
    repo.finish_capture(
        c2,
        Status.PARTIAL,
        "evidence_only",
        None,
        Observation(http_status=200, final_url="https://a.test/p"),
        None,
        [],
    )
    rows = repo.query("SELECT capture_id,status FROM captures ORDER BY attempt")
    assert [r["status"] for r in rows] == ["failed", "partial"]
    # same attempt number twice for one job is rejected
    with pytest.raises(Exception, match="UNIQUE"):
        repo.start_capture(str(uuid.uuid4()), job, run, src, 2, "https://a.test/p")


def test_transaction_rolls_back_on_error(repo: SqliteRepository) -> None:
    with pytest.raises(RuntimeError), repo.tx() as c:
        c.execute("INSERT INTO runs(run_id,started_at,status) VALUES('x','t','running')")
        raise RuntimeError("boom")
    assert repo.query("SELECT * FROM runs WHERE run_id='x'") == []


def test_artifact_unique_per_capture_kind(repo: SqliteRepository) -> None:
    run, src, job = make_job(repo)
    cid = str(uuid.uuid4())
    repo.start_capture(cid, job, run, src, 1, "u")
    d = ArtifactDescriptor(
        artifact_id=str(uuid.uuid4()),
        kind=ArtifactKind.SCREENSHOT_VIEWPORT,
        relative_path="captures/x/a.png",
        sha256=H,
        byte_size=3,
        media_type="image/png",
        description="d",
    )
    repo.add_artifact(cid, d)
    with pytest.raises(Exception, match="UNIQUE"):
        repo.add_artifact(
            cid,
            d.model_copy(
                update={"artifact_id": str(uuid.uuid4()), "relative_path": "captures/x/b.png"}
            ),
        )


def test_recover_interrupted(repo: SqliteRepository) -> None:
    run, src, job = make_job(repo)
    repo.start_job(job)
    repo.start_capture(str(uuid.uuid4()), job, run, src, 1, "u")
    assert repo.recover_interrupted() == {"captures": 1, "jobs": 1, "runs": 1}
    assert repo.query("SELECT status,error_code FROM captures")[0] == {
        "status": "failed",
        "error_code": "interrupted",
    }
    assert repo.recover_interrupted() == {"captures": 0, "jobs": 0, "runs": 0}
    assert repo.query("SELECT action FROM audit_events WHERE action='recovered_interrupted'")


def test_legacy_view_latest_capture_and_lossy_mapping(repo: SqliteRepository) -> None:
    run, src, job = make_job(repo)
    statuses = [
        (Status.PARTIAL, "partial"),
        (Status.BLOCKED, "inaccessible"),
        (Status.UNAVAILABLE, "inaccessible"),
        (Status.FAILED, "inaccessible"),
        (Status.SUCCESS, "success"),
    ]
    for attempt, (status, expected_label) in enumerate(statuses, start=1):
        cid = str(uuid.uuid4())
        repo.start_capture(cid, job, run, src, attempt, "u")
        repo.finish_capture(cid, status, "r", None, None, None, [])
        rows = repo.query("SELECT * FROM social_evidence")
        assert len(rows) == 1
        assert rows[0]["status_label"] == expected_label
    assert set(rows[0]) == {
        "id",
        "source_url",
        "platform",
        "author",
        "captured_at",
        "status_label",
        "post_text",
        "raw_metadata_json",
    }
    assert rows[0]["id"] == src


def test_legacy_view_is_read_only(repo: SqliteRepository) -> None:
    with pytest.raises(Exception, match="view"):
        repo._conn.execute("INSERT INTO social_evidence(id) VALUES('x')")
    with pytest.raises(Exception, match="view"):
        repo._conn.execute("DELETE FROM social_evidence")


def test_extraction_results_are_append_only(repo: SqliteRepository) -> None:
    run, src, job = make_job(repo)
    capture = str(uuid.uuid4())
    repo.start_capture(capture, job, run, src, 1, "https://a.test/p")
    extraction = ExtractionResult(
        extraction_id=str(uuid.uuid4()),
        capture_id=capture,
        source_id=src,
        normalized_url="https://a.test/p",
        extractor="test",
        extractor_version="1",
        status=Status.PARTIAL,
        created_at="2025-01-01T00:00:00Z",
    )
    repo.add_extraction_result(extraction)
    with pytest.raises(Exception, match="append-only"):
        repo._conn.execute(
            "UPDATE extraction_results SET status='failed' WHERE extraction_id=?",
            (extraction.extraction_id,),
        )
    with pytest.raises(Exception, match="append-only"):
        repo._conn.execute(
            "DELETE FROM extraction_results WHERE extraction_id=?",
            (extraction.extraction_id,),
        )
