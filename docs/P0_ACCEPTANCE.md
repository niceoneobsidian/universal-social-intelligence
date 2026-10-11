# P0 acceptance: core, schemas, storage, evidence preservation

Environment: Python 3.12.3, Linux container, Playwright 1.56.0, Chromium 141.0.7390.37,
pydantic 2.14.0, pytest 9.1.1, ruff 0.17.0, mypy 2.4.0. **Not run on macOS or Python 3.11.**

| # | Criterion | Evidence | Result |
|---|-----------|----------|--------|
| 1 | Versioned, transactional migrations; failed migration leaves DB unchanged; newer DB refused; upgrade preserves data | `tests/test_db.py` | pass |
| 2 | All 14 spec tables exist with FKs, CHECKs, unique constraints, indexes; audit log append-only | `tests/test_db.py` | pass |
| 3 | Reprocessing preserves history; sources not overwritten; jobs idempotent per run | `tests/test_repository.py`, `test_collector.py` | pass |
| 4 | URL validation rejects malformed/credentialed/non-HTTP/backslash/overlong URLs; routing by parsed hostname | `test_urlsafety.py`, `test_routing.py` | pass |
| 5 | SSRF: private/loopback/link-local/CGNAT/mapped/legacy-numeric hosts blocked; redirect to internal host blocked **and never contacted** (real Chromium) | `test_urlsafety.py`, `test_proxy.py`, `test_browser_integration.py` | pass |
| 6 | Evidence atomic, never overwritten, 0600/0700, traversal-safe, hash re-verified after write, tamper/loss detected | `test_evidence.py`, `usie verify` | pass |
| 7 | Evidence persists when capture crashes or a later step fails; manifest sealed on every path | `test_collector.py` | pass |
| 8 | Status taxonomy: no `success` without validated extraction; no deletion inferred from 401/403/429/5xx | `test_status.py`, browser tests | pass |
| 9 | Bounded retry with backoff for transient failures only; blocked never retried | `test_status.py`, `test_collector.py` | pass |
| 10 | Bounded concurrency; graceful cancellation; crash recovery | `test_collector.py`, manual real-SIGINT run (exit 130, 0.1 s) | pass |
| 11 | Secret redaction in logs, headers (`Set-Cookie` verified end-to-end), audit entries | `test_logging_and_config.py`, browser test | pass |
| 12 | Layer 1 does not import Layer 2 machinery or storage | `test_architecture_and_cli.py` (AST check) | pass |
| 13 | `ruff check` clean; `mypy --strict` clean (src + tests) | run | pass |
| 14 | Full suite | `pytest` → **147 passed** (~15 s, incl. 13 real-Chromium tests against local fixtures) | pass |

## Defects found by the tests during P0 (all fixed)
1. **SSRF via redirect** (D-009): route interception skipped redirect hops. Fixed with the filtering proxy; regression test asserts the internal target receives zero requests.
2. **Proxy 502 misread as origin 5xx**: unresolvable host classified `http_server_error`. Proxy responses are now tagged and mapped to `navigation_error`.
3. **Test gap**: post-write hash verification was untested (mutation survived). Test added.

Mutation check: 12 hand-made mutants on security/integrity logic; 10 killed immediately, 1 equivalent
(no behavioral change), 1 genuine gap (fixed, then killed). This is a spot check, not full mutation coverage.

## Not verified
Live social platforms (deliberately none), macOS, Python 3.11, Intel-Mac Playwright wheels, authenticated
sessions with a real storage-state file, large/pathological pages, behavior behind corporate proxies
(the filtering proxy connects directly and does not chain to an upstream proxy).
