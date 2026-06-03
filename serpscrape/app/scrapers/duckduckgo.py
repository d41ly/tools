from __future__ import annotations

from urllib.parse import quote_plus

from playwright.async_api import Page

from app.scrapers.base import (
    CaptchaError,
    ScrapeContext,
    ScrapedResult,
    Scraper,
    dedupe,
    human_sleep,
)
from app.scrapers.geo import info as geo_info

# The server-rendered HTML endpoint (html.duckduckgo.com/html/) returns stable,
# parseable markup without requiring the JS SPA to hydrate. Each result is a
# `div.result` with an `a.result__a` title link and an `a.result__snippet`.
_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div.result, div.web-result')) {
    if (el.classList.contains('result--ad') || el.classList.contains('result--no-result')) continue;
    const a = el.querySelector('a.result__a');
    if (!a) continue;
    let url = a.href || a.getAttribute('href') || '';
    // DDG wraps outbound links as //duckduckgo.com/l/?uddg=<encoded-real-url>
    if (url.includes('duckduckgo.com/l/?')) {
      try {
        const u = new URL(url, location.href);
        const real = u.searchParams.get('uddg');
        if (real) url = decodeURIComponent(real);
      } catch (e) { /* ignore */ }
    }
    if (!/^https?:\/\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const s = el.querySelector('a.result__snippet, .result__snippet');
    out.push({ title: (a.innerText || '').trim(), url, description: s ? s.innerText.trim() : '' });
  }
  return out;
}
"""


class DuckDuckGoScraper(Scraper):
    engine = "duckduckgo"

    async def _scrape(
        self, page: Page, keyword: str, ctx: ScrapeContext
    ) -> list[ScrapedResult]:
        g = geo_info(ctx.country)
        url = (
            f"https://html.duckduckgo.com/html/?q={quote_plus(keyword)}"
            f"&kl={g['ddg']}"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await self._check_block(page)

        results: list[ScrapedResult] = []
        prev_len = -1
        # The HTML endpoint serves ~20-30 results per page; paginate via the
        # POST "Next" form at the bottom until we reach the target or run dry.
        for _ in range(8):
            try:
                await page.wait_for_selector("div.result, div.web-result", timeout=12000)
            except Exception:
                await self._check_block(page)
                break

            for r in await page.evaluate(_EXTRACT_JS):
                results.append(
                    ScrapedResult(
                        position=0,
                        title=r.get("title") or None,
                        description=r.get("description") or None,
                        url=r["url"],
                    )
                )
            results = dedupe(results)
            if len(results) >= self.target_results:
                break
            if len(results) == prev_len:
                break  # this page added nothing new
            prev_len = len(results)

            # The last submit button in the bottom nav is always "Next".
            submits = await page.query_selector_all('.nav-link input[type="submit"]')
            if not submits:
                break
            await human_sleep(ctx.per_page_delay_ms)
            try:
                await submits[-1].click()
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                break

        return results[: self.target_results]

    async def _check_block(self, page: Page) -> None:
        url = page.url.lower()
        if "anomaly" in url or "captcha" in url:
            raise CaptchaError("DuckDuckGo anomaly/blocked page encountered")
        try:
            text = (await page.locator("body").inner_text(timeout=2000)).lower()
        except Exception:
            return
        if "anomaly" in text and "automated" in text:
            raise CaptchaError("DuckDuckGo flagged this request as automated")
