"""Read-only export of finalized USIE captures to the OCIF evidence contract."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from usie.evidence import verify_artifact
from usie.models import Status

CONTRACT_ID = "ocif.usie-import"
CONTRACT_VERSION = "0.1.0-draft"
_FINAL_STATUSES = {
    Status.SUCCESS.value,
    Status.PARTIAL.value,
    Status.BLOCKED.value,
    Status.UNAVAILABLE.value,
    Status.FAILED.value,
    Status.CANCELLED.value,
}
_SECRET_QUERY_KEYS = re.compile(
    r"(?:^|[_-])(access[_-]?token|auth(?:orization)?|client[_-]?secret|"
    r"api[_-]?key|token|secret|password|passwd|session|signature|sig|code)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"\b(?:bearer\s+)[A-Za-z0-9._~+/-]+=*|"
    r"(?:password|passwd|client_secret|api_key|access_token|refresh_token)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


class ExportError(RuntimeError):
    """Raised when evidence cannot safely be exported."""


def redact_url(value: str | None) -> str | None:
    """Remove URL credentials, fragments and likely secret-bearing query parameters."""
    if value is None:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("not an absolute HTTP(S) URL")
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parts.port
        netloc = f"[{host}]" if ":" in host else host
        if port is not None and port != {"http": 80, "https": 443}[parts.scheme]:
            netloc = f"{netloc}:{port}"
        safe_query = [
            (key, "[REDACTED]" if _SECRET_QUERY_KEYS.search(key) else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (parts.scheme.lower(), netloc, parts.path or "/", urlencode(safe_query), "")
        )
    except (UnicodeError, ValueError) as exc:
        raise ExportError("stored URL is malformed; refusing to export it") from exc


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _SENSITIVE_TEXT.sub("[REDACTED]", value)


def _load_json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExportError("stored evidence contains malformed JSON") from exc


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise ExportError(f"USIE database does not exist: {db_path}")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error as exc:
        raise ExportError("could not open USIE database read-only") from exc


def export_envelopes(
    db_path: Path,
    evidence_root: Path,
    *,
    capture_id: str | None = None,
    revision: str | None = None,
) -> list[dict[str, Any]]:
    """Export finalized captures without running migrations or writing to USIE storage.

    A single invalid selected capture fails the export; it is never silently skipped.
    """
    conn = _connect_readonly(db_path)
    try:
        params: tuple[Any, ...] = ()
        where = ""
        if capture_id:
            where = "WHERE c.capture_id = ?"
            params = (capture_id,)
        captures = conn.execute(
            "SELECT c.capture_id, c.source_id, c.status, c.status_reason, c.error_code, "
            "c.submitted_url, c.captured_at, c.finished_at, c.issues_json, "
            "s.normalized_url, s.platform "
            "FROM captures c JOIN sources s ON s.source_id=c.source_id "
            f"{where} ORDER BY c.captured_at, c.capture_id",
            params,
        ).fetchall()
        if capture_id and not captures:
            raise ExportError(f"capture not found: {capture_id}")
        envelopes: list[dict[str, Any]] = []
        for cap in captures:
            status = str(cap["status"])
            if status not in _FINAL_STATUSES:
                if capture_id:
                    raise ExportError(f"capture {capture_id} is not finalized")
                continue
            artifact_rows = conn.execute(
                "SELECT artifact_id, kind, relative_path, sha256, byte_size, media_type "
                "FROM artifacts WHERE capture_id=? ORDER BY artifact_id",
                (cap["capture_id"],),
            ).fetchall()
            artifacts: list[dict[str, Any]] = []
            artifact_ids: set[str] = set()
            for row in artifact_rows:
                issue = verify_artifact(
                    evidence_root,
                    str(row["relative_path"]),
                    str(row["sha256"]),
                    int(row["byte_size"]),
                )
                if issue:
                    raise ExportError(
                        f"capture {cap['capture_id']} artifact {row['artifact_id']} failed "
                        f"integrity check: {issue}"
                    )
                artifact_id = str(row["artifact_id"])
                artifact_ids.add(artifact_id)
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "kind": str(row["kind"]),
                        "sha256": str(row["sha256"]),
                        "byte_size": int(row["byte_size"]),
                        "media_type": str(row["media_type"])
                        if row["media_type"] is not None
                        else None,
                        "location": str(row["relative_path"]),
                    }
                )

            extraction_row = conn.execute(
                "SELECT extraction_id, extractor, extractor_version, status, post_text, author, "
                "title, canonical_url, metadata_json, issues_json, schema_version, provenance_json, "
                "error_code FROM extraction_results WHERE capture_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cap["capture_id"],),
            ).fetchone()
            extraction: dict[str, Any] | None = None
            if extraction_row:
                extraction_status = str(extraction_row["status"])
                if extraction_status not in {"success", "partial", "failed"}:
                    raise ExportError(
                        f"capture {cap['capture_id']} has unsupported extraction status "
                        f"{extraction_status!r}"
                    )
                provenance = _load_json(extraction_row["provenance_json"], {})
                if not isinstance(provenance, dict):
                    raise ExportError("extraction provenance must be a JSON object")
                evidence_refs: list[str] = []
                for field_provenance in provenance.values():
                    if not isinstance(field_provenance, dict):
                        raise ExportError("malformed extraction provenance record")
                    artifact_ref = field_provenance.get("artifact_id")
                    artifact_hash = field_provenance.get("artifact_sha256")
                    if artifact_ref not in artifact_ids:
                        raise ExportError(
                            f"extraction references missing artifact {artifact_ref!r}"
                        )
                    matching = next(a for a in artifacts if a["artifact_id"] == artifact_ref)
                    if artifact_hash != matching["sha256"]:
                        raise ExportError("extraction provenance hash does not match artifact")
                    if artifact_ref not in evidence_refs:
                        evidence_refs.append(artifact_ref)
                fields: dict[str, Any] = {}
                for field_name in ("post_text", "author", "title"):
                    value = extraction_row[field_name]
                    if value is not None:
                        fields[field_name] = _safe_text(str(value))
                canonical_url = extraction_row["canonical_url"]
                if canonical_url is not None:
                    fields["canonical_url"] = redact_url(str(canonical_url))
                metadata = _load_json(extraction_row["metadata_json"], {})
                if not isinstance(metadata, dict):
                    raise ExportError("extraction metadata must be a JSON object")
                if metadata:
                    fields["metadata"] = {str(k): _safe_text(str(v)) for k, v in metadata.items()}
                extraction = {
                    "status": extraction_status,
                    "adapter": str(extraction_row["extractor"]),
                    "adapter_version": extraction_row["extractor_version"],
                    "schema_version": str(extraction_row["schema_version"]),
                    "fields": fields,
                    "evidence_refs": evidence_refs,
                }
                extraction_issues = _load_json(extraction_row["issues_json"], [])
                if not isinstance(extraction_issues, list):
                    raise ExportError("extraction issues must be a JSON array")
            else:
                extraction_issues = []

            if status == Status.SUCCESS.value:
                if extraction is None or extraction["status"] != Status.SUCCESS.value:
                    raise ExportError(
                        f"capture {cap['capture_id']} is marked success without successful extraction"
                    )
                if not artifacts or not extraction["evidence_refs"]:
                    raise ExportError(
                        f"capture {cap['capture_id']} success lacks verified artifact provenance"
                    )

            capture_issues = _load_json(cap["issues_json"], [])
            if not isinstance(capture_issues, list):
                raise ExportError("capture issues must be a JSON array")
            warnings = [
                _safe_text(str(item)) or "" for item in [*capture_issues, *extraction_issues]
            ]
            envelopes.append(
                {
                    "contract_id": CONTRACT_ID,
                    "contract_version": CONTRACT_VERSION,
                    "producer": {
                        "name": "USIE",
                        "revision": revision or os.environ.get("USIE_BUILD_REVISION", "unknown"),
                    },
                    "source": {
                        "source_id": str(cap["source_id"]),
                        "submitted_url": redact_url(str(cap["submitted_url"])),
                        "normalized_url": redact_url(str(cap["normalized_url"])),
                        "platform": str(cap["platform"]) if cap["platform"] is not None else None,
                    },
                    "capture": {
                        "capture_id": str(cap["capture_id"]),
                        "status": status,
                        "status_reason": _safe_text(cap["status_reason"]),
                        "error_code": _safe_text(cap["error_code"]),
                        "captured_at": str(cap["captured_at"]) if cap["captured_at"] else None,
                        "completed_at": str(cap["finished_at"]) if cap["finished_at"] else None,
                    },
                    "artifacts": artifacts,
                    "extraction": extraction,
                    "warnings": warnings,
                }
            )
        return envelopes
    except sqlite3.Error as exc:
        raise ExportError("could not read USIE evidence records") from exc
    finally:
        conn.close()


def write_export(path: Path, envelopes: list[dict[str, Any]]) -> None:
    """Create a new export file with owner-only permissions; never overwrite an existing file."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(envelopes, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
