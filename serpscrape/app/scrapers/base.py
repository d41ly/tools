from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

try:
    from playwright_stealth import stealth_async
except ImportError:  # graceful fallback if stealth lib not installed
    async def stealth_async(page: Page) -> None:  # type: ignore[no-redef]
        return None

from app.scrapers.geo import info as geo_info

_USER_AGENTS = [
    # Recent stable Chrome on common desktop OSes
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]


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


async def human_sleep(base_ms: int, jitter: float = 0.35) -> None:
    """Sleep base_ms ± jitter*base_ms (non-negative)."""
    if base_ms <= 0:
        return
    delta = base_ms * jitter
    actual = max(0.0, random.uniform(base_ms - delta, base_ms + delta)) / 1000.0
    await asyncio.sleep(actual)


@asynccontextmanager
async def open_context(
    pw: Playwright, ctx: ScrapeContext
) -> AsyncIterator[BrowserContext]:
    g = geo_info(ctx.country)
    proxy_arg = None
    if ctx.proxy:
        proxy_arg = {"server": ctx.proxy["server"]}
        if ctx.proxy.get("username"):
            proxy_arg["username"] = ctx.proxy["username"]
        if ctx.proxy.get("password"):
            proxy_arg["password"] = ctx.proxy["password"]
    browser = await pw.chromium.launch(
        headless=True,
        proxy=proxy_arg,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    try:
        context = await browser.new_context(
            user_agent=random.choice(_USER_AGENTS),
            locale=g["locale"],
            timezone_id=g["timezone"],
            viewport=random.choice(_VIEWPORTS),
            extra_http_headers={"Accept-Language": f"{g['locale']},{g['lang']};q=0.9,en;q=0.8"},
        )
        yield context
        await context.close()
    finally:
        await browser.close()


async def apply_stealth(page: Page) -> None:
    try:
        await stealth_async(page)
    except Exception:
        pass


class Scraper:
    """Abstract base. Subclasses implement scrape(...)."""

    engine: str = ""
    target_results: int = 100

    async def scrape(
        self, keyword: str, ctx: ScrapeContext
    ) -> list[ScrapedResult]:
        async with async_playwright() as pw:
            async with open_context(pw, ctx) as browser_ctx:
                page = await browser_ctx.new_page()
                await apply_stealth(page)
                return await self._scrape(page, keyword, ctx)

    async def _scrape(
        self, page: Page, keyword: str, ctx: ScrapeContext
    ) -> list[ScrapedResult]:
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
