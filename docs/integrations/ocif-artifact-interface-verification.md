# USIE Capture and Artifact Interface Verification

**Verified against:** `main` source files retrieved on 2026-10-23  
**Scope:** code/schema verification, not a runtime integration test  
**Conclusion:** USIE has a canonical SQLite capture/artifact model and typed extraction results. It does not currently expose a documented stable OCIF JSON export API in the inspected source.

## Files inspected

- `src/usie/models.py`: `Status`, `ArtifactKind`, `ArtifactDescriptor`, `ExtractionProvenance`, `ExtractionResult`, `Observation`.
- `src/usie/db/repository.py`: `SqliteRepository`, transactional writes, source/job/capture/artifact/extraction/audit operations.
- `src/usie/collector.py`: `Collector` composes validation, capture, evidence persistence, extraction and status classification.
- `src/usie/extraction/__init__.py`: `Extractor`, `ExtractionRequest`, `OpenGraphAdapter`.
- `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `README.md`, `pyproject.toml`.

## Verified producer-side structures

### Capture outcome

The canonical `Status` enum contains `queued`, `running`, `success`, `partial`, `blocked`, `unavailable`, `failed`, and `cancelled`. Terminal statuses are success, partial, blocked, unavailable, failed, and cancelled.

Do not use `social_evidence` for contract export when the distinctions matter. It is a read-only compatibility VIEW over the latest capture. Its `status_label` is lossy: blocked/unavailable/failed map to `inaccessible`. Canonical capture history is append-only, except for the documented one-time finalization transition.

### Artifact descriptor

The typed `ArtifactDescriptor` fields are:

- `artifact_id`
- `kind` (the `ArtifactKind` enum)
- `relative_path`
- `sha256`
- `byte_size`
- `media_type`
- `description`

Observed artifact kinds include `rendered_dom_html`, `response_body`, `screenshot_viewport`, `screenshot_full_page`, `navigation_record`, and `capture_manifest`. These are capture evidence types; the inspected enum does not define a PDF-specific artifact kind. Do not assume a PDF capture path exists solely because a PDF can be represented as bytes.

Artifact files are stored under the configured USIE data directory in capture-specific paths. The documented writer uses fixed filenames, atomic creation, fsync, restrictive permissions, and hash verification. Preserve artifact paths as producer-owned references; do not expose absolute local paths in exported contracts.

### Extraction result

The typed `ExtractionResult` includes `extraction_id`, `capture_id`, `source_id`, `normalized_url`, `extractor`, `extractor_version`, `status`, optional post text/author/title/canonical URL/metadata, field-level `provenance`, error code, issues, schema version and created timestamp.

Validation rules require provenance for every populated extracted field. `success` requires non-empty validated `post_text`; an extraction with an error cannot be successful. Extraction result status is constrained to success/partial and is not the same field as the capture outcome.

### Collector and persistence boundary

`Collector` receives an injected `SqliteRepository`, capturer, settings, and optional extractor. Its default extractor is `OpenGraphAdapter`. Architecture docs say the generic metadata adapter reads locally saved DOM evidence and performs no network I/O; it does not provide platform-specific extraction.

The repository provides transactional operations for runs/jobs, sources, captures, artifact descriptors, extraction results and audit events. Duplicate jobs are idempotent per run/source; a recapture is a new capture. Recovery marks unfinished captures failed/interrupted rather than deleting evidence.

## Integration decision

1. Do not invent a public export endpoint, SDK, or filesystem manifest shape. No stable producer export API was found in the inspected paths.
2. Build the first OCIF integration against a versioned fixture/export function explicitly approved by the USIE owner, or add a producer-side export command in a separate reviewed change.
3. The export should include the typed artifact descriptor plus source/capture identity, exact canonical statuses, extraction result and producer revision. Resolve `relative_path` only inside the configured USIE evidence root and verify SHA-256 before reading.
4. If PDFs are to be processed, first add and test a documented PDF artifact acquisition path. The existing artifact enum is not itself proof that USIE collects PDF files.
5. Keep PDF extraction status separate from capture status. Never mark browser-only evidence or an unvalidated document conversion as USIE `success`.
6. Contract tests must cover all capture statuses, missing artifacts, hash mismatch, traversal/absolute paths, duplicate capture, recapture, and secrets redaction.

## Verification limits

This report is based on source inspection of the listed files and design records. The connector did not execute USIE tests or query a live USIE database in this session. No claim is made that an OCIF export command or PDF capture feature currently exists. The producer/consumer contract remains subject to explicit owner approval.
