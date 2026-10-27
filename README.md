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

## Status semantics
`success` needs a validated extraction (P1+); browser-only evidence is `partial`. `blocked` and
`unavailable` require observed evidence; `failed` carries an error code; see `docs/DECISIONS.md` D-003.

`social_evidence` is a read-only, lossy compatibility view over the latest capture, not a writable
store. Append-only `captures` are canonical; the legacy `status_label` maps `blocked`, `unavailable`,
and `failed` to `inaccessible`. See D-001.

## OCIF evidence export (draft contract v0.1)
The read-only `export-ocif` command exports finalized captures to a JSON bundle for a future OCIF importer. It opens SQLite in read-only mode, verifies every referenced artifact against its stored SHA-256 and size, redacts likely secrets from URLs/text, and fails closed on integrity or provenance errors. It does not call Meta APIs or change the canonical USIE database.

```bash
# Print all finalized captures as JSON (no database writes)
usie export-ocif

# Export one capture to stdout
usie export-ocif --capture-id <CAPTURE_UUID>

# Write a new private file; an existing path is never overwritten
usie export-ocif --capture-id <CAPTURE_UUID> --output ./exports/capture.json
```
The exporter and contract are still draft integration work. The OCIF importer, durable receipt, and end-to-end producer/consumer acceptance are not implemented yet. Review [the contract](docs/integrations/ocif-evidence-contract.md) and [the JSON Schema](schemas/ocif-usie-evidence-v0.1.schema.json).
