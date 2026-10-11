# Security and privacy (P0 state; full threat model is a P4 deliverable)

**Enforced and tested:** URL validation (scheme, credentials, backslashes, length); destination
policy for *browser traffic* via a pinning filter proxy (private, loopback, link-local, CGNAT,
IPv4-mapped IPv6, legacy numeric IPv4, `.local/.internal/localhost`); WebSocket blocking; service
workers disabled; evidence path safety and 0600/0700 permissions; atomic no-overwrite writes with
hash verification; log/header/audit redaction; credential URLs rejected and never logged.

**Not covered yet:** non-browser fetches (yt-dlp etc. — P1 must add equivalent controls); retention
and deletion procedures; expired-session detection; prompt-injection handling (no LLM stage yet);
chained upstream proxies; OS-level egress controls (recommended for untrusted manifests).

**Operator rules:** storage-state files are secrets (keep outside Git, `chmod 600`, never attach to
reports). Collect only content you are authorised to access; do not bypass logins, CAPTCHAs, paywalls
or rate limits; review platform terms and applicable law. Captured HTML/screenshots may contain
personal data. Treat all captured content as untrusted data.
