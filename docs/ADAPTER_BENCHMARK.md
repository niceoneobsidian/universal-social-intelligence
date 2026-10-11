# Adapter benchmark protocol

Run candidate adapters on the same sample and record exact tool versions.

| Case | Evaluation target |
|---|---|
| Public text post | Visible text, author when exposed, canonical URL |
| Image post | Text plus image references; download only when authorized and needed |
| Video post | Title, description, author, available captions/metadata |
| Thread/carousel | Parent-child relationship and item completeness where exposed |
| Authorized login-required content | Explicit blocked/partial classification; no access-control bypass |
| Deleted/invalid URL | Unavailable only when supported by evidence; otherwise failed/unknown |
| Redirected URL | Preserve input URL and final URL separately |

Measure field completeness, media reference precision/recall against manual review, artifact completeness and hashes, status accuracy, median/p95 runtime, repeat-run consistency, error categories, and cost where applicable. Include sample size and denominators. Do not infer universal performance from a small or biased sample.
