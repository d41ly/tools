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
  const items = document.querySelectorAll('li.b_algo');
  for (const li of items) {
    const a = li.querySelector('h2 a[href]');
    if (!a) continue;
    const url = a.href;
    if (!url || !/^https?:\\/\\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const title = (a.innerText || '').trim();
    const descEl = li.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4, .b_paractl');
    const desc = descEl ? descEl.innerText.trim() : '';
    out.push({title, url, description: desc});
  }
  return out;
}
"""


class BingScraper(Scraper):
    engine = "bing"

    async def _scrape(
        self, page: Page, keyword: str, ctx: ScrapeContext
    ) -> list[ScrapedResult]:
        g = geo_info(ctx.country)
        results: list[ScrapedResult] = []
        first = 1
        per_page = 20  # Bing serves ~10-15 algorithmic results per page reliably; paginate.
        empty_pages = 0
        while len(results) < self.target_results and empty_pages < 2:
            url = (
                f"https://www.bing.com/search?q={quote_plus(keyword)}"
                f"&count={per_page}&first={first}"
                f"&cc={ctx.country.lower()}&setlang={g['lang']}"
                f"&form=QBLH"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self._check_captcha(page)
            try:
                await page.wait_for_selector("ol#b_results, li.b_algo", timeout=15000)
            except Exception:
                await self._check_captcha(page)
                break

            extracted = await page.evaluate(_EXTRACT_JS)
            if not extracted:
                empty_pages += 1
            else:
                empty_pages = 0
            for r in extracted:
                results.append(
                    ScrapedResult(
                        position=0,
                        title=r.get("title") or None,
                        description=r.get("description") or None,
                        url=r["url"],
                    )
                )
            first += len(extracted) if extracted else per_page
            if first > 200:
                break
            await human_sleep(ctx.per_page_delay_ms)

        return dedupe(results)[: self.target_results]

    async def _check_captcha(self, page: Page) -> None:
        url = page.url.lower()
        if "bing.com/sa/su" in url or "captcha" in url:
            raise CaptchaError("Bing captcha challenge encountered")
        try:
            text = (await page.locator("body").inner_text(timeout=2000)).lower()
        except Exception:
            return
        if "verify you are a human" in text or "blocked" in text and "bing" in text:
            raise CaptchaError("Bing flagged this request")
