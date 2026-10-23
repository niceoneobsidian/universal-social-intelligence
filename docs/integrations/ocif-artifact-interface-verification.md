# USIE Capture and Artifact Interface Verification

**Baseline inspected:** `main` at `75fe1c23545c05c7d0894a3e6550f22f7cd2348e`  
**Scope:** source/schema inspection; not a runtime integration test  
**Conclusion:** The inspected baseline has canonical SQLite capture/artifact records and typed extraction results, but no stable, documented OCIF export API. A candidate read-only exporter is proposed separately in [PR #6](https://github.com/niceoneobsidian/universal-social-intelligence/pull/6); it must not be described as merged or production-approved until its final head passes the required checks and both repository owners approve the contract.

## Files inspected

- `src/usie/models.py`: `Status`, `ArtifactKind`, `ArtifactDescriptor`, `ExtractionProvenance`, `ExtractionResult`, `Observation`.
- `src/usie/db/repository.py`: `SqliteRepository`, transactional writes, source/job/capture/artifact/extraction/audit operations.
- `src/usie/collector.py`: capture orchestration and status classification.
- `src/usie/extraction/__init__.py`: `Extractor`, `ExtractionRequest`, `OpenGraphAdapter`.
- `src/usie/evidence.py`: artifact path resolution and hash/size verification.
- `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `README.md`, `pyproject.toml`.

## Verified producer-side structures

### Capture outcome

The canonical `Status` enum contains `queued`, `running`, `success`, `partial`, `blocked`, `unavailable`, `failed`, and `cancelled`. Terminal statuses are success, partial, blocked, unavailable, failed, and cancelled.

Do not use `social_evidence` for contract export when distinctions matter. It is a read-only compatibility view over the latest capture and its `status_label` is lossy: blocked, unavailable, and failed map to `inaccessible`. Export canonical capture history instead.

### Artifact descriptor

The typed `ArtifactDescriptor` fields are:

- `artifact_id`
- `kind` (`ArtifactKind`)
- `relative_path`
- `sha256`
- `byte_size`
- `media_type`
- `description`

Observed artifact kinds include `rendered_dom_html`, `response_body`, `screenshot_viewport`, `screenshot_full_page`, `navigation_record`, and `capture_manifest`. The inspected enum does not define a PDF-specific artifact kind. Do not infer PDF acquisition from the ability to represent arbitrary bytes.

Artifact paths are producer-owned references. Never export absolute local paths. Resolve relative paths under the configured evidence root and verify the actual bytes, hash, and size before export and again at the consumer boundary. Path checks must account for traversal and symlinks; a string-prefix check alone is insufficient.

### Extraction result

The typed `ExtractionResult` includes `extraction_id`, `capture_id`, `source_id`, `normalized_url`, `extractor`, `extractor_version`, `status`, optional post text/author/title/canonical URL/metadata, field-level `provenance`, error code, issues, schema version, and creation timestamp.

The model requires provenance for each populated extracted field. Success requires non-empty validated `post_text`; an extraction with an error cannot be successful. Extraction status is constrained to success/partial and is not the same as capture status. A consumer/exporter must validate persisted rows too; database rows should not be assumed valid merely because the typed model normally validates writes.

### Collector and persistence boundary

`Collector` receives an injected `SqliteRepository`, capturer, settings, and optional extractor. Its default extractor is `OpenGraphAdapter`. The generic metadata adapter reads locally saved DOM evidence and does not provide platform-specific API extraction.

The repository provides transactional operations for runs/jobs, sources, captures, artifact descriptors, extraction results, and audit events. Duplicate jobs are idempotent per run/source; a recapture is a new capture. Recovery marks unfinished captures failed/interrupted rather than deleting evidence.

## Integration decision

1. Do not invent a public endpoint, SDK, or manifest format. The inspected baseline did not contain a stable producer export API.
2. Review the candidate versioned exporter in [PR #6](https://github.com/niceoneobsidian/universal-social-intelligence/pull/6) separately from this source-verification document.
3. Keep the contract versioned and explicitly internal until both USIE and OCIF owners approve the exact schema and semantics.
4. Preserve source/capture identity, canonical capture status, extraction status, artifact descriptor, field-level provenance, and producer revision.
5. Resolve artifact references only within a configured trusted root; verify hash and byte size at both producer and consumer boundaries.
6. If PDFs are required, first implement and test a documented PDF acquisition path. Keep acquisition, extraction, and downstream processing statuses separate.
7. Contract tests must cover all capture statuses, invalid extraction combinations, missing/tampered artifacts, path traversal and symlink escape, duplicate import/replay, recapture identity, malformed stored JSON, and secret redaction.

## Verification status and limitations

This document is source-verified against the listed baseline only. It does not claim that this documentation change ran USIE tests, queried a live database, or proved end-to-end OCIF ingestion. The candidate exporter and its tests are a separate change; their latest CI result must be checked after any new commits. No claim is made that PDF acquisition currently exists. Contract approval and production readiness remain outstanding until explicit owner review and the shared acceptance gates pass.
