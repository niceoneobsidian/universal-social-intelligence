from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from usie.capture.proxy import PROXY_HEADER, GuardProxy
from usie.urlsafety import DestinationPolicy


async def echo_server() -> tuple[asyncio.Server, int, list[bytes]]:
    seen: list[bytes] = []

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = await r.readuntil(b"\r\n\r\n")
        seen.append(head)
        w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await w.drain()
        w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1]), seen


async def talk(proxy_url: str, payload: bytes) -> bytes:
    port = int(proxy_url.rsplit(":", 1)[1])
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(payload)
    await w.drain()
    data = await asyncio.wait_for(r.read(65536), 5)
    w.close()
    return data


@pytest.fixture
async def origin() -> AsyncIterator[tuple[int, list[bytes]]]:
    server, port, seen = await echo_server()
    yield port, seen
    server.close()
    await server.wait_closed()


async def test_forwards_allowed_http_and_strips_hop_headers(
    origin: tuple[int, list[bytes]],
) -> None:
    port, seen = origin
    proxy = GuardProxy(DestinationPolicy(host_allowlist=frozenset({"127.0.0.1"})))
    url = await proxy.start()
    try:
        reply = await talk(
            url,
            (
                f"GET http://127.0.0.1:{port}/a?b=1 HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\nProxy-Connection: keep-alive\r\n"
                "Proxy-Authorization: Basic Zm9v\r\nAccept: x\r\n\r\n"
            ).encode(),
        )
    finally:
        await proxy.stop()
    assert reply.endswith(b"ok") and PROXY_HEADER.encode() not in reply.lower()
    head = seen[0].lower()
    assert head.startswith(b"get /a?b=1 http/1.1")
    assert b"proxy-" not in head and b"connection: close" in head and b"accept: x" in head
    assert proxy.blocked == []


@pytest.mark.parametrize(
    "payload",
    [
        b"GET http://127.0.0.1:1/x HTTP/1.1\r\nHost: a\r\n\r\n",
        b"GET http://localhost:1/x HTTP/1.1\r\nHost: a\r\n\r\n",
        b"GET http://[::1]:1/x HTTP/1.1\r\nHost: a\r\n\r\n",
        b"GET http://169.254.169.254/latest/meta-data HTTP/1.1\r\nHost: a\r\n\r\n",
        b"GET http://2130706433/ HTTP/1.1\r\nHost: a\r\n\r\n",
        b"CONNECT 10.0.0.5:443 HTTP/1.1\r\nHost: 10.0.0.5:443\r\n\r\n",
        b"CONNECT [fd00::1]:443 HTTP/1.1\r\nHost: x\r\n\r\n",
    ],
)
async def test_denies_private_destinations_with_tagged_response(payload: bytes) -> None:
    proxy = GuardProxy(DestinationPolicy())
    url = await proxy.start()
    try:
        reply = await talk(url, payload)
    finally:
        await proxy.stop()
    assert reply.startswith(b"HTTP/1.1 403") and b"x-usie-proxy: blocked" in reply.lower()
    assert len(proxy.blocked) == 1


async def test_malformed_requests_get_400() -> None:
    proxy = GuardProxy(DestinationPolicy())
    url = await proxy.start()
    try:
        for payload in (
            b"NONSENSE\r\n\r\n",
            b"GET /relative HTTP/1.1\r\n\r\n",
            b"CONNECT nohost HTTP/1.1\r\n\r\n",
            b"GET ftp://a/ HTTP/1.1\r\n\r\n",
        ):
            reply = await talk(url, payload)
            assert reply.startswith(b"HTTP/1.1 400"), payload
    finally:
        await proxy.stop()


async def test_dns_is_resolved_once_and_pinned(origin: tuple[int, list[bytes]]) -> None:
    port, _ = origin
    calls: list[str] = []

    def rebinding_resolver(host: str) -> list[str]:
        calls.append(host)
        # A hostile DNS server: first answer is loopback (allowlisted here), later answers
        # would point at an internal address. Pinning means only the first is ever used.
        return ["127.0.0.1"] if len(calls) == 1 else ["10.0.0.9"]

    proxy = GuardProxy(
        DestinationPolicy(host_allowlist=frozenset({"pinned.test"})), rebinding_resolver
    )
    url = await proxy.start()
    try:
        reply = await talk(
            url, (f"GET http://pinned.test:{port}/ HTTP/1.1\r\nHost: pinned.test\r\n\r\n").encode()
        )
    finally:
        await proxy.stop()
    assert reply.endswith(b"ok")
    assert calls == ["pinned.test"]  # exactly one resolution per request


async def test_hostname_resolving_to_private_is_denied() -> None:
    proxy = GuardProxy(DestinationPolicy(), lambda _h: ["93.184.216.34", "192.168.0.10"])
    url = await proxy.start()
    try:
        reply = await talk(url, b"GET http://evil.test/ HTTP/1.1\r\nHost: evil.test\r\n\r\n")
    finally:
        await proxy.stop()
    assert b"403" in reply.split(b"\r\n")[0] and "192.168.0.10" in proxy.blocked[0]


async def test_unreachable_upstream_is_tagged_not_origin_502() -> None:
    proxy = GuardProxy(DestinationPolicy(host_allowlist=frozenset({"127.0.0.1"})))
    url = await proxy.start()
    try:
        reply = await talk(url, b"GET http://127.0.0.1:1/ HTTP/1.1\r\nHost: a\r\n\r\n")
    finally:
        await proxy.stop()
    assert b"x-usie-proxy: upstream_unreachable" in reply.lower()
