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

_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const li of document.querySelectorAll('li.b_algo')) {
    const a = li.querySelector('h2 a[href]');
    if (!a) continue;
    const url = a.href;
    if (!url || !/^https?:\/\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const descEl = li.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4, .b_algoSlug');
    out.push({
      title: (a.innerText || '').trim(),
      url,
      description: descEl ? descEl.innerText.trim() : '',
    });
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
        mkt = g["locale"]  # e.g. "en-US" — Bing's authoritative market selector
        mkt_l = mkt.lower()

        # Lock Bing to the requested market. Without this, Bing geolocates by the
        # server's egress IP and returns mixed-language/irrelevant results.
        try:
            await page.context.add_cookies(
                [
                    {"name": "_EDGE_S", "value": f"mkt={mkt_l}&ui={mkt_l}", "domain": ".bing.com", "path": "/"},
                    {"name": "_EDGE_CD", "value": f"m={mkt_l}&u={mkt_l}", "domain": ".bing.com", "path": "/"},
                    {"name": "SRCHHPGUSR", "value": f"SRCHLANG={g['lang']}", "domain": ".bing.com", "path": "/"},
                ]
            )
        except Exception:
            pass

        results: list[ScrapedResult] = []
        first = 1
        prev_len = -1
        stall = 0
        while len(results) < self.target_results and first <= 150:
            # NOTE: do NOT send &count= — it makes Bing collapse to a single page
            # and ignore &first=, which caps results at ~10. Plain &first= paginates.
            url = (
                f"https://www.bing.com/search?q={quote_plus(keyword)}"
                f"&mkt={mkt}&setlang={g['lang']}&first={first}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await self._check_captcha(page)
            except CaptchaError:
                if results:
                    break
                raise
            try:
                await page.wait_for_selector("li.b_algo", timeout=12000)
            except Exception:
                try:
                    await self._check_captcha(page)
                except CaptchaError:
                    if not results:
                        raise
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
            if len(results) == prev_len:
                stall += 1
                if stall >= 2:
                    break
            else:
                stall = 0
            prev_len = len(results)
            first += 10
            await human_sleep(ctx.per_page_delay_ms)

        return results[: self.target_results]

    async def _check_captcha(self, page: Page) -> None:
        url = page.url.lower()
        if "bing.com/sa/su" in url or "captcha" in url:
            raise CaptchaError("Bing captcha challenge encountered")
        try:
            text = (await page.locator("body").inner_text(timeout=2000)).lower()
        except Exception:
            return
        if "verify you are a human" in text or ("blocked" in text and "bing" in text):
            raise CaptchaError("Bing flagged this request")
