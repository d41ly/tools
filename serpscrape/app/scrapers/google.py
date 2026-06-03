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
  const headings = document.querySelectorAll(
    'div.MjjYud h3, div.g h3, .tF2Cxc h3, div.kvH3mc h3'
  );
  for (const h of headings) {
    const a = h.closest('a[href]');
    if (!a) continue;
    const url = a.href;
    if (!url || !/^https?:\\/\\//.test(url)) continue;
    if (url.includes('/aclk?') || url.startsWith('https://www.google.com/url?')) continue;
    if (url.includes('webcache.googleusercontent.com')) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const container = h.closest('.MjjYud, .g, .tF2Cxc, .kvH3mc') || a.parentElement;
    let desc = '';
    if (container) {
      const d = container.querySelector(
        'div[data-sncf], .VwiC3b, .lEBKkf, .yXK7lf, .BNeawe.s3v9rd.AP7Wnd'
      );
      if (d) desc = d.innerText.trim();
    }
    out.push({title: h.innerText.trim(), url, description: desc});
  }
  return out;
}
"""


class GoogleScraper(Scraper):
    engine = "google"

    async def _scrape(
        self, page: Page, keyword: str, ctx: ScrapeContext
    ) -> list[ScrapedResult]:
        g = geo_info(ctx.country)
        # Pass through a consent flow if it appears, using gl/hl params first.
        results: list[ScrapedResult] = []
        start = 0
        page_size = 100
        while len(results) < self.target_results:
            url = (
                f"https://www.google.com/search?q={quote_plus(keyword)}"
                f"&num={page_size}&hl={g['lang']}&gl={ctx.country.lower()}"
                f"&pws=0&start={start}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self._handle_consent(page)
            await self._check_captcha(page)
            try:
                await page.wait_for_selector("div#search, div#rso, div#main", timeout=15000)
            except Exception:
                await self._check_captcha(page)
                break

            extracted = await page.evaluate(_EXTRACT_JS)
            if not extracted:
                break
            for r in extracted:
                results.append(
                    ScrapedResult(
                        position=0,
                        title=r.get("title") or None,
                        description=r.get("description") or None,
                        url=r["url"],
                    )
                )
            # If Google honored num=100, we're likely done.
            if len(extracted) >= 30:
                start += len(extracted)
            else:
                start += 10
            if start >= 100 or len(results) >= self.target_results:
                break
            await human_sleep(ctx.per_page_delay_ms)

        return dedupe(results)[: self.target_results]

    async def _handle_consent(self, page: Page) -> None:
        # Google EU consent: a button labelled "Accept all" or "Reject all" inside a dialog.
        for sel in [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Reject all")',
            '#L2AGLb',
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    return
            except Exception:
                continue

    async def _check_captcha(self, page: Page) -> None:
        url = page.url
        if "/sorry/" in url or "google.com/sorry" in url:
            raise CaptchaError("Google captcha challenge encountered")
        if await page.query_selector("form#captcha-form, div#recaptcha"):
            raise CaptchaError("Google captcha form detected")
        # Body text fallback
        try:
            text = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            return
        low = text.lower()
        if "unusual traffic" in low or "our systems have detected" in low:
            raise CaptchaError("Google flagged this as unusual traffic")
