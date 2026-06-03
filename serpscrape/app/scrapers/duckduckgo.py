from __future__ import annotations

from urllib.parse import quote_plus

from playwright.async_api import Page

from app.scrapers.base import (
    CaptchaError,
    ScrapeContext,
    ScrapedResult,
    Scraper,
    dedupe,
    guard_block,
    human_dwell,
    human_sleep,
)
from app.scrapers.geo import info as geo_info

# The "lite" endpoint is the most bot-tolerant DuckDuckGo surface: a plain
# table of results with stable classes (a.result-link / td.result-snippet) and
# a POST "Next Page" form. It avoids the JS SPA and the flakier /html/ endpoint.
_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const links = Array.from(document.querySelectorAll('a.result-link'));
  const snippets = Array.from(document.querySelectorAll('.result-snippet'));
  links.forEach((a, i) => {
    let url = a.href || a.getAttribute('href') || '';
    if (url.includes('duckduckgo.com/l/?')) {
      try {
        const u = new URL(url, location.href);
        const real = u.searchParams.get('uddg');
        if (real) url = decodeURIComponent(real);
      } catch (e) { /* ignore */ }
    }
    if (!/^https?:\/\//.test(url)) return;
    if (seen.has(url)) return;
    seen.add(url);
    const s = snippets[i];
    out.push({ title: (a.innerText || '').trim(), url, description: s ? s.innerText.trim() : '' });
  });
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
            f"https://lite.duckduckgo.com/lite/?q={quote_plus(keyword)}"
            f"&kl={g['ddg']}"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        results: list[ScrapedResult] = []
        prev_len = -1
        # ~10 results per page; paginate via the POST "Next Page" form.
        for _ in range(12):
            if await guard_block(page, ctx, self._check_block, bool(results)) == "break":
                break
            await human_dwell(page)
            try:
                await page.wait_for_selector("a.result-link", timeout=12000)
            except Exception:
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
                break  # page added nothing new
            prev_len = len(results)

            nxt = await page.query_selector('input[type="submit"][value*="Next"]')
            if not nxt:
                break
            await human_sleep(ctx.per_page_delay_ms)
            try:
                await nxt.click()
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
        if "if this error persists" in text or ("anomaly" in text and "automated" in text):
            raise CaptchaError("DuckDuckGo flagged this request as automated")
