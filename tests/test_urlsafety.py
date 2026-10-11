from __future__ import annotations

import pytest

from usie.urlsafety import (
    DestinationPolicy,
    InvalidUrlError,
    UnsafeDestinationError,
    check_host,
    check_url,
    normalize_url,
    validate_submitted_url,
)

POLICY = DestinationPolicy()


def public_resolver(_: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "x.com/foo",
        "ftp://x.com/a",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,hi",
        "https:///missing-host",
        "https://user:pw@example.com/",
        "https://user@example.com/",
        "https://example.com\\@evil.com/",
        "https://exa mple.com/",
        "https://example.com/\x00",
        "https://example.com:99999/",
        "http://[::1/",
        "https://" + "a" * 3000 + ".com",
    ],
)
def test_rejects_invalid_and_malicious(url: str) -> None:
    with pytest.raises(InvalidUrlError):
        validate_submitted_url(url)


def test_preserves_submitted_and_normalizes_separately() -> None:
    raw = "HTTPS://Example.COM:443/Path?utm_source=a&id=1&fbclid=zzz#frag"
    v = validate_submitted_url(raw)
    assert v.submitted == raw
    assert v.normalized == "https://example.com/Path?id=1"
    assert v.hostname == "example.com"


def test_normalization_keeps_meaningful_params_and_nondefault_port() -> None:
    assert (
        normalize_url("http://example.com:8080/a?t=30&b=2") == "http://example.com:8080/a?t=30&b=2"
    )


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "foo.localhost",
        "printer.local",
        "db.internal",
        "127.0.0.1",
        "127.1",
        "2130706433",
        "0x7f.0.0.1",
        "017700000001",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "[::1]",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
    ],
)
def test_blocks_non_public_hosts(host: str) -> None:
    with pytest.raises(UnsafeDestinationError):
        check_host(host, POLICY, public_resolver)


def test_blocks_hostname_resolving_to_private() -> None:
    with pytest.raises(UnsafeDestinationError):
        check_host("evil.example", POLICY, lambda _: ["93.184.216.34", "10.0.0.7"])


def test_allows_public_and_unresolvable() -> None:
    check_host("example.com", POLICY, public_resolver)
    check_host("93.184.216.34", POLICY, public_resolver)

    def fail(_: str) -> list[str]:
        raise OSError("no dns")

    check_host("nonexistent.example", POLICY, fail)


def test_explicit_overrides() -> None:
    check_host("127.0.0.1", DestinationPolicy(allow_private_hosts=True), public_resolver)
    check_host(
        "127.0.0.1", DestinationPolicy(host_allowlist=frozenset({"127.0.0.1"})), public_resolver
    )
    with pytest.raises(UnsafeDestinationError):
        check_host(
            "10.0.0.1", DestinationPolicy(host_allowlist=frozenset({"127.0.0.1"})), public_resolver
        )


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/", "gopher://x/", "http://u:p@example.com/"]
)
def test_check_url_blocks_schemes_and_credentials(url: str) -> None:
    with pytest.raises(UnsafeDestinationError):
        check_url(url, POLICY, public_resolver)
