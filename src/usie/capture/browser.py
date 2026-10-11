"""Playwright Chromium capture. Fresh unauthenticated context unless a storage state is given.

Every request the page makes (main document, redirects, subresources) passes the destination
guard; WebSockets are blocked outright; service workers are disabled.
"""

from __future__ import annotations

import importlib.metadata
import json
from types import TracebackType
from typing import Any

from usie.capture.base import ArtifactSink, CaptureRequest
from usie.capture.proxy import PROXY_HEADER, GuardProxy
from usie.logging_utils import REDACTED
from usie.models import ArtifactKind, ErrorCode, Observation, RedirectHop
from usie.urlsafety import (
    Resolver,
    UnsafeDestinationError,
    acheck_url,
    default_resolver,
)

_SECRET_HEADERS = {"set-cookie", "cookie", "authorization", "proxy-authorization"}
_ALLOWED_NON_HTTP = {"data", "blob", "about"}
VIEWPORT = {"width": 1440, "height": 1000}


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: (REDACTED if k.lower() in _SECRET_HEADERS else v) for k, v in headers.items()}


class PlaywrightCapturer:
    def __init__(self, resolver: Resolver = default_resolver) -> None:
        self._resolver = resolver
        self._pw: Any = None
        self._browser: Any = None
        self._versions: dict[str, str] = {}

    async def __aenter__(self) -> PlaywrightCapturer:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Install browser extras: pip install -e '.[browser]' "
                "&& python -m playwright install chromium"
            ) from exc
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(
                headless=True, proxy={"server": "per-context"}
            )
        except Exception:
            await self._pw.stop()
            raise
        self._versions = {
            "playwright": importlib.metadata.version("playwright"),
            "chromium": str(self._browser.version),
        }
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def capture(self, request: CaptureRequest, sink: ArtifactSink) -> Observation:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        options: dict[str, Any] = {
            "viewport": VIEWPORT,
            "service_workers": "block",
            "accept_downloads": False,
        }
        if request.storage_state is not None:
            if not request.storage_state.is_file():
                return Observation(
                    error_code=ErrorCode.SESSION_STATE_INVALID,
                    errors=["storage state file does not exist"],
                    tool_versions=self._versions,
                )
            options["storage_state"] = str(request.storage_state)

        blocked: list[str] = []
        errors: list[str] = []
        verdicts: dict[str, str | None] = {}

        async def guard(route: Any, req: Any) -> None:
            url: str = req.url
            scheme = url.split(":", 1)[0].lower()
            if scheme in _ALLOWED_NON_HTTP:
                await route.continue_()
                return
            host_key = url.split("/")[2] if "//" in url else url
            if host_key not in verdicts:
                try:
                    await acheck_url(url, request.policy, self._resolver)
                    verdicts[host_key] = None
                except UnsafeDestinationError as exc:
                    verdicts[host_key] = str(exc)
            verdict = verdicts[host_key]
            if verdict is not None:
                blocked.append(f"{url[:300]} :: {verdict}")
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        async def ws_guard(ws: Any) -> None:
            blocked.append(f"{ws.url[:300]} :: websocket blocked")
            await ws.close()

        guard_proxy = GuardProxy(request.policy, self._resolver)
        proxy_url = await guard_proxy.start()
        # "<-loopback>" makes Chromium send loopback traffic through the proxy too.
        options["proxy"] = {"server": proxy_url, "bypass": "<-loopback>"}
        try:
            context = await self._browser.new_context(**options)
        except Exception as exc:
            await guard_proxy.stop()
            return Observation(
                error_code=ErrorCode.SESSION_STATE_INVALID,
                errors=[f"context:{type(exc).__name__}"],
                tool_versions=self._versions,
            )
        try:
            await context.route("**/*", guard)
            await context.route_web_socket("**/*", ws_guard)
            page = await context.new_page()
            response = None
            error_code: ErrorCode | None = None
            try:
                response = await page.goto(
                    request.url, wait_until="domcontentloaded", timeout=request.timeout_ms
                )
            except PlaywrightTimeout:
                error_code = ErrorCode.NAVIGATION_TIMEOUT
                errors.append("navigation_timeout")
            except PlaywrightError as exc:
                error_code = (
                    ErrorCode.UNSAFE_DESTINATION
                    if (blocked or guard_proxy.blocked)
                    else ErrorCode.NAVIGATION_ERROR
                )
                errors.append(f"navigation:{str(exc).splitlines()[0][:200]}")

            timed_out = error_code is ErrorCode.NAVIGATION_TIMEOUT
            if request.wait_ms and (response is not None or timed_out):
                await page.wait_for_timeout(request.wait_ms)

            title: str | None = None
            final_url: str | None = page.url if page.url != "about:blank" else None
            if response is not None or error_code is ErrorCode.NAVIGATION_TIMEOUT:
                try:
                    title = await page.title()
                except PlaywrightError as exc:
                    errors.append(f"title:{type(exc).__name__}")
                await self._store(
                    sink,
                    ArtifactKind.RENDERED_DOM_HTML,
                    "text/html",
                    errors,
                    lambda: self._html(page),
                )
                if response is not None:
                    await self._store(
                        sink,
                        ArtifactKind.RESPONSE_BODY,
                        "application/octet-stream",
                        errors,
                        response.body,
                    )
                await self._store(
                    sink,
                    ArtifactKind.SCREENSHOT_VIEWPORT,
                    "image/png",
                    errors,
                    lambda: page.screenshot(full_page=False, timeout=request.timeout_ms),
                )
                await self._store(
                    sink,
                    ArtifactKind.SCREENSHOT_FULL_PAGE,
                    "image/png",
                    errors,
                    lambda: page.screenshot(full_page=True, timeout=request.timeout_ms),
                )
            body_text: str | None = None
            if response is not None:
                try:
                    body_text = (await page.locator("body").inner_text(timeout=5000))[:20_000]
                except PlaywrightError as exc:
                    errors.append(f"body_text:{type(exc).__name__}")

            chain: list[RedirectHop] = []
            headers: dict[str, str] = {}
            if response is not None:
                headers = redact_headers(await response.all_headers())
                chain = await self._redirect_chain(response)
                proxy_reason = {k.lower(): v for k, v in headers.items()}.get(PROXY_HEADER)
                if proxy_reason is not None:
                    # Generated by our proxy (denial / unreachable upstream), not by the origin.
                    error_code = (
                        ErrorCode.UNSAFE_DESTINATION
                        if proxy_reason == "blocked"
                        else ErrorCode.NAVIGATION_ERROR
                    )
                    errors.append(f"proxy_response:{proxy_reason}")
                    response = None
                    body_text = None
            all_blocked = blocked + guard_proxy.blocked
            return Observation(
                http_status=response.status if response is not None else None,
                final_url=final_url,
                title=title,
                redirect_chain=chain,
                response_headers=headers,
                body_text_sample=body_text,
                blocked_requests=all_blocked,
                errors=errors,
                error_code=error_code,
                tool_versions=self._versions,
            )
        finally:
            await context.close()
            await guard_proxy.stop()

    @staticmethod
    async def _html(page: Any) -> bytes:
        return str(await page.content()).encode("utf-8")

    @staticmethod
    async def _store(
        sink: ArtifactSink, kind: ArtifactKind, media_type: str, errors: list[str], produce: Any
    ) -> None:
        try:
            data = await produce()
            sink.put(kind, bytes(data), media_type)
        except Exception as exc:  # evidence gaps are recorded, never hidden
            errors.append(f"{kind.value}:{type(exc).__name__}:{str(exc).splitlines()[0][:160]}")

    @staticmethod
    async def _redirect_chain(response: Any) -> list[RedirectHop]:
        requests = []
        req = response.request
        while req is not None:
            requests.append(req)
            req = req.redirected_from
        requests.reverse()
        hops: list[RedirectHop] = []
        for r in requests:
            resp = await r.response()
            location = await resp.header_value("location") if resp else None
            hops.append(
                RedirectHop(url=r.url, status=resp.status if resp else None, location=location)
            )
        return hops


def navigation_record(submitted_url: str, obs: Observation) -> bytes:
    return (
        json.dumps(
            {
                "submitted_url": submitted_url,
                "final_url": obs.final_url,
                "http_status": obs.http_status,
                "redirect_chain": [h.model_dump() for h in obs.redirect_chain],
                "redirect_chain_note": "HTTP redirects only; JavaScript and meta-refresh redirects "
                "are not included.",
                "response_headers_redacted": obs.response_headers,
                "blocked_requests": obs.blocked_requests,
                "errors": obs.errors,
                "error_code": obs.error_code.value if obs.error_code else None,
                "tool_versions": obs.tool_versions,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    ).encode()
