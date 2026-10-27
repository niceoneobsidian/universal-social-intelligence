# OCIF Evidence Export Adapter

**Status:** Candidate read-only exporter and producer tests are present on this pull-request branch. The latest checks must be rerun against the final branch head before this change is considered validated.  
**Wire contract:** `ocif.usie-import` version `0.1.0` is a candidate for internal cross-repository integration testing only. It is not a stable public API or production certification.

## Source of truth

The adapter reads canonical `captures`, `sources`, `artifacts`, and `extraction_results` rows through `SqliteRepository.query()`. It does not use the lossy `social_evidence` view and never mutates capture history.

For each finalized capture it emits:
- stable source and capture IDs;
- submitted and normalized URLs;
- exact capture status and status reason;
- capture start/finish timestamps;
- artifact IDs, kinds, relative paths, hashes, byte sizes, and media types;
- the latest extraction record, extractor/schema versions, field-level provenance, and extraction error code;
- platform classification, producer revision, and warnings.

The caller must supply `producer_revision`; the adapter does not guess a Git revision from runtime state. It does not export cookies, authorization headers, browser storage state, raw HTML, or secret material.

## Integrity and semantic checks

Before emitting an artifact reference, the adapter calls `verify_artifact()` against the configured evidence root. Missing files, byte-size mismatch, hash mismatch, traversal paths, and provenance references to missing or different artifacts cause export rejection.

The exporter validates persisted extraction rows rather than assuming every row is valid because the normal write path uses Pydantic. It rejects unsupported extraction statuses, malformed JSON or issue shapes, populated fields without provenance, provenance pointing to unverified artifacts, and a successful extraction with blank/missing post text or an error code. A capture marked `success` is rejected if its extraction is missing or not a validated success. Capture status and extraction status remain separate.

Non-terminal captures are not exportable. The exporter is read-only and introduces no public HTTP endpoint.

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

The caller is responsible for serializing the envelope to a reviewed, access-controlled transport. No public HTTP endpoint or automatic cross-repository write is introduced here.

## Contract decision

`0.1.0` remains a candidate for internal integration testing. USIE owns canonical capture history and artifact bytes; OCIF owns imported observations and retrieval. Neither repository should treat this candidate as stable or production-approved until the final reviewed PR heads pass the shared acceptance gates and both owners explicitly approve the contract.

## Producer acceptance gates

1. Run formatting, lint, typing, and the complete USIE test suite on the final head.
2. Run a disposable real-SQLite integration test with an artifact directory.
3. Cover valid provenance, missing populated-field provenance, unsupported extraction statuses, invalid error/status combinations, malformed issue JSON, missing/tampered artifacts, and non-terminal captures.
4. Compare the emitted JSON with shared OCIF fixtures for success, partial, blocked, unavailable, failed, and cancelled capture outcomes.
5. Confirm duplicate/replay behavior and producer revision semantics.
6. Approve the candidate contract with OCIF owners before promising compatibility.

## Consumer-side artifact verification

The producer-side hash is necessary but not sufficient: OCIF must verify bytes again at import. For artifact-bearing envelopes, configure the PostgreSQL consumer with the trusted artifact root containing the exported files. The consumer resolves each manifest path under that root, rejects traversal/symlink escapes or missing files, checks byte size, and recalculates SHA-256 before persisting the observation. An artifact-bearing envelope without an artifact root must be rejected or quarantined; envelopes with no artifacts do not require a file root.

Replay tests must replay the unchanged envelope against the same PostgreSQL database and artifact root. A mutated artifact must fail integrity verification even if the envelope is unchanged. A changed envelope under an existing source/capture idempotency key must not overwrite the accepted observation.
