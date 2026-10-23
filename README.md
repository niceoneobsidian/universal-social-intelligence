# Universal Social Intelligence Engine (USIE)

Standalone, evidence-first URL collection. **P0 is complete; P1 extraction foundations are in
progress.** The generic metadata adapter reads saved DOM evidence only; it does not fetch pages
directly or provide platform-specific extraction. Not production-certified. See
`docs/P0_ACCEPTANCE.md` for P0 verification limits and `docs/DECISIONS.md` for decisions.

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev,browser]'
python -m playwright install chromium
usie route 'https://x.com/u/status/1?utm_source=a'   # offline validation + routing
usie collect input/urls.txt                          # one authorised URL per line
usie report; usie verify; usie db-status
pytest && ruff check src tests && mypy
```
Data lives in `~/.usie` (override `USIE_DATA_DIR`). Loopback/private destinations are blocked unless
explicitly allowed (`USIE_HOST_ALLOWLIST`, `USIE_ALLOW_PRIVATE_HOSTS`). Browser contexts are
unauthenticated unless `--storage-state` is given.

## OCIF integration

A read-only candidate export adapter is under review at `src/usie/integrations/ocif.py`. It reads canonical capture tables, verifies artifact bytes and field-level provenance, and emits a versioned envelope. It does not expose a network API or mutate capture history. See [OCIF export contract](docs/integrations/ocif-export.md). The adapter and contract remain unverified until tests and a disposable-database integration run pass.

## Status semantics
`success` needs a validated extraction (P1+); browser-only evidence is `partial`. `blocked` and
`unavailable` require observed evidence; `failed` carries an error code; see `docs/DECISIONS.md` D-003.

`social_evidence` is a read-only, lossy compatibility view over the latest capture, not a writable
store. Append-only `captures` are canonical; the legacy `status_label` maps `blocked`, `unavailable`,
and `failed` to `inaccessible`. See D-001.
