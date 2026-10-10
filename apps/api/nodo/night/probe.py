"""Site probes: is the page up, fast, showing what it should, free of JavaScript errors?

`HttpProber` needs nothing but httpx. `BrowserProber` additionally drives headless Chromium through Playwright
(optional dependency) to catch what an HTTP check cannot see: console errors, uncaught exceptions, failed
requests. If Playwright is missing or cannot start, it silently degrades to the HTTP check.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx

USER_AGENT = "NODO-Monitor/1.0 (+night shift; read-only)"
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# noise we never blame on the site itself
_IGNORED_REQUEST_FAILURES = ("google-analytics", "googletagmanager", "doubleclick", "facebook", "hotjar",
                             "favicon", "/_vercel/insights", "/_vercel/speed-insights", "clarity.ms")


@dataclass
class ProbeResult:
    url: str
    ok: bool
    status: int | None = None
    latency_ms: int = 0
    error: str | None = None
    title: str | None = None
    expect: str | None = None
    expect_found: bool | None = None
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    size: int = 0
    protected: bool = False   # Vercel Deployment Protection answered (401/403): not a site failure
    method: str = "http"      # http | browser

    @property
    def down(self) -> bool:
        """The page cannot be served at all (server error, timeout, DNS/TLS failure)."""
        return not self.protected and (self.status is None or self.status >= 500)

    def short(self) -> str:
        if self.protected:
            return "protetto da Vercel (serve il bypass)"
        if self.error and self.status is None:
            return self.error
        bits = [f"HTTP {self.status}", f"{self.latency_ms} ms"]
        if self.expect and self.expect_found is False:
            bits.append(f"testo atteso \"{self.expect}\" assente")
        if self.console_errors:
            bits.append(f"{len(self.console_errors)} errori JavaScript")
        return ", ".join(bits)


class Prober(Protocol):
    async def probe(self, url: str, *, expect: str | None = None, headers: dict | None = None,
                    browser: bool = False) -> ProbeResult: ...

    async def cert_days_left(self, url: str) -> int | None: ...

    async def close(self) -> None: ...


class HttpProber:
    def __init__(self, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None):
        self.timeout, self.transport = timeout, transport

    async def probe(self, url: str, *, expect: str | None = None, headers: dict | None = None,
                    browser: bool = False) -> ProbeResult:
        res = ProbeResult(url=url, ok=False, expect=expect)
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, transport=self.transport,
                                         headers={"User-Agent": USER_AGENT, **(headers or {})}) as c:
                r = await c.get(url)
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.status, res.size = r.status_code, len(r.content)
            body = r.text if "text" in r.headers.get("content-type", "text/html") else ""
            if m := _TITLE.search(body[:200_000]):
                res.title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
            served_by_vercel = "x-vercel-id" in r.headers or r.headers.get("server", "").lower() == "vercel"
            if r.status_code in (401, 403) and served_by_vercel:
                res.protected = True  # Deployment Protection (Vercel Authentication / password) on previews
            if expect is not None:
                res.expect_found = expect.lower() in body.lower()
            res.ok = r.status_code < 400 and res.expect_found is not False
        except httpx.TimeoutException:
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.error = f"nessuna risposta entro {int(self.timeout)} s"
        except httpx.HTTPError as e:
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.error = f"irraggiungibile ({type(e).__name__})"
        return res

    async def cert_days_left(self, url: str) -> int | None:
        u = urlparse(url)
        if u.scheme != "https" or not u.hostname:
            return None
        try:
            ctx = ssl.create_default_context()
            _, writer = await asyncio.wait_for(asyncio.open_connection(u.hostname, u.port or 443, ssl=ctx,
                                                                       server_hostname=u.hostname), timeout=10)
            cert = writer.get_extra_info("ssl_object").getpeercert()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), UTC)
            return (expires - datetime.now(UTC)).days
        except Exception:
            return None

    async def close(self) -> None:
        return None


class BrowserProber(HttpProber):
    """HTTP probe + (when asked) a real headless browser load to collect JavaScript errors."""

    def __init__(self, timeout: float = 20.0):
        super().__init__(timeout)
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self.unavailable_reason: str | None = None

    async def _ensure(self):
        if self._browser or self.unavailable_reason:
            return self._browser
        async with self._lock:
            if self._browser or self.unavailable_reason:
                return self._browser
            try:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
            except Exception as e:  # not installed, no browser binary, unsupported event loop...
                self.unavailable_reason = f"{type(e).__name__}: {str(e)[:120]}"
                self._browser = None
        return self._browser

    async def probe(self, url: str, *, expect: str | None = None, headers: dict | None = None,
                    browser: bool = False) -> ProbeResult:
        res = await super().probe(url, expect=expect, headers=headers)
        if not browser or not res.ok:
            return res
        b = await self._ensure()
        if b is None:
            return res
        ctx = None
        try:
            ctx = await b.new_context(user_agent=USER_AGENT, extra_http_headers=headers or {})
            page = await ctx.new_page()
            errors: list[str] = []
            failed: list[str] = []
            page.on("console", lambda m: errors.append(m.text[:300]) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"uncaught: {str(e)[:300]}"))
            page.on("requestfailed", lambda r: failed.append(r.url[:200])
                    if not any(x in r.url for x in _IGNORED_REQUEST_FAILURES) else None)
            await page.goto(url, wait_until="load", timeout=int(self.timeout * 1000))
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=5000)
            res.console_errors = [e for e in errors if not any(x in e for x in _IGNORED_REQUEST_FAILURES)][:10]
            res.failed_requests = failed[:10]
            res.method = "browser"
        except Exception as e:
            res.console_errors = []
            res.method = f"http (browser non riuscito: {type(e).__name__})"
        finally:
            if ctx is not None:
                with contextlib.suppress(Exception):
                    await ctx.close()
        return res

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        self._browser = self._pw = None
