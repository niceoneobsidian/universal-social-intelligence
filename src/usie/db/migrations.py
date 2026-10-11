"""Versioned, transactional, forward-only schema migrations.

Rules: never edit a shipped migration; add a new one. Never rename/drop columns of shipped
tables without a documented copy-forward migration. Each migration runs in one transaction
together with its schema_migrations row, so a failure leaves the database unchanged.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

STATUS_SQL = "('queued','running','success','partial','blocked','unavailable','failed','cancelled')"


class SchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


_V1 = f"""
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN {STATUS_SQL}),
  input_count INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL DEFAULT '{{}}',
  tool_versions_json TEXT NOT NULL DEFAULT '{{}}',
  summary_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE TABLE sources (
  source_id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL,
  normalized_url TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);
CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  submitted_url TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN {STATUS_SQL}),
  attempts INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  status_reason TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (run_id, source_id)
);
CREATE TABLE captures (
  capture_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  attempt INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN {STATUS_SQL}),
  status_reason TEXT,
  error_code TEXT,
  submitted_url TEXT NOT NULL,
  final_url TEXT,
  http_status INTEGER,
  page_title TEXT,
  redirect_chain_json TEXT NOT NULL DEFAULT '[]',
  completeness_json TEXT NOT NULL DEFAULT '{{}}',
  tool_versions_json TEXT NOT NULL DEFAULT '{{}}',
  issues_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (job_id, attempt)
);
CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL REFERENCES captures(capture_id),
  kind TEXT NOT NULL,
  relative_path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
  media_type TEXT NOT NULL,
  description TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (capture_id, kind)
);
CREATE TABLE extraction_results (
  extraction_id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL REFERENCES captures(capture_id),
  extractor TEXT NOT NULL,
  extractor_version TEXT,
  status TEXT NOT NULL CHECK (status IN {STATUS_SQL}),
  post_text TEXT,
  author TEXT,
  title TEXT,
  canonical_url TEXT,
  media_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{{}}',
  issues_json TEXT NOT NULL DEFAULT '[]',
  schema_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE media_references (
  media_ref_id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL REFERENCES captures(capture_id),
  extraction_id TEXT REFERENCES extraction_results(extraction_id),
  media_url TEXT NOT NULL,
  media_type TEXT,
  local_artifact_id TEXT REFERENCES artifacts(artifact_id)
);
CREATE TABLE validation_results (
  validation_id TEXT PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  validator TEXT NOT NULL,
  schema_version TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('pass','warn','fail')),
  details_json TEXT NOT NULL DEFAULT '{{}}',
  created_at TEXT NOT NULL
);
CREATE TABLE audit_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  subject_type TEXT,
  subject_id TEXT,
  details_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE INDEX idx_jobs_run ON jobs(run_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_captures_source ON captures(source_id, captured_at);
CREATE INDEX idx_captures_status ON captures(status);
CREATE INDEX idx_artifacts_capture ON artifacts(capture_id);
CREATE INDEX idx_extractions_capture ON extraction_results(capture_id);
CREATE INDEX idx_validation_subject ON validation_results(subject_type, subject_id);
CREATE INDEX idx_audit_subject ON audit_events(subject_type, subject_id);
"""

# Tables used from P2/P3 onward; created now so the schema is versioned as a whole.
_V2 = """
CREATE TABLE documents (
  document_id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE CHECK (length(sha256) = 64),
  original_filename TEXT,
  relative_path TEXT NOT NULL UNIQUE,
  media_type TEXT,
  recognized_format TEXT,
  byte_size INTEGER NOT NULL,
  ingested_at TEXT NOT NULL
);
CREATE TABLE document_pages (
  page_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(document_id),
  extraction_id TEXT NOT NULL REFERENCES extraction_results(extraction_id),
  page_number INTEGER NOT NULL CHECK (page_number >= 1),
  text TEXT,
  layout_json TEXT,
  UNIQUE (extraction_id, page_number)
);
CREATE TABLE structured_fields (
  field_id TEXT PRIMARY KEY,
  extraction_id TEXT NOT NULL REFERENCES extraction_results(extraction_id),
  field_name TEXT NOT NULL,
  value_json TEXT,
  value_state TEXT NOT NULL CHECK (value_state IN ('present','missing','ambiguous','conflicting')),
  evidence_sha256 TEXT,
  source_span_json TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT,
  validation_outcome TEXT CHECK (validation_outcome IN ('pass','warn','fail')),
  review_state TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (review_state IN ('unreviewed','confirmed','corrected','rejected')),
  created_at TEXT NOT NULL
);
CREATE TABLE review_tasks (
  task_id TEXT PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','resolved','dismissed')),
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  resolution_json TEXT
);
CREATE TABLE derived_analyses (
  analysis_id TEXT PRIMARY KEY,
  capture_id TEXT REFERENCES captures(capture_id),
  document_id TEXT REFERENCES documents(document_id),
  kind TEXT NOT NULL,
  model TEXT,
  model_version TEXT,
  input_sha256s_json TEXT NOT NULL DEFAULT '[]',
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK (capture_id IS NOT NULL OR document_id IS NOT NULL)
);
CREATE INDEX idx_pages_document ON document_pages(document_id);
CREATE INDEX idx_fields_extraction ON structured_fields(extraction_id);
CREATE INDEX idx_review_state ON review_tasks(state);
"""

# Read-only compatibility view for the legacy `social_evidence` table described in the project
# context. The legacy table was one row per URL with a 3-value status; the real schema keeps
# every observation. The view exposes the *latest* capture per source under the legacy column
# names. Mapping is lossy by design: blocked/unavailable/failed all become 'inaccessible'.
_V3 = """
CREATE VIEW social_evidence AS
SELECT
  s.source_id AS id,
  s.source_url AS source_url,
  s.platform AS platform,
  e.author AS author,
  c.captured_at AS captured_at,
  CASE c.status WHEN 'success' THEN 'success' WHEN 'partial' THEN 'partial'
       ELSE 'inaccessible' END AS status_label,
  e.post_text AS post_text,
  COALESCE(e.metadata_json, '{}') AS raw_metadata_json
FROM sources s
JOIN captures c ON c.capture_id = (
  SELECT c2.capture_id FROM captures c2
  WHERE c2.source_id = s.source_id
    AND c2.status IN ('success','partial','blocked','unavailable','failed')
  ORDER BY c2.captured_at DESC, c2.rowid DESC LIMIT 1)
LEFT JOIN extraction_results e ON e.extraction_id = (
  SELECT e2.extraction_id FROM extraction_results e2
  WHERE e2.capture_id = c.capture_id ORDER BY e2.created_at DESC, e2.rowid DESC LIMIT 1);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "core_evidence_schema", _V1),
    Migration(2, "documents_and_structured_fields", _V2),
    Migration(3, "legacy_social_evidence_view", _V3),
)


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def apply_migrations(
    conn: sqlite3.Connection, migrations: tuple[Migration, ...] = MIGRATIONS
) -> list[int]:
    """Apply pending migrations. `conn` must be in autocommit mode (isolation_level=None)."""
    if conn.isolation_level is not None:
        raise SchemaError("connection must use isolation_level=None")
    version = current_version(conn)
    latest = max((m.version for m in migrations), default=0)
    if version > latest:
        raise SchemaError(f"database schema v{version} is newer than this code (v{latest})")
    applied: list[int] = []
    for migration in sorted(migrations, key=lambda m: m.version):
        if migration.version <= version:
            continue
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{migration.sql}\n"
            "INSERT INTO schema_migrations(version, name, applied_at) "
            f"VALUES ({int(migration.version)}, '{migration.name}', "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise SchemaError(f"migration {migration.version} failed: {exc}") from exc
        applied.append(migration.version)
    return applied
