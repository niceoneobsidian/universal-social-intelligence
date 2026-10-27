from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from usie.config import Settings
from usie.db import SqliteRepository
from usie.evidence import store_artifact
from usie.models import (
    ArtifactDescriptor,
    ArtifactKind,
    ExtractionProvenance,
    ExtractionResult,
    Status,
)
from usie.ocif_export import ExportError, export_envelopes, redact_url, write_export


def _fixture_capture(
    repo: SqliteRepository,
    evidence_root: Path,
    *,
    status: Status = Status.PARTIAL,
    with_extraction: bool = False,
    submitted_url: str = "https://example.com/post?access_token=topsecret&utm_source=test",
) -> str:
    run = repo.create_run(1, {}, {})
    normalized = "https://example.com/post"
    source_id = repo.ensure_source(submitted_url, normalized, "UNIVERSAL")
    job_id, _ = repo.create_job(run, source_id, submitted_url)
    capture_id = str(uuid.uuid4())
    repo.start_capture(capture_id, job_id, run, source_id, 1, submitted_url)
    payload = b"<html>public fixture</html>"
    stored = store_artifact(evidence_root, capture_id, ArtifactKind.RENDERED_DOM_HTML, payload, 1024)
    artifact_id = str(uuid.uuid4())
    repo.add_artifact(
        capture_id,
        ArtifactDescriptor(
            artifact_id=artifact_id,
            kind=ArtifactKind.RENDERED_DOM_HTML,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            media_type="text/html",
            description="test fixture",
        ),
    )
    if with_extraction:
        repo.add_extraction_result(
            ExtractionResult(
                extraction_id=str(uuid.uuid4()),
                capture_id=capture_id,
                source_id=source_id,
                normalized_url=normalized,
                extractor="generic_metadata",
                extractor_version="1.0",
                status=Status.SUCCESS,
                post_text="A verified public post",
                provenance={
                    "post_text": ExtractionProvenance(
                        artifact_id=artifact_id,
                        artifact_sha256=stored.sha256,
                        locator="meta[property=og:description]",
                    )
                },
                schema_version="1",
                created_at="2026-10-01T00:00:00Z",
            )
        )
    repo.finish_capture(capture_id, status, "fixture", None, None, None, [])
    return capture_id


def test_export_is_read_only_and_preserves_canonical_rows(repo: SqliteRepository, settings: Settings) -> None:
    capture_id = _fixture_capture(repo, settings.evidence_root)
    db_path = settings.db_path
    before = {
        table: repo.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        for table in ("runs", "sources", "jobs", "captures", "artifacts", "audit_events")
    }
    schema_version = repo.query("SELECT MAX(version) AS v FROM schema_migrations")[0]["v"]
    repo.close()

    result = export_envelopes(db_path, settings.evidence_root, capture_id=capture_id)
    readonly = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        after = {
            table: readonly.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        after_version = readonly.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        readonly.close()
    assert len(result) == 1
    assert before == after
    assert schema_version == after_version
    assert result[0]["capture"]["capture_id"] == capture_id


def test_export_redacts_secret_query_parameters(repo: SqliteRepository, settings: Settings) -> None:
    capture_id = _fixture_capture(repo, settings.evidence_root)
    envelope = export_envelopes(
        settings.db_path, settings.evidence_root, capture_id=capture_id
    )[0]
    url = envelope["source"]["submitted_url"]
    assert "topsecret" not in url
    assert "utm_source" not in url
    assert "[REDACTED]" in url


def test_redact_url_removes_credentials_fragment_and_token() -> None:
    value = redact_url("https://user:pass@example.com/p?token=abc&view=1#private")
    assert value == "https://example.com/p?token=%5BREDACTED%5D&view=1"
    assert "user" not in value and "pass" not in value and "private" not in value


def test_success_requires_verified_extraction_provenance(repo, settings) -> None:
    capture_id = _fixture_capture(repo, settings.evidence_root, status=Status.SUCCESS)
    with pytest.raises(ExportError, match="without successful extraction"):
        export_envelopes(settings.db_path, settings.evidence_root, capture_id=capture_id)


def test_partial_capture_without_extraction_is_exportable(repo, settings) -> None:
    capture_id = _fixture_capture(repo, settings.evidence_root)
    envelope = export_envelopes(
        settings.db_path, settings.evidence_root, capture_id=capture_id
    )[0]
    assert envelope["capture"]["status"] == "partial"
    assert envelope["extraction"] is None
    assert envelope["artifacts"][0]["sha256"] == hashlib.sha256(
        b"<html>public fixture</html>"
    ).hexdigest()


def test_tampered_artifact_fails_closed(repo, settings) -> None:
    capture_id = _fixture_capture(repo, settings.evidence_root)
    artifact = repo.query("SELECT relative_path FROM artifacts WHERE capture_id=?", (capture_id,))[0]
    (settings.evidence_root / artifact["relative_path"]).write_bytes(b"tampered")
    with pytest.raises(ExportError, match="integrity check"):
        export_envelopes(settings.db_path, settings.evidence_root, capture_id=capture_id)


def test_success_with_extraction_exports_provenance(repo, settings) -> None:
    capture_id = _fixture_capture(
        repo, settings.evidence_root, status=Status.SUCCESS, with_extraction=True
    )
    envelope = export_envelopes(
        settings.db_path, settings.evidence_root, capture_id=capture_id, revision="abc123"
    )[0]
    assert envelope["producer"]["revision"] == "abc123"
    assert envelope["extraction"]["status"] == "success"
    assert envelope["extraction"]["evidence_refs"] == [
        envelope["artifacts"][0]["artifact_id"]
    ]
    assert envelope["extraction"]["fields"]["post_text"] == "A verified public post"


def test_export_skips_unfinished_captures(repo, settings) -> None:
    run = repo.create_run(1, {}, {})
    source = repo.ensure_source("https://example.com/p", "https://example.com/p", "UNIVERSAL")
    job, _ = repo.create_job(run, source, "https://example.com/p")
    capture = str(uuid.uuid4())
    repo.start_capture(capture, job, run, source, 1, "https://example.com/p")
    assert export_envelopes(settings.db_path, settings.evidence_root) == []


def test_export_file_is_private_and_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "bundle.json"
    write_export(output, [{"ok": True}])
    assert json.loads(output.read_text()) == [{"ok": True}]
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_export(output, [])
    assert json.loads(output.read_text()) == [{"ok": True}]
