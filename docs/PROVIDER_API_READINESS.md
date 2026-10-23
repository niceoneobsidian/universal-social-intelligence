# Provider API and Webhook Readiness Plan

**Status:** Design proposal; this document does not implement provider API adapters or an inbound webhook server.  
**Baseline:** `main` inspected at `75fe1c23545c05c7d0894a3e6550f22f7cd2348e`.  
**Scope:** Add authorized structured source feeds without weakening USIE URL safety, evidence, audit, or status semantics.

## Verified implementation baseline

The inspected repository contains:
- URL validation, hostname routing, browser capture, a filtering proxy, SQLite migrations/repository, capture/artifact evidence, status classification, CLI, and generic extraction from saved rendered DOM.
- `ExtractionRequest`, `Extractor`, and `OpenGraphAdapter`; extraction validates source identity, normalized URL, artifact size, and SHA-256.
- Tests for URL safety, routing, browser/proxy security, storage, status semantics, and generic extraction.

The inspected baseline does **not** expose a provider API adapter package, OAuth/token lifecycle, provider quota scheduler, webhook HTTP endpoint, webhook signature verification, webhook inbox/delivery ledger, or documented provider-specific API contract. URL routing to a platform is not proof of API integration or platform-specific extraction.

## Proposed boundaries

Keep browser capture and API ingestion as distinct acquisition methods. Do not force API payloads into the rendered-DOM extractor contract.

Suggested package boundaries (names are proposals):
- `usie.providers.base`: provider capability declaration and normalized source-record contract.
- `usie.providers.youtube`: YouTube Data API read adapter, initially keyword search and video metadata lookup.
- `usie.ingestion`: validated ingestion orchestration, rate-limit handling, idempotency, and audit events.
- `usie.webhooks`: later, provider-specific webhook verification and durable inbox processing only for providers that offer the required events.

A normalized record should retain provider/platform, native object ID, canonical URL when available, provider timestamps, observed-at timestamp, raw-response hash/reference, normalized fields, missing-field semantics, provider/API version, collection method, and validation warnings. Retain raw payloads only where policy and provider terms permit. Secrets, tokens, cookies, and authorization headers must never enter artifacts or logs.

## API ingestion requirements

1. Read credentials from environment or approved secret storage; never commit them.
2. Declare supported operations and fields per provider. Unsupported features must fail explicitly.
3. Validate responses against versioned schemas; bound and safely handle unknown fields.
4. Store observations append-only and define idempotency using provider identity, native object ID, and observation/version identity; an update must not destructively overwrite prior evidence.
5. Respect current quota and rate-limit headers, bounded retries with jitter, retry-after values, timeouts, cancellation, and provider-specific errors.
6. Distinguish observed absence, access denial, rate limiting, transient failure, and successful validated extraction.
7. Preserve provenance and data-retention/deletion obligations; provider terms override generic storage assumptions.
8. Treat fetched text as untrusted data, never instructions to an agent.
9. Do not claim real-time streaming when the source supports only polling/search.

## Webhook requirements (later milestone)

Add a webhook receiver only after selecting a provider and confirming its documented webhook product and event coverage. Require:
- TLS at deployment and provider-specific signature/authentication verification over the exact raw request bytes.
- Timestamp/replay protection where supported and constant-time signature comparison.
- Request-size limits, content-type checks, schema validation, secret-safe logs, and fail-closed behavior.
- Durable inbox persistence before acknowledging success; unique delivery IDs and idempotent processing.
- Fast acknowledgement and asynchronous processing; bounded retries, dead-letter handling, and replay tooling.
- Metrics for received, verified, rejected, duplicate, queued, processed, failed, and delayed deliveries.
- Tests for forged signatures, replay, duplicate delivery, malformed/oversized payloads, unavailable database, crash-after-persist, and retry.

Do not build a generic unauthenticated endpoint that accepts arbitrary provider payloads.

## Recommended first provider

Start with **YouTube Data API** as a candidate first read-only adapter, subject to current credentials, quota, terms, and intended-use review. It fits AI/software/automation creator and topic research and offers structured search/video resources. Implement explicit quota accounting and bounded batching. Do not hard-code an assumed daily quota: confirm the current quota allocation and per-operation cost against the official documentation and the project's Google Cloud configuration at implementation time. This is a polling/search integration, not a generic webhook stream.

Official references:
- https://developers.google.com/youtube/data/getting-started
- https://developers.google.com/youtube/v3/docs/search/list
- https://developers.google.com/youtube/data/determine_quota_cost

Do not select Reddit as the first commercial research feed without resolving current access and use restrictions. Re-check official terms for the exact intended research and commercial use before implementing that adapter.

## Implementation order and acceptance criteria

### A0 — Contract and policy
- Approve provider-neutral schema and storage/retention rules.
- Record source terms, fields, quota, auth, and deletion requirements.
- Acceptance: reviewed ADR and fixtures; no inferred platform capabilities.

### A1 — Read-only YouTube vertical slice
- Add an injectable HTTP transport and typed configuration; use credentials only for supported public-data operations.
- Implement bounded `search.list` and video-detail fetches; normalize only fields actually returned.
- Acceptance: fixture tests for valid, empty, malformed, quota-exceeded, unauthorized, rate-limited, and transient responses; no network in unit tests; no secret leakage; quota consumption recorded.

### A2 — Durable source observations
- Add a versioned migration and repository methods for API observations, idempotency, provenance, and append-only history.
- Acceptance: migration failure safety, duplicate idempotency, changed-observation history, transaction/crash tests, hashes, and source identity verified.

### A3 — CLI and operational controls
- Add explicit discover/import commands with bounded result limits, timeout, quota budget, and dry-run.
- Acceptance: command tests, deterministic JSON output, safe exit codes, cancellation, redacted diagnostics, and no browser-capture side effects.

### A4 — Provider contract and OCIF fixture export
- Define a versioned export/fixture contract from canonical records, not the lossy `social_evidence` compatibility view.
- Acceptance: fixtures include success/partial/failure, provenance, hashes, timestamps, producer revision, and schema version.

### A5 — Webhooks only if justified
- Choose a specific provider/event product and implement its exact verified protocol.
- Acceptance: signature/replay/duplicate/failure/recovery tests above; deployment and operational review before activation.

## Non-goals for this change

No scraping or login-wall bypass; no automated publishing; no unbounded collection; no promise of complete platform coverage; no production-readiness claim.
