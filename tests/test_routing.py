from __future__ import annotations

import pytest

from usie.models import Platform
from usie.routing import route_url


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://x.com/user/status/123", Platform.X),
        ("https://twitter.com/user/status/123", Platform.X),
        ("https://mobile.twitter.com/user/status/123", Platform.X),
        ("https://www.linkedin.com/posts/example", Platform.LINKEDIN),
        ("https://vm.tiktok.com/abc/", Platform.TIKTOK),
        ("https://www.instagram.com/p/abc/", Platform.INSTAGRAM),
        ("https://www.threads.net/@user/post/abc", Platform.THREADS),
        ("https://www.threads.com/@user/post/abc", Platform.THREADS),
        ("https://youtu.be/abc", Platform.YOUTUBE),
        ("https://www.youtube.com/watch?v=abc", Platform.YOUTUBE),
        ("https://www.facebook.com/post/abc", Platform.FACEBOOK),
        ("https://fb.watch/abc", Platform.FACEBOOK),
        ("https://example.org/path?next=x.com", Platform.UNIVERSAL),
        ("https://x.com.attacker.example/post", Platform.UNIVERSAL),
        ("https://notx.com/post", Platform.UNIVERSAL),
        ("https://example.org/x.com", Platform.UNIVERSAL),
    ],
)
def test_route_by_hostname(url: str, platform: Platform) -> None:
    assert route_url(url).platform == platform


@pytest.mark.parametrize("url", ["", "x.com/foo", "ftp://x.com/a", "https:///missing-host"])
def test_reject_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        route_url(url)
