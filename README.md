# Universal Social Intelligence Engine (USIE)

Standalone, evidence-first URL collection. **Phase P0 complete** (core, schemas, storage, evidence
preservation). P1 (platform adapters) onward is not started. Not production-certified. See
`docs/P0_ACCEPTANCE.md` for exactly what was and was not verified, and `docs/DECISIONS.md`
(**D-001 needs your decision**).

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

## Status semantics
`success` needs a validated extraction (P1+); browser-only evidence is `partial`. `blocked` and
`unavailable` require observed evidence; `failed` carries an error code; see `docs/DECISIONS.md` D-003.
