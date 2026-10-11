# Decisions, assumptions and open questions

Status key: **Confirmed** (verified this session) · **Assumed** (reasoned, not verified) · **Open** (needs an owner decision).

## D-001 Legacy `social_evidence` schema vs. the build specification — **Open (needs your ruling)**
The context PDFs define a single `social_evidence` table (one row per URL, `UNIQUE source_url`,
`status_label` ∈ success|partial|inaccessible) and "System Rules" forbidding column changes and
any status values other than those three. The build specification requires the opposite in places:
append-only capture history (no destructive upsert), 14 tables, and the statuses
`blocked`/`unavailable`/`failed`/`cancelled`, plus "do not infer deleted from a generic access error".
A one-row-per-URL table with a 3-value enum cannot satisfy both.

**Resolution applied:** the real schema follows the specification; `social_evidence` exists as a
**read-only VIEW** (migration 3) exposing the *latest* capture per source under the legacy column
names (`id, source_url, platform, author, captured_at, status_label, post_text, raw_metadata_json`).
No legacy column was renamed. `status_label` maps success→success, partial→partial, and
blocked/unavailable/failed→`inaccessible`. **The mapping is lossy by design** (a crash and a
deleted post both read as "inaccessible"). Consumers that need the distinction must read `captures`.
If the 3-value enum is a hard requirement for the *primary* store, say so and the spec's
status/idempotency requirements need to be relaxed.

## D-002 Platform and dependencies
- Python ≥3.11 declared. **Tested only on Python 3.12.3 / Linux.** 3.11 and macOS are untested.
- Runtime dependencies: `pydantic` (models) and `playwright` (extra). Dev: pytest, pytest-asyncio, ruff, mypy.
- **Confirmed:** Playwright for Python is Apache-2.0, requires Python ≥3.9, current line is 1.6x.
- **Assumed, not verified this session:** Pydantic v2 license (believed MIT) and support window.
- **Open:** a conda listing noted macOS-64 (Intel) support for Playwright ended after 1.58 on that
  channel. Whether PyPI wheels differ for Intel Macs was not checked. Verify on your machine.
- yt-dlp, gallery-dl, ArchiveBox, ArchiveWeb.page, twscrape/twikit, Marker, Lift, Datasette:
  **not yet researched or adopted** (P1–P3 scope).

## D-003 Status semantics
`success` is never assigned from browser evidence alone; it needs a validated Layer 2 extraction
(P1). A Playwright-only capture is therefore at best `partial` (reason `evidence_only_no_validated_extraction`).
`blocked` requires HTTP 401/403/429 or an access-wall phrase in the title / opening of a *short*
page; `unavailable` requires HTTP 404/410 or a not-found phrase on a short page. Marker lists are
heuristics, English-only, and will have false negatives and false positives; every decision stores
its reason string.

## D-004 Identity and history
Source identity = UUIDv5 of the *normalized* URL (lowercased host, default port and fragment
removed, tracking params `utm_*, fbclid, gclid, igshid, igsh, mc_cid, mc_eid, ref_src, ref_url,
_hsenc, _hsmi` removed). Generic `s=`/`t=` are **not** stripped (YouTube `t=` is a timestamp). The
submitted URL is preserved verbatim on `jobs` and `captures`; `sources.source_url` is first-seen.
Re-collection never overwrites: each attempt is a new `captures` row with new artifacts.

## D-005 Capture lifecycle and recovery
A `captures` row is inserted as `running`, receives artifact rows as evidence is written, then is
finalized once (this is the only UPDATE of an observation row). A crash leaves `running` rows;
`usie recover` marks them `failed/interrupted` without deleting evidence. SIGINT cancels cleanly
(verified manually and by test).

## D-006 Evidence storage
`<data_dir>/captures/<id[:2]>/<capture_id>/<fixed filename>`; capture IDs must be canonical UUIDs;
filenames are from a fixed allow-list (never from URLs); files are written atomically with
`os.link` (fails if the destination exists), fsynced, mode 0600 (dirs 0700), then re-hashed.
The default data dir is `~/.usie`, outside any source tree.

## D-007 `audit_events` is append-only (DB triggers)
Retention/deletion procedures (P4) will need an explicit, audited path. **Open:** decide whether
deletion is a documented maintenance migration or a dedicated tombstone mechanism.

## D-008 Artifacts are labelled by what they contain
`rendered_dom_html` is a serialized DOM, **not** original server bytes; `response_body` is the
decoded main-document body and may be an app shell. Redirect chains cover HTTP redirects only
(not JS/meta refresh). Full-page screenshots may omit lazy/virtualized content. HAR/WACZ are not yet captured.

## D-009 SSRF: request interception is insufficient — **Confirmed by experiment**
In Playwright 1.56 / Chromium 141, `page.route`/`context.route` was **not invoked for server
redirect hops**: a 302 from an allowed host to `127.0.0.2` was followed and answered (observed
by a server on that address). The original design relied on route interception and would have been
vulnerable. Enforcement is now a per-capture filtering proxy (`capture/proxy.py`) that resolves
DNS once, validates all addresses, and connects to the pinned IP (also closing DNS rebinding for
browser traffic). Proxy-generated responses carry `X-USIE-Proxy` so they cannot be mistaken for
origin responses. WebSockets are blocked outright; service workers are disabled. Residual risk:
non-browser fetches added later (e.g. yt-dlp subprocess) are **not** covered by this proxy and need
their own controls in P1.

## D-010 Unverified platform facts
`threads.com` was added alongside `threads.net` from recollection, not research. Verify.

## D-011 Process notes (honesty)
- The core modules were written before most tests (not strict test-first). Tests were then written
  adversarially and mutation-checked; this found one genuine SSRF bug (D-009), one misclassification
  (proxy 502 read as origin 5xx) and one test gap (post-write hash check). Details in `docs/P0_ACCEPTANCE.md`.
- SQLite calls and artifact `fsync` run on the event-loop thread. Fine at bounded concurrency
  (default 2); revisit if throughput matters.

## D-012 Known P0 limitations (not defects, just not done)
Expired/invalid session detection is limited to "file missing / context creation failed"; an expired
cookie jar will look like an ordinary login wall (classified by markers, so possibly `partial`).
No HAR/WACZ, no media download, no CSV/Markdown export, no FTS, no review UI — later phases.
