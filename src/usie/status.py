"""Evidence-based status classification for Layer 1 observations.

Rules (each decision records its reason):
  * SUCCESS is never assigned from browser evidence alone; it requires a validated Layer 2
    extraction (P1+). A title, HTTP 200 or non-empty body is not proof of extraction.
  * BLOCKED needs observed evidence: HTTP 401/403/429, or an access-wall phrase in the title or
    in the opening of a *short* page. A phrase deep inside a long article is ignored.
  * UNAVAILABLE needs observed evidence: HTTP 404/410, or a not-found phrase on a short page.
    Generic access errors (401/403/429/5xx) are never read as deletion.
  * FAILED covers anything where no defensible status could be assigned; the error code says why.
"""

from __future__ import annotations

from usie.models import ErrorCode, Observation, Status, StatusDecision

BLOCK_MARKERS = (
    "log in to continue",
    "sign in to continue",
    "log in to see",
    "verify you are human",
    "unusual activity",
    "access denied",
    "are you a robot",
    "complete the captcha",
    "checking your browser",
)
UNAVAILABLE_MARKERS = (
    "this page isn't available",
    "page not found",
    "video unavailable",
    "post unavailable",
    "content isn't available",
    "this account doesn't exist",
    "this post is unavailable",
)
_SHORT_PAGE_CHARS = 4000
_OPENING_CHARS = 1500


def _marker_hit(markers: tuple[str, ...], title: str | None, text: str | None) -> str | None:
    title_l = (title or "").lower()
    text_l = (text or "").lower()
    for marker in markers:
        if marker in title_l:
            return f"title:{marker}"
        if len(text_l) < _SHORT_PAGE_CHARS and marker in text_l[:_OPENING_CHARS]:
            return f"body:{marker}"
    return None


def classify_observation(obs: Observation, *, extraction_validated: bool = False) -> StatusDecision:
    code = obs.error_code
    if code is ErrorCode.UNSAFE_DESTINATION:
        return StatusDecision(
            status=Status.FAILED, reason="destination rejected by safety policy", error_code=code
        )
    if code is ErrorCode.NAVIGATION_TIMEOUT:
        return StatusDecision(
            status=Status.FAILED, reason="navigation timed out", error_code=code, retryable=True
        )
    if code is not None and obs.http_status is None:
        return StatusDecision(
            status=Status.FAILED,
            reason=f"no HTTP response observed ({code.value})",
            error_code=code,
            retryable=code in {ErrorCode.NAVIGATION_ERROR},
        )
    status = obs.http_status
    if status is not None:
        if status in {401, 403, 429}:
            return StatusDecision(status=Status.BLOCKED, reason=f"http_{status}")
        if status in {404, 410}:
            return StatusDecision(status=Status.UNAVAILABLE, reason=f"http_{status}")
        if status >= 500:
            return StatusDecision(
                status=Status.FAILED,
                reason=f"http_{status}",
                error_code=ErrorCode.HTTP_SERVER_ERROR,
                retryable=True,
            )
    hit = _marker_hit(BLOCK_MARKERS, obs.title, obs.body_text_sample)
    if hit:
        return StatusDecision(status=Status.BLOCKED, reason=f"access_wall_marker:{hit}")
    hit = _marker_hit(UNAVAILABLE_MARKERS, obs.title, obs.body_text_sample)
    if hit:
        return StatusDecision(status=Status.UNAVAILABLE, reason=f"not_found_marker:{hit}")
    if status is None:
        return StatusDecision(
            status=Status.FAILED,
            reason="no HTTP response observed",
            error_code=ErrorCode.NAVIGATION_ERROR,
            retryable=True,
        )
    if extraction_validated:
        return StatusDecision(status=Status.SUCCESS, reason="validated extraction")
    return StatusDecision(status=Status.PARTIAL, reason="evidence_only_no_validated_extraction")
