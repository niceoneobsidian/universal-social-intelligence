# OCIF Evidence Export Adapter

**Status:** Read-only adapter and producer tests are implemented in the integration branch. Producer CI passes on the current branch head.  
**Wire contract:** `ocif.usie-import` version `0.1.0` is approved for internal cross-repository integration testing; it is not a stable public API or production certification.

## Source of truth

The adapter reads canonical `captures`, `sources`, `artifacts`, and `extraction_results` rows through `SqliteRepository.query()`. It does not use the lossy `social_evidence` view and never mutates capture history.

For each finalized capture it emits:
- stable source and capture IDs;
- submitted and normalized URLs;
- exact capture status and status reason;
- capture start/finish timestamps;
- artifact IDs, kinds, relative paths, hashes, byte sizes and media types;
- the latest extraction record, extractor/schema versions and field-level provenance;
- platform classification, producer revision and warnings.

The caller must supply `producer_revision`; the adapter does not guess a Git revision from runtime state. It does not export cookies, authorization headers, browser storage state, raw HTML, or secret material.

## Integrity checks

Before emitting an artifact reference, the adapter calls `verify_artifact()` against the configured evidence root. Missing files, byte-size mismatch, hash mismatch, traversal paths, and extraction provenance that refers to a missing or different artifact cause export rejection. A capture marked `success` is rejected if its extraction is missing, non-successful, empty or lacks provenance. Non-terminal captures are not exportable.

## Example

```python
from pathlib import Path
from usie.db.repository import SqliteRepository
from usie.integrations.ocif import export_capture

repo = SqliteRepository(Path.home() / ".usie" / "usie.sqlite3")
try:
    envelope = export_capture(
        repo,
        Path.home() / ".usie" / "evidence",
        capture_id="REPLACE_WITH_CAPTURE_UUID",
        producer_revision="REPLACE_WITH_VERIFIED_GIT_SHA",
    )
finally:
    repo.close()
```

The caller is responsible for serializing this envelope to a reviewed, access-controlled transport. No public HTTP endpoint or automatic cross-repository write is introduced here.

## Contract decision

USIE and OCIF accept the `0.1.0` envelope for internal implementation and integration testing. This decision preserves USIE ownership of canonical capture history and artifact bytes and OCIF ownership of imported observations and retrieval. The adapter is read-only and emits no public endpoint. The contract must not be described as stable or production-approved until the shared acceptance gates pass on the final reviewed PR heads.

## Acceptance still required

1. Run `pytest`, `ruff check src tests`, and `mypy`.
2. Add a disposable real-database test using a real USIE capture and artifact directory.
3. Compare the emitted JSON with OCIF contract fixtures for success, partial, blocked, unavailable, failed and cancelled outcomes.
4. Test malformed database JSON, missing artifacts, hash mismatch and provenance mismatch.
5. Approve the candidate contract with OCIF owners before promising compatibility.
