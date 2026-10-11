"""URL validation, normalization and destination (SSRF) policy.

Pre-checks here are advisory. The enforcing control is capture/proxy.py, which resolves once and
connects to the pinned address, so DNS rebinding between check and connect is not possible for
browser traffic. Direct (non-browser) fetches must use pin_destination() the same way.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from usie.models import ErrorCode

Resolver = Callable[[str], Sequence[str]]

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "igsh",
        "mc_cid",
        "mc_eid",
        "ref_src",
        "ref_url",
        "_hsenc",
        "_hsmi",
    }
)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan")
_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")


class InvalidUrlError(ValueError):
    code = ErrorCode.INVALID_URL


class UnsafeDestinationError(ValueError):
    code = ErrorCode.UNSAFE_DESTINATION


@dataclass(frozen=True)
class DestinationPolicy:
    allow_private_hosts: bool = False
    host_allowlist: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ValidatedUrl:
    submitted: str
    normalized: str
    scheme: str
    hostname: str


def validate_submitted_url(raw: str, max_length: int = 2048) -> ValidatedUrl:
    """Parse and validate. The submitted string is preserved verbatim for evidence."""
    url = raw.strip()
    if not url:
        raise InvalidUrlError("empty URL")
    if len(url) > max_length:
        raise InvalidUrlError("URL exceeds maximum length")
    if _CONTROL.search(url):
        raise InvalidUrlError("URL contains whitespace, control characters or backslashes")
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError as exc:
        raise InvalidUrlError(f"malformed URL: {exc}") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise InvalidUrlError("only http and https URLs are accepted")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidUrlError("URLs with embedded credentials are rejected")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidUrlError("URL has no hostname")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidUrlError("hostname is not valid IDNA") from exc
    return ValidatedUrl(url, normalize_url(url), parts.scheme.lower(), host)


def normalize_url(url: str) -> str:
    """Normalized form used for source identity. Never replaces the submitted URL."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    default = {"http": 80, "https": 443}.get(scheme)
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != default:
        netloc = f"{netloc}:{port}"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query), ""))


def literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    bare = host.strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    # Legacy IPv4 spellings browsers still accept: 2130706433, 0x7f.1, 017700000001, 127.1
    if re.fullmatch(r"[0-9a-fA-Fx.]+", bare):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(bare))
        except OSError:
            return None
    return None


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def pin_destination(
    host: str, policy: DestinationPolicy, resolver: Resolver = default_resolver
) -> str:
    """Resolve ONCE, check every address, return the address to connect to (DNS pinning).

    Raises UnsafeDestinationError for blocked destinations and OSError if unresolvable.
    """
    host = host.lower().rstrip(".")
    exempt = policy.allow_private_hosts or host in policy.host_allowlist
    if not exempt and (host == "localhost" or host.endswith(_BLOCKED_SUFFIXES)):
        raise UnsafeDestinationError(f"blocked local hostname: {host}")
    literal = literal_ip(host)
    if literal is not None:
        if not exempt and not _is_public(literal):
            raise UnsafeDestinationError(f"blocked non-public address: {literal}")
        return str(literal)
    addresses = [a.split("%", 1)[0] for a in resolver(host)]
    if not addresses:
        raise OSError(f"no addresses for {host}")
    if not exempt:
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not _is_public(ip):
                raise UnsafeDestinationError(f"{host} resolves to non-public address {ip}")
    return addresses[0]


def check_host(host: str, policy: DestinationPolicy, resolver: Resolver = default_resolver) -> None:
    try:
        pin_destination(host, policy, resolver)
    except UnsafeDestinationError:
        raise
    except OSError:
        return  # unresolvable: the browser will report a navigation error


def check_url(url: str, policy: DestinationPolicy, resolver: Resolver = default_resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeDestinationError(f"blocked scheme: {parts.scheme or '(none)'}")
    if parts.username is not None or parts.password is not None:
        raise UnsafeDestinationError("blocked URL with embedded credentials")
    host = parts.hostname
    if not host:
        raise UnsafeDestinationError("URL has no hostname")
    check_host(host, policy, resolver)


async def acheck_url(
    url: str, policy: DestinationPolicy, resolver: Resolver = default_resolver
) -> None:
    """check_url without blocking the event loop on DNS."""
    await asyncio.get_running_loop().run_in_executor(None, check_url, url, policy, resolver)
