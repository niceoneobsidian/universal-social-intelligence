# Architecture (P0)

```
manifest -> validate (urlsafety) -> route (routing) -> job (idempotent per run)
  -> Layer 1 capture (capture/: Playwright + per-capture GuardProxy) -> artifacts (evidence/, hashed, never overwritten)
  -> classify (status.py, evidence-based) -> SQLite (db/: runs, jobs, sources, captures, artifacts, audit_events, ...)
  -> [Layer 2 extraction: P1]  -> [export/review: P3]
```
- `capture/` may not import extraction code or storage (enforced by an AST test).
- `collector.py` is the only place that composes layers.
- Schema changes: add a new `Migration`; never edit shipped ones. `social_evidence` is a compat VIEW (D-001).
