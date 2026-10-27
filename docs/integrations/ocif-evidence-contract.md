# USIE → OCIF integration boundary

**Contract ID:** `ocif.usie-import`  
**Contract version:** `0.1.0-draft`  
**Status:** The read-only USIE exporter is implemented on the integration branch; the OCIF consumer is not implemented. The contract remains draft pending local test execution and producer/consumer acceptance.

## Ownership

- **USIE** owns URL safety, routing, collection jobs, append-only captures, artifact storage and hashes, extraction results, and capture outcomes.
- **OCIF** consumes validated evidence references and builds downstream knowledge/intelligence. It must not write into USIE's database or treat generated analysis as observed evidence.
- **OIS** remains the future authority for scheduling, authorization, retries, approvals, checkpoints and audit. This contract does not add automation or external side effects.
- **Human review** remains required before any external publishing.

## First vertical slice

1. Collect one explicitly authorized public URL using the existing USIE CLI.
2. Run `usie verify`; do not export records whose artifact integrity fails.
3. Produce a versioned, redacted JSON envelope for a finalized capture using canonical `captures`, `artifacts` and `extraction_results` records—not the lossy `social_evidence` compatibility view.
4. Validate the envelope against `schemas/ocif-usie-evidence-v0.1.schema.json`.
5. Import it into OCIF as an immutable, provenance-linked observation. Key delivery idempotency by `(source_id, capture_id, contract_version)`.
6. Record the receipt/rejection and reason in the consumer. A later capture is a new observation; it never overwrites prior evidence.

**Current limitation:** step 3 now has a draft `usie export-ocif` CLI implementation and fixture tests. The OCIF importer, receipt endpoint, and end-to-end integration remain unimplemented; the tests still need to run in CI/local checkout.

## Envelope rules

- Preserve exact canonical status; never promote browser-only evidence or HTTP success to extraction success.
- Keep submitted and normalized URLs separate. Redact credentials, cookies, authorization headers, session-state paths and other secrets.
- Include producer revision, capture timestamps when available, artifact IDs/types/SHA-256/size/media type, extraction adapter/schema/version, warnings and evidence references.
- Artifact paths are local references, not portable URLs. An OCIF importer must not assume it can read USIE's filesystem. The first integration may use a controlled local export bundle; copying raw artifacts requires an explicit data-transfer policy.
- Source text and metadata are untrusted data. Prompt-like content must never become agent instructions.
- Unknown status or contract versions must be quarantined, not guessed. Missing/tampered evidence must fail provenance validation.
- Unknown metrics are unknown/null, not zero. A URL capture does not imply access to private Page Insights or account-only analytics.

## Outcome mapping

| USIE outcome | Consumer behavior |
|---|---|
| `success` | Accept only when the validated extraction and artifact provenance are present |
| `partial` | Retain as incomplete evidence; exclude from claims requiring missing fields |
| `blocked`, `unavailable` | Preserve the observed outcome; no indefinite retry |
| `failed`, cancelled/interrupted | Preserve exact status and reason; never remap silently |
| unknown value | Quarantine with a compatibility error |

## Acceptance matrix

- [ ] valid success with extraction and verified hashes
- [ ] partial capture with artifacts but no validated extraction
- [ ] blocked, unavailable, failed and cancellation/interruption outcomes
- [ ] duplicate delivery is idempotent
- [ ] recapture of one source produces a distinct observation
- [ ] missing artifact, hash mismatch and malformed timestamps are rejected/quarantined
- [ ] unknown status and contract version are rejected safely
- [ ] secrets are absent from the envelope
- [ ] hostile/prompt-like source text remains data
- [ ] export does not mutate USIE canonical records
- [ ] integration runs against a disposable data directory and records exact commands/results

## Implementation order

1. Map actual repository columns and timestamps to the schema; add fixture tests first.
2. [Implemented on the integration branch] Add a read-only exporter that selects finalized captures and resolves artifact references safely.
3. Verify exporter output against the JSON Schema in consumer/contract tests and verify artifact hashes. The exporter performs artifact hash and size checks; a full JSON Schema validator is not currently a runtime dependency.
4. Implement the OCIF importer and durable idempotency/receipt behavior.
5. Run producer and consumer contract tests plus one local end-to-end fixture.
6. Only after acceptance, design OIS-governed scheduling. Do not add platform-specific analytics claims or live Meta integration as part of this contract change.

## Source of truth

- [USIE architecture](ARCHITECTURE.md)
- [P0 acceptance and limitations](P0_ACCEPTANCE.md)
- [OCIF consumer contract draft](https://github.com/niceoneobsidian/obsidian-content-intelligence-factory/blob/main/docs/contracts/usie-to-ocif.md)
