from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path

from usie.db.repository import SqliteRepository
from usie.integrations.ocif import export_capture


def test_export_from_real_sqlite_capture_preserves_artifact_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        evidence_root = base / "evidence"
        evidence_root.mkdir()
        artifact_bytes = b"real SQLite capture fixture"
        artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
        (evidence_root / "page.html").write_bytes(artifact_bytes)

        repository = SqliteRepository(base / "usie.sqlite3")
        run_id = str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        capture_id = str(uuid.uuid4())
        artifact_id = str(uuid.uuid4())
        extraction_id = str(uuid.uuid4())
        timestamp = "2026-10-22T10:00:00+00:00"
        url = "https://example.org/posts/real-fixture"
        connection = repository._conn
        connection.execute(
            "INSERT INTO runs(run_id, started_at, status, input_count, config_json, "
            "tool_versions_json) VALUES (?, ?, 'success', 1, '{}', '{}')",
            (run_id, timestamp),
        )
        connection.execute(
            "INSERT INTO sources(source_id, source_url, normalized_url, platform, first_seen_at) "
            "VALUES (?, ?, ?, 'UNIVERSAL', ?)",
            (source_id, url, url, timestamp),
        )
        connection.execute(
            "INSERT INTO jobs(job_id, run_id, source_id, submitted_url, status, created_at) "
            "VALUES (?, ?, ?, ?, 'success', ?)",
            (job_id, run_id, source_id, url, timestamp),
        )
        connection.execute(
            "INSERT INTO captures(capture_id, job_id, run_id, source_id, attempt, captured_at, "
            "finished_at, status, submitted_url, issues_json) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, 'success', ?, '[]')",
            (capture_id, job_id, run_id, source_id, timestamp, timestamp, url),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, capture_id, kind, relative_path, sha256, "
            "byte_size, media_type, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                capture_id,
                "rendered_dom_html",
                "page.html",
                artifact_hash,
                len(artifact_bytes),
                "text/html",
                "Integration test artifact",
                timestamp,
            ),
        )
        reference = {
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_hash,
            "locator": "meta[property=og:description]",
        }
        provenance = {
            "post_text": reference,
            "title": reference,
            "canonical_url": reference,
        }
        connection.execute(
            "INSERT INTO extraction_results(extraction_id, capture_id, extractor, "
            "extractor_version, status, post_text, title, canonical_url, metadata_json, "
            "issues_json, schema_version, created_at, source_id, normalized_url, provenance_json, "
            "error_code) VALUES (?, ?, 'generic_metadata', '0.1.0', 'success', ?, ?, ?, '{}', "
            "'[]', '1', ?, ?, ?, ?, NULL)",
            (
                extraction_id,
                capture_id,
                "Evidence from a real SQLite fixture",
                "Fixture",
                url,
                timestamp,
                source_id,
                url,
                json.dumps(provenance),
            ),
        )
        connection.commit()

        try:
            envelope = export_capture(
                repository,
                evidence_root,
                capture_id,
                producer_revision="integration-test",
            )
        finally:
            repository.close()

        assert envelope["capture_id"] == capture_id
        assert envelope["capture_status"] == "success"
        assert envelope["artifacts"][0]["sha256"] == artifact_hash
        assert envelope["extraction"]["provenance"]["post_text"]["artifact_sha256"] == artifact_hash
        assert envelope["extraction"]["error_code"] is None
