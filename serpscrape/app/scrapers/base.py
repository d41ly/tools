from __future__ import annotations

import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

try:
    from playwright_stealth import stealth_async
except Exception:  # not installed, or version-incompatible at import time
    async def stealth_async(page: Page) -> None:  # type: ignore[no-redef]
        return None

from app.scrapers.geo import info as geo_info

log = logging.getLogger("scrapers.base")

# Headful (under Xvfb) is much harder to fingerprint than true headless.
# Set SCRAPER_HEADFUL=0 to force headless (e.g. local dev without a display).
_HEADFUL = os.environ.get("SCRAPER_HEADFUL", "1") != "0"

# Common, real desktop viewport sizes.
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

# Injected into every page before any site script runs. Neutralises the most
# common headless/automation tells. playwright-stealth covers some of these too;
# applying both is harmless and more robust across versions.
_EVASION_JS = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
try {
  const _q = window.navigator.permissions && window.navigator.permissions.query;
  if (_q) {
    window.navigator.permissions.query = (p) =>
      p && p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : _q(p);
  }
} catch (e) {}
try {
  const patch = (proto) => {
    const gp = proto.getParameter;
    proto.getParameter = function (p) {
      if (p === 37445) return 'Intel Inc.';                  // UNMASKED_VENDOR_WEBGL
      if (p === 37446) return 'Intel Iris OpenGL Engine';    // UNMASKED_RENDERER_WEBGL
      return gp.apply(this, [p]);
    };
  };
  if (window.WebGLRenderingContext) patch(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) patch(WebGL2RenderingContext.prototype);
} catch (e) {}
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
"""


class CaptchaError(RuntimeError):
    """Raised when the search engine returned a captcha / bot-protection page."""


@dataclass(slots=True)
class ScrapedResult:
    position: int
    title: str | None
    description: str | None
    url: str


@dataclass(slots=True)
class ScrapeContext:
    country: str
    proxy: dict | None
    per_page_delay_ms: int
    capsolver_api_key: str | None = None
    task_id: int | None = None


async def human_sleep(base_ms: int, jitter: float = 0.35) -> None:
    """Sleep base_ms ± jitter*base_ms (non-negative)."""
    if base_ms <= 0:
        return
    delta = base_ms * jitter
    actual = max(0.0, random.uniform(base_ms - delta, base_ms + delta)) / 1000.0
    await asyncio.sleep(actual)


async def human_dwell(page: Page) -> None:
    """A small, variable, human-like pause plus a little mouse movement / scroll.
    Applied on every page even when per-page delay is 0, so request timing and
    interaction look organic rather than instantaneous and identical."""
    await asyncio.sleep(random.uniform(0.4, 1.6))
    try:
        await page.mouse.move(
            random.randint(80, 900), random.randint(80, 600), steps=random.randint(3, 9)
        )
        if random.random() < 0.7:
            await page.mouse.wheel(0, random.randint(200, 1200))
            await asyncio.sleep(random.uniform(0.2, 0.8))
    except Exception:
        pass


@asynccontextmanager
async def open_context(pw: Playwright, ctx: ScrapeContext) -> AsyncIterator[BrowserContext]:
    g = geo_info(ctx.country)
    proxy_arg = None
    if ctx.proxy:
        proxy_arg = {"server": ctx.proxy["server"]}
        if ctx.proxy.get("username"):
            proxy_arg["username"] = ctx.proxy["username"]
        if ctx.proxy.get("password"):
            proxy_arg["password"] = ctx.proxy["password"]

    launch_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1920,1080",
        f"--lang={g['locale']}",
    ]

    async def _launch(headless: bool):
        return await pw.chromium.launch(
            headless=headless,
            proxy=proxy_arg,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )

    # Prefer headful (needs Xvfb's DISPLAY); fall back to headless if unavailable.
    try:
        browser = await _launch(headless=not _HEADFUL)
    except Exception as exc:
        log.warning("headful launch failed (%s); falling back to headless", exc)
        browser = await _launch(headless=True)

    try:
        context = await browser.new_context(
            locale=g["locale"],
            timezone_id=g["timezone"],
            viewport=random.choice(_VIEWPORTS),
            # Do NOT override the user agent: the bundled Chromium's real UA is
            # internally consistent (version, platform, client hints). A hand-rolled
            # UA with a mismatched Chrome version is itself a strong bot signal.
            extra_http_headers={
                "Accept-Language": f"{g['locale']},{g['lang']};q=0.9,en;q=0.8",
            },
            color_scheme="light",
        )
        await context.add_init_script(_EVASION_JS)
        yield context
        await context.close()
    finally:
        await browser.close()


async def apply_stealth(page: Page) -> None:
    try:
        await stealth_async(page)
    except Exception:
        pass


async def guard_block(page: Page, ctx: "ScrapeContext", detector, have_results: bool) -> str:
    """Run an engine-specific `detector(page)` coroutine that raises CaptchaError
    when the page is a captcha/block. If blocked and a Capsolver key is set, try to
    solve and re-check. Returns 'ok' (proceed) or 'break' (stop, keep results).
    Re-raises the original CaptchaError if blocked, unsolved, and nothing collected."""
    try:
        await detector(page)
        return "ok"
    except CaptchaError as exc:
        original = exc

    if ctx.capsolver_api_key:
        from app.scrapers import captcha  # local import avoids cycle

        if await captcha.solve(page, ctx.capsolver_api_key):
            try:
                await detector(page)
                return "ok"
            except CaptchaError as exc2:
                original = exc2

    if have_results:
        return "break"
    raise original


class Scraper:
    """Abstract base. Subclasses implement _scrape(...)."""

    engine: str = ""
    target_results: int = 100

    async def scrape(self, keyword: str, ctx: ScrapeContext) -> list[ScrapedResult]:
        async with async_playwright() as pw:
            async with open_context(pw, ctx) as browser_ctx:
                page = await browser_ctx.new_page()
                await apply_stealth(page)
                from app.scrapers import diagnostics  # local import avoids cycle
                try:
                    results = await self._scrape(page, keyword, ctx)
                except Exception as exc:
                    # Capture the page state before the context tears down, so blocks
                    # / captchas / unexpected layouts are inspectable.
                    await diagnostics.capture(
                        page, self.engine, keyword, ctx.task_id,
                        reason=f"exception:{type(exc).__name__}",
                    )
                    raise
                if not results:
                    await diagnostics.capture(
                        page, self.engine, keyword, ctx.task_id, reason="zero-results"
                    )
                elif os.environ.get("SCRAPER_DEBUG"):
                    # Opt-in: capture even on success, to inspect wrong-but-non-empty
                    # results (e.g. off-region/spam pages).
                    await diagnostics.capture(
                        page, self.engine, keyword, ctx.task_id, reason="debug"
                    )
                return results

    async def _scrape(self, page: Page, keyword: str, ctx: ScrapeContext) -> list[ScrapedResult]:
        raise NotImplementedError


def dedupe(results: list[ScrapedResult]) -> list[ScrapedResult]:
    seen: set[str] = set()
    out: list[ScrapedResult] = []
    for r in results:
        if not r.url or r.url in seen:
            continue
        seen.add(r.url)
        out.append(r)
    for i, r in enumerate(out, start=1):
        r.position = i
    return out
