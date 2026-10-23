"""Read-only, provenance-checked export from canonical USIE records to OCIF.

This adapter does not add an HTTP endpoint and does not mutate USIE capture history.
The caller supplies the deployed producer revision and the configured evidence root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from usie.db.repository import SqliteRepository
from usie.evidence import verify_artifact

CONTRACT_VERSION = "0.1.0"
TERMINAL_STATUSES = frozenset(
    {"success", "partial", "blocked", "unavailable", "failed", "cancelled"}
)


class OCIFExportError(ValueError):
    """A capture cannot be exported as a trustworthy OCIF evidence envelope."""


def _decode_json(value: str | None, field: str, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OCIFExportError(f"stored {field} is not valid JSON") from exc


def export_capture(
    repository: SqliteRepository,
    evidence_root: Path | str,
    capture_id: str,
    *,
    producer_revision: str,
) -> dict[str, Any]:
    """Build one sanitized OCIF envelope from canonical capture tables.

    Export only finalized captures. Artifact files are re-hashed before their references
    are released. Cookies, headers, raw HTML, and browser storage state are never exported.
    """
    if not producer_revision or not producer_revision.strip():
        raise OCIFExportError("producer_revision is required")
    captures = repository.query(
        """SELECT c.capture_id, c.source_id, c.status, c.status_reason, c.captured_at,
                  c.finished_at, c.submitted_url, c.issues_json, c.error_code,
                  s.normalized_url, s.platform
           FROM captures c JOIN sources s ON s.source_id=c.source_id
           WHERE c.capture_id=?""",
        (capture_id,),
    )
    if not captures:
        raise OCIFExportError(f"unknown capture_id: {capture_id}")
    capture = captures[0]
    status = str(capture["status"])
    if status not in TERMINAL_STATUSES:
        raise OCIFExportError(f"capture is not finalized: {status}")
    artifacts = repository.query(
        """SELECT artifact_id, kind, relative_path, sha256, byte_size, media_type
           FROM artifacts WHERE capture_id=? ORDER BY artifact_id""",
        (capture_id,),
    )
    root = Path(evidence_root).expanduser().resolve()
    artifact_envelopes: list[dict[str, Any]] = []
    artifact_hashes: dict[str, str] = {}
    for artifact in artifacts:
        problem = verify_artifact(
            root,
            str(artifact["relative_path"]),
            str(artifact["sha256"]),
            int(artifact["byte_size"]),
        )
        if problem is not None:
            raise OCIFExportError(
                f"artifact {artifact['artifact_id']} failed verification: {problem}"
            )
        artifact_hashes[str(artifact["artifact_id"])] = str(artifact["sha256"])
        artifact_envelopes.append(
            {
                "artifact_id": str(artifact["artifact_id"]),
                "kind": str(artifact["kind"]),
                "location": str(artifact["relative_path"]),
                "sha256": str(artifact["sha256"]),
                "byte_size": int(artifact["byte_size"]),
                "media_type": str(artifact["media_type"]),
            }
        )
    extraction_rows = repository.query(
        """SELECT extraction_id, extractor, extractor_version, status, post_text, author,
                  title, canonical_url, metadata_json, issues_json, schema_version,
                  provenance_json
           FROM extraction_results WHERE capture_id=?
           ORDER BY created_at DESC, rowid DESC LIMIT 1""",
        (capture_id,),
    )
    extraction: dict[str, Any] | None = None
    warnings = _decode_json(capture.get("issues_json"), "capture issues", [])
    if not isinstance(warnings, list):
        warnings = []
    if extraction_rows:
        row = extraction_rows[0]
        provenance = _decode_json(row.get("provenance_json"), "extraction provenance", {})
        metadata = _decode_json(row.get("metadata_json"), "extraction metadata", {})
        extraction_issues = _decode_json(row.get("issues_json"), "extraction issues", [])
        if not isinstance(provenance, dict) or not isinstance(metadata, dict):
            raise OCIFExportError("stored extraction metadata/provenance has the wrong shape")
        for field, ref in provenance.items():
            if not isinstance(ref, dict):
                raise OCIFExportError(f"invalid provenance entry for field {field}")
            artifact_id = ref.get("artifact_id")
            artifact_sha = ref.get("artifact_sha256")
            if not isinstance(artifact_id, str) or not isinstance(artifact_sha, str):
                raise OCIFExportError(f"invalid artifact identity for provenance field {field}")
            if artifact_id not in artifact_hashes or artifact_hashes[artifact_id] != artifact_sha:
                raise OCIFExportError(
                    f"provenance for {field} references missing or changed artifact"
                )
        extraction = {
            "extraction_id": str(row["extraction_id"]),
            "extractor": str(row["extractor"]),
            "extractor_version": str(row.get("extractor_version") or "unknown"),
            "schema_version": str(row["schema_version"]),
            "status": str(row["status"]),
            "post_text": row.get("post_text"),
            "author": row.get("author"),
            "title": row.get("title"),
            "canonical_url": row.get("canonical_url"),
            "metadata": metadata,
            "provenance": provenance,
        }
        if isinstance(extraction_issues, list):
            warnings.extend(str(item) for item in extraction_issues)
    elif status == "success":
        raise OCIFExportError("successful capture has no extraction result")
    if status == "success" and (
        extraction is None
        or extraction["status"] != "success"
        or not extraction.get("post_text")
        or not extraction.get("provenance")
    ):
        raise OCIFExportError("success requires validated non-empty extraction and provenance")
    return {
        "contract_version": CONTRACT_VERSION,
        "source_id": str(capture["source_id"]),
        "submitted_url": str(capture["submitted_url"]),
        "normalized_url": str(capture["normalized_url"]),
        "capture_id": str(capture["capture_id"]),
        "capture_status": status,
        "status_reason": capture.get("status_reason"),
        "error_code": capture.get("error_code"),
        "captured_at": str(capture["captured_at"]),
        "completed_at": capture.get("finished_at"),
        "artifacts": artifact_envelopes,
        "extraction": extraction,
        "platform": str(capture["platform"]),
        "producer_revision": producer_revision.strip(),
        "warnings": warnings,
    }
