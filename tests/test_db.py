from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from usie.db import MIGRATIONS, Migration, SchemaError, SqliteRepository, apply_migrations
from usie.db.migrations import current_version

EXPECTED_TABLES = {
    "runs",
    "jobs",
    "sources",
    "captures",
    "artifacts",
    "extraction_results",
    "media_references",
    "documents",
    "document_pages",
    "structured_fields",
    "validation_results",
    "review_tasks",
    "derived_analyses",
    "audit_events",
    "schema_migrations",
}


def raw(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    return c


def test_fresh_database_has_all_tables_and_view(repo: SqliteRepository) -> None:
    names = {r["name"] for r in repo.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names
    views = {r["name"] for r in repo.query("SELECT name FROM sqlite_master WHERE type='view'")}
    assert views == {"social_evidence"}
    assert current_version(repo._conn) == MIGRATIONS[-1].version


def test_reopen_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "a.sqlite3"
    SqliteRepository(p).close()
    r = SqliteRepository(p)
    assert r.query("SELECT COUNT(*) AS n FROM schema_migrations")[0]["n"] == len(MIGRATIONS)
    r.close()


def test_failed_migration_rolls_back_completely(tmp_path: Path) -> None:
    conn = raw(tmp_path / "m.sqlite3")
    sql = "CREATE TABLE ok_table(x); CREATE TABLE ok_table(y);"
    bad = (MIGRATIONS[0], Migration(2, "bad", sql))
    with pytest.raises(SchemaError):
        apply_migrations(conn, bad)
    assert current_version(conn) == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables
    assert not conn.in_transaction


def test_newer_database_is_refused(tmp_path: Path) -> None:
    conn = raw(tmp_path / "n.sqlite3")
    apply_migrations(conn)
    conn.execute("INSERT INTO schema_migrations VALUES (99,'future','x')")
    with pytest.raises(SchemaError, match="newer"):
        apply_migrations(conn)


def test_upgrade_path_preserves_existing_data(tmp_path: Path) -> None:
    conn = raw(tmp_path / "u.sqlite3")
    apply_migrations(conn, MIGRATIONS[:1])
    conn.execute(
        "INSERT INTO sources VALUES ('s1','https://a.test/','https://a.test/','UNIVERSAL','t')"
    )
    assert apply_migrations(conn) == [2, 3, 4]
    assert conn.execute("SELECT source_url FROM sources").fetchone()[0] == "https://a.test/"


def test_constraints(repo: SqliteRepository) -> None:
    c = repo._conn
    with pytest.raises(sqlite3.IntegrityError):  # FK
        c.execute(
            "INSERT INTO jobs(job_id,run_id,source_id,submitted_url,status,created_at) "
            "VALUES('j','nope','nope','u','queued','t')"
        )
    with pytest.raises(sqlite3.IntegrityError):  # status CHECK
        c.execute("INSERT INTO runs(run_id,started_at,status) VALUES('r','t','inaccessible')")
    with pytest.raises(sqlite3.IntegrityError):  # sha length
        c.execute("INSERT INTO runs(run_id,started_at,status) VALUES('r','t','running')")
        c.execute("INSERT INTO sources VALUES('s','u','n','P','t')")
        c.execute(
            "INSERT INTO jobs(job_id,run_id,source_id,submitted_url,status,created_at) "
            "VALUES('j','r','s','u','queued','t')"
        )
        c.execute(
            "INSERT INTO captures(capture_id,job_id,run_id,source_id,attempt,captured_at,"
            "status,submitted_url) VALUES('c','j','r','s',1,'t','running','u')"
        )
        c.execute("INSERT INTO artifacts VALUES('a','c','k','p','short',1,'m','d','t')")


def test_audit_events_are_append_only(repo: SqliteRepository) -> None:
    repo.audit("manual_correction", actor="operator", details={"x": 1})
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repo._conn.execute("UPDATE audit_events SET action='tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repo._conn.execute("DELETE FROM audit_events")


def test_database_file_permissions(repo: SqliteRepository) -> None:
    assert repo.path.stat().st_mode & 0o077 == 0
