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

_EXTRACT_JS = """
() => {
  const out = [];
  const seen = new Set();
  const items = document.querySelectorAll('article[data-testid="result"], li[data-layout="organic"], div.result');
  for (const li of items) {
    const a = li.querySelector('h2 a[href], a.result__a');
    if (!a) continue;
    let url = a.href;
    if (!url) continue;
    // DDG sometimes wraps with //duckduckgo.com/l/?uddg=
    if (url.includes('duckduckgo.com/l/?')) {
      try {
        const u = new URL(url, location.href);
        const real = u.searchParams.get('uddg');
        if (real) url = decodeURIComponent(real);
      } catch (e) { /* ignore */ }
    }
    if (!/^https?:\\/\\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const title = (a.innerText || '').trim();
    const descEl = li.querySelector('[data-result="snippet"], .result__snippet, span[data-testid="result-snippet"]');
    const desc = descEl ? descEl.innerText.trim() : '';
    out.push({title, url, description: desc});
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
            f"https://duckduckgo.com/?q={quote_plus(keyword)}"
            f"&kl={g['ddg']}&kp=-2"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await self._check_captcha(page)
        try:
            await page.wait_for_selector(
                'article[data-testid="result"], li[data-layout="organic"], .result',
                timeout=15000,
            )
        except Exception:
            await self._check_captcha(page)
            return []

        results: list[ScrapedResult] = []
        last_count = -1
        stalls = 0
        max_iters = 30
        for _ in range(max_iters):
            extracted = await page.evaluate(_EXTRACT_JS)
            for r in extracted:
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
            if len(results) == last_count:
                stalls += 1
                if stalls >= 2:
                    # Try clicking "More results" if present, else give up
                    btn = await page.query_selector('button#more-results, button:has-text("More results")')
                    if btn:
                        try:
                            await btn.click()
                            await human_sleep(ctx.per_page_delay_ms)
                            stalls = 0
                            continue
                        except Exception:
                            pass
                    break
            else:
                stalls = 0
            last_count = len(results)
            # Scroll to bottom to trigger lazy load
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await human_sleep(ctx.per_page_delay_ms)

        return results[: self.target_results]

    async def _check_captcha(self, page: Page) -> None:
        url = page.url.lower()
        if "duckduckgo.com/anomaly" in url or "captcha" in url:
            raise CaptchaError("DuckDuckGo anomaly check encountered")
