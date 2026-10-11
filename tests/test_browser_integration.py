"""Real Chromium against local fixture servers. No live social platforms are contacted."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from usie.capture.browser import PlaywrightCapturer
from usie.collector import Collector
from usie.config import Settings
from usie.db import SqliteRepository
from usie.evidence import verify_artifact

pytestmark = pytest.mark.browser

HITS: dict[str, int] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        HITS[self.path] = HITS.get(self.path, 0) + 1
        path = self.path.split("?")[0]
        if path == "/ok":
            body = (
                b"<html><head><title>Fixture Post</title></head><body>"
                b"<h1>Hello evidence</h1><p>" + b"lorem " * 50 + b"</p></body></html>"
            )
            self._send(200, body, {"Set-Cookie": "sid=SUPERSECRET; HttpOnly"})
        elif path == "/spa":  # content only exists after JS runs
            self._send(
                200,
                b"<html><title>S</title><body><div id=a></div><script>"
                b"document.getElementById('a').textContent='rendered-by-js'</script>"
                b"</body></html>",
            )
        elif path == "/redir1":
            self._send(302, b"", {"Location": "/redir2"})
        elif path == "/redir2":
            self._send(301, b"", {"Location": "/ok"})
        elif path == "/404":
            self._send(404, b"<html><title>Nope</title><body>Page not found</body></html>")
        elif path == "/500":
            self._send(500, b"<html><body>boom</body></html>")
        elif path == "/403":
            self._send(403, b"<html><title>Denied</title><body>Access denied</body></html>")
        elif path == "/slow":
            time.sleep(3)
            self._send(200, b"<html><body>late</body></html>")
        elif path == "/to-private":
            addr = self.server.server_address
            port = int(addr[1]) if isinstance(addr, tuple) else 0
            self._send(302, b"", {"Location": f"http://localhost:{port}/secret"})
        elif path == "/secret":
            self._send(200, b"<html><body>internal-secret</body></html>")
        elif path == "/subresource":
            self._send(
                200,
                b"<html><title>Sub</title><body><img src='http://127.0.0.2:9/x.png'>"
                b"<script>try{new WebSocket('ws://127.0.0.1:9/ws')}catch(e){}</script>"
                b"hello</body></html>",
            )
        else:
            self._send(404, b"")

    def _send(self, code: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def local_settings(settings: Settings) -> Settings:
    # Loopback is blocked by default; allow exactly 127.0.0.1 for the fixture server.
    return replace(
        settings,
        host_allowlist=frozenset({"127.0.0.1"}),
        max_attempts=1,
        navigation_timeout_ms=15_000,
    )


async def collect(s: Settings, repo: SqliteRepository, url: str) -> dict[str, Any]:
    async with PlaywrightCapturer() as cap:
        await Collector(s, repo, cap).run([url])
    return repo.query("SELECT * FROM captures")[-1]


async def test_successful_capture_preserves_all_layer1_artifacts(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    cap = await collect(local_settings, repo, f"{server}/ok")
    assert cap["status"] == "partial" and cap["http_status"] == 200
    assert cap["page_title"] == "Fixture Post"
    arts = {a["kind"]: a for a in repo.query("SELECT * FROM artifacts")}
    assert set(arts) == {
        "rendered_dom_html",
        "response_body",
        "screenshot_viewport",
        "screenshot_full_page",
        "navigation_record",
        "capture_manifest",
    }
    for a in arts.values():
        assert (
            verify_artifact(
                local_settings.evidence_root, a["relative_path"], a["sha256"], a["byte_size"]
            )
            is None
        )
    png = (
        local_settings.evidence_root / arts["screenshot_full_page"]["relative_path"]
    ).read_bytes()
    assert png.startswith(b"\x89PNG")
    assert json.loads(cap["completeness_json"])["missing"] == []
    assert json.loads(cap["tool_versions_json"])["chromium"]


async def test_headers_are_redacted_in_navigation_record(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    await collect(local_settings, repo, f"{server}/ok")
    nav = next(a for a in repo.query("SELECT * FROM artifacts WHERE kind='navigation_record'"))
    text = (local_settings.evidence_root / nav["relative_path"]).read_text()
    assert "SUPERSECRET" not in text and "[REDACTED]" in text


async def test_rendered_dom_differs_from_raw_response_for_js_pages(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    await collect(local_settings, repo, f"{server}/spa")
    root = local_settings.evidence_root
    dom = next(root.rglob("rendered_dom.html")).read_text()
    raw = next(root.rglob("response_body.bin")).read_text()
    assert "rendered-by-js" in dom and "rendered-by-js" not in raw.split("<script>")[0]
    assert "textContent='rendered-by-js'" in raw


async def test_redirect_chain_preserves_submitted_and_final_url(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    cap = await collect(local_settings, repo, f"{server}/redir1?utm_source=t")
    assert cap["submitted_url"].endswith("/redir1?utm_source=t")  # tracking param kept
    assert cap["final_url"].endswith("/ok")
    chain = json.loads(cap["redirect_chain_json"])
    assert [h["status"] for h in chain] == [302, 301, 200]
    assert chain[0]["location"] == "/redir2"


@pytest.mark.parametrize(
    ("path", "status", "reason"),
    [
        ("/404", "unavailable", "http_404"),
        ("/403", "blocked", "http_403"),
        ("/500", "failed", "http_500"),
    ],
)
async def test_http_outcomes(
    server: str,
    local_settings: Settings,
    repo: SqliteRepository,
    path: str,
    status: str,
    reason: str,
) -> None:
    cap = await collect(local_settings, repo, f"{server}{path}")
    assert cap["status"] == status and cap["status_reason"] == reason
    kinds = {a["kind"] for a in repo.query("SELECT kind FROM artifacts")}
    assert "rendered_dom_html" in kinds  # evidence kept even for error pages


async def test_timeout_is_failed_retryable_and_keeps_partial_evidence(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    s = replace(local_settings, navigation_timeout_ms=800)
    cap = await collect(s, repo, f"{server}/slow")
    assert cap["status"] == "failed" and cap["error_code"] == "navigation_timeout"


async def test_default_policy_blocks_loopback_before_any_request(
    server: str, settings: Settings, repo: SqliteRepository
) -> None:
    HITS.clear()
    cap = await collect(replace(settings, max_attempts=1), repo, f"{server}/ok")
    assert cap["status"] == "failed" and cap["error_code"] == "unsafe_destination"
    assert HITS == {}  # the server never saw a request


async def test_redirect_to_private_host_is_blocked_mid_chain(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    HITS.clear()
    cap = await collect(local_settings, repo, f"{server}/to-private")
    assert cap["status"] == "failed" and cap["error_code"] == "unsafe_destination"
    assert "/secret" not in HITS  # the internal target was never contacted
    nav = next(a for a in repo.query("SELECT * FROM artifacts WHERE kind='navigation_record'"))
    record = json.loads((local_settings.evidence_root / nav["relative_path"]).read_text())
    assert any("localhost" in b for b in record["blocked_requests"])


async def test_page_subresources_and_websockets_to_private_hosts_are_blocked(
    server: str, local_settings: Settings, repo: SqliteRepository
) -> None:
    cap = await collect(local_settings, repo, f"{server}/subresource")
    assert cap["status"] == "partial"
    nav = next(a for a in repo.query("SELECT * FROM artifacts WHERE kind='navigation_record'"))
    blocked = json.loads((local_settings.evidence_root / nav["relative_path"]).read_text())[
        "blocked_requests"
    ]
    assert any("127.0.0.2:9/x.png" in b for b in blocked)
    assert any("websocket blocked" in b for b in blocked)


async def test_missing_storage_state_is_a_failure_not_a_silent_anonymous_capture(
    server: str, local_settings: Settings, repo: SqliteRepository, tmp_path: Any
) -> None:
    s = replace(local_settings, storage_state=tmp_path / "missing.json")
    cap = await collect(s, repo, f"{server}/ok")
    assert cap["status"] == "failed" and cap["error_code"] == "session_state_invalid"


async def test_unresolvable_host_is_navigation_error(
    local_settings: Settings, repo: SqliteRepository
) -> None:
    s = replace(local_settings, navigation_timeout_ms=8000)
    cap = await collect(s, repo, "http://does-not-exist.invalid/")
    assert cap["status"] == "failed"
    assert cap["error_code"] in {"navigation_error", "navigation_timeout"}
