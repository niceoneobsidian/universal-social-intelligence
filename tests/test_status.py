from __future__ import annotations

from usie.models import ErrorCode, Observation, Status
from usie.retry import RetryPolicy
from usie.status import classify_observation


def obs(**kw: object) -> Observation:
    return Observation(**kw)  # type: ignore[arg-type]


def test_clean_page_is_partial_not_success() -> None:
    d = classify_observation(obs(http_status=200, title="Hello", body_text_sample="x" * 5000))
    assert d.status is Status.PARTIAL
    assert d.reason == "evidence_only_no_validated_extraction"


def test_success_requires_validated_extraction() -> None:
    d = classify_observation(
        obs(http_status=200, title="t", body_text_sample="hi"), extraction_validated=True
    )
    assert d.status is Status.SUCCESS


def test_http_codes() -> None:
    assert classify_observation(obs(http_status=403)).status is Status.BLOCKED
    assert classify_observation(obs(http_status=429)).status is Status.BLOCKED
    assert classify_observation(obs(http_status=401)).status is Status.BLOCKED
    assert classify_observation(obs(http_status=404)).status is Status.UNAVAILABLE
    assert classify_observation(obs(http_status=410)).status is Status.UNAVAILABLE
    d = classify_observation(obs(http_status=503))
    assert d.status is Status.FAILED and d.retryable and d.error_code is ErrorCode.HTTP_SERVER_ERROR


def test_generic_access_error_is_never_deletion() -> None:
    assert (
        classify_observation(obs(http_status=403, body_text_sample="page not found")).status
        is Status.BLOCKED
    )


def test_login_wall_text_on_short_page_is_blocked() -> None:
    d = classify_observation(
        obs(http_status=200, title="X", body_text_sample="Please log in to continue")
    )
    assert d.status is Status.BLOCKED


def test_marker_in_long_article_is_ignored() -> None:
    text = "Intro. " + "word " * 2000 + " verify you are human"
    assert (
        classify_observation(obs(http_status=200, title="Blog", body_text_sample=text)).status
        is Status.PARTIAL
    )


def test_not_found_text_on_200_is_unavailable_with_reason() -> None:
    d = classify_observation(
        obs(http_status=200, title="", body_text_sample="Sorry, this page isn't available.")
    )
    assert d.status is Status.UNAVAILABLE and d.reason.startswith("not_found_marker")


def test_navigation_failures() -> None:
    t = classify_observation(obs(error_code=ErrorCode.NAVIGATION_TIMEOUT))
    assert t.status is Status.FAILED and t.retryable
    u = classify_observation(obs(error_code=ErrorCode.UNSAFE_DESTINATION))
    assert u.status is Status.FAILED and not u.retryable
    n = classify_observation(obs(error_code=ErrorCode.NAVIGATION_ERROR))
    assert n.retryable
    s = classify_observation(obs(error_code=ErrorCode.SESSION_STATE_INVALID))
    assert s.status is Status.FAILED and not s.retryable
    assert classify_observation(obs()).status is Status.FAILED


def test_retry_policy() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay_s=1, jitter=0)
    transient = classify_observation(obs(http_status=503))
    blocked = classify_observation(obs(http_status=403))
    assert policy.should_retry(transient, 1) and policy.should_retry(transient, 2)
    assert not policy.should_retry(transient, 3)
    assert not policy.should_retry(blocked, 1)
    assert [policy.delay_s(a) for a in (1, 2, 3)] == [1, 2, 4]
    assert RetryPolicy(base_delay_s=100, max_delay_s=5, jitter=0).delay_s(4) == 5
