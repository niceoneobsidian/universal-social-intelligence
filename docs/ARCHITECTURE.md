# Architecture

```
manifest -> validate (urlsafety) -> route (routing) -> job (idempotent per run)
  -> Layer 1 capture (capture/: Playwright + per-capture GuardProxy) -> artifacts (evidence/, hashed, never overwritten)
  -> Layer 2 extraction (extraction/: validated metadata from stored DOM only)
  -> classify (status.py, evidence-based) -> SQLite (db/: runs, jobs, sources, captures, artifacts, extraction_results, audit_events, ...)
  -> [export/review: P3]
```
- `capture/` may not import extraction code or storage (enforced by an AST test).
- `collector.py` is the only place that composes layers.
- The generic Open Graph/JSON-LD adapter performs no network I/O. A `success` extraction requires non-empty post text and artifact-hash provenance; title-only evidence remains `partial`.
- Future direct fetchers (including media or tool-specific fetchers) must resolve and pin every destination with `pin_destination()` or use an equivalent enforcing boundary before network access.
- Schema changes: add a new `Migration`; never edit shipped ones. `social_evidence` is a compat VIEW (D-001).
