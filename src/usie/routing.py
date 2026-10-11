"""Hostname-aware platform classification (parsed hostname, never substring matching)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from usie.models import Platform


@dataclass(frozen=True)
class Route:
    platform: Platform
    hostname: str
    supported_host: bool


_HOSTS: tuple[tuple[Platform, tuple[str, ...]], ...] = (
    (Platform.X, ("x.com", "twitter.com")),
    (Platform.LINKEDIN, ("linkedin.com",)),
    (Platform.TIKTOK, ("tiktok.com",)),
    (Platform.INSTAGRAM, ("instagram.com",)),
    (Platform.THREADS, ("threads.net", "threads.com")),
    (Platform.YOUTUBE, ("youtube.com", "youtu.be")),
    (Platform.FACEBOOK, ("facebook.com", "fb.watch")),
)


def route_url(url: str) -> Route:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise ValueError("URL must be an absolute HTTP(S) URL with a hostname")
    for platform, domains in _HOSTS:
        if any(host == d or host.endswith("." + d) for d in domains):
            return Route(platform, host, True)
    return Route(Platform.UNIVERSAL, host, False)
