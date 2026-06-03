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
  const headings = document.querySelectorAll(
    'div.MjjYud h3, div.g h3, .tF2Cxc h3, div.kvH3mc h3, #rso h3'
  );
  for (const h of headings) {
    const a = h.closest('a[href]');
    if (!a) continue;
    const url = a.href;
    if (!url || !/^https?:\/\//.test(url)) continue;
    if (url.includes('/aclk?') || url.startsWith('https://www.google.com/')) continue;
    if (url.includes('googleadservices.com') || url.includes('webcache.googleusercontent.com')) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const container = h.closest('.MjjYud, .g, .tF2Cxc, .kvH3mc') || a.parentElement;
    let desc = '';
    if (container) {
      const d = container.querySelector('div[data-sncf], .VwiC3b, .lEBKkf, .yXK7lf');
      if (d) desc = d.innerText.trim();
    }
    out.push({ title: h.innerText.trim(), url, description: desc });
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

        # Pre-set the consent cookie so we skip the EU consent interstitial that
        # otherwise hides results behind an "Accept all" dialog.
        try:
            await page.context.add_cookies(
                [
                    {
                        "name": "CONSENT",
                        "value": "YES+cb.20220301-11-p0.en+FX+000",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", "domain": ".google.com", "path": "/"},
                ]
            )
        except Exception:
            pass

        results: list[ScrapedResult] = []
        start = 0
        # Standard 10-per-page pagination. We deliberately avoid num=100, which
        # Google treats as a bot signal (and has largely stopped honoring).
        while start < 100:
            url = (
                f"https://www.google.com/search?q={quote_plus(keyword)}"
                f"&hl={g['lang']}&gl={ctx.country.lower()}&pws=0&num=10&start={start}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self._handle_consent(page)
            # A captcha on a later page should keep the results we already have;
            # only fail outright if we were blocked before collecting anything.
            try:
                await self._check_captcha(page)
            except CaptchaError:
                if results:
                    break
                raise
            try:
                await page.wait_for_selector("div#search, div#rso, div#main", timeout=12000)
            except Exception:
                try:
                    await self._check_captcha(page)
                except CaptchaError:
                    if not results:
                        raise
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
            results = dedupe(results)
            if len(results) >= self.target_results:
                break
            start += 10
            await human_sleep(ctx.per_page_delay_ms)

        return results[: self.target_results]

    async def _handle_consent(self, page: Page) -> None:
        for sel in [
            "#L2AGLb",
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Reject all")',
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
            raise CaptchaError(
                "Google blocked the request (captcha / unusual-traffic page). "
                "Google heavily blocks datacenter IPs — configure a residential proxy for this task."
            )
        if await page.query_selector("form#captcha-form, div#recaptcha"):
            raise CaptchaError("Google captcha form detected")
        try:
            text = (await page.locator("body").inner_text(timeout=2000)).lower()
        except Exception:
            return
        if "unusual traffic" in text or "our systems have detected" in text:
            raise CaptchaError(
                "Google flagged this as unusual traffic. Configure a residential proxy for this task."
            )
