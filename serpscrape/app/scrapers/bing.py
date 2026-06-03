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

_EXTRACT_JS = r"""
() => {
  // Bing wraps result links in /ck/a redirects; decode the base64url 'u' param
  // back to the real destination URL.
  const decode = (href) => {
    try {
      const u = new URL(href, location.href);
      if (u.pathname.includes('/ck/a')) {
        let raw = u.searchParams.get('u') || '';
        if (raw.startsWith('a1')) raw = raw.slice(2);
        raw = raw.replace(/-/g, '+').replace(/_/g, '/');
        while (raw.length % 4) raw += '=';
        const dec = atob(raw);
        if (/^https?:\/\//.test(dec)) return dec;
      }
    } catch (e) {}
    return href;
  };
  const out = [];
  const seen = new Set();
  for (const li of document.querySelectorAll('li.b_algo')) {
    const a = li.querySelector('h2 a[href]');
    if (!a) continue;
    const url = decode(a.href);
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
        mkt = g["locale"]  # e.g. "en-US"
        mkt_l = mkt.lower()

        # Hint the market via cookies (the headful context already sends en-US locale
        # + Accept-Language). Keep this minimal — earlier attempts to *force* the market
        # with a homepage setmkt redirect / ensearch=1 made Bing serve a region page
        # with no results. NOTE: Bing ultimately geolocates by the egress IP, so from an
        # IP in another country results may still be localized; use a proxy in the target
        # country (per-task proxy field) for strict geo-targeting.
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
            # No &count= (it collapses Bing to a single page and ignores &first=).
            url = (
                f"https://www.bing.com/search?q={quote_plus(keyword)}"
                f"&mkt={mkt}&setlang={g['lang']}&first={first}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if await guard_block(page, ctx, self._check_captcha, bool(results)) == "break":
                break
            await self._dismiss_consent(page)
            await human_dwell(page)
            try:
                await page.wait_for_selector("li.b_algo", timeout=12000)
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

    async def _dismiss_consent(self, page: Page) -> None:
        # EU/GDPR cookie-consent banner can sit over (or instead of) results.
        for sel in [
            "#bnp_btn_accept",
            "button#bnp_btn_accept",
            'button:has-text("Accept all")',
            'button:has-text("Accept")',
            'button:has-text("I agree")',
            'a[aria-label="Accept"]',
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click(timeout=2000)
                    await page.wait_for_load_state("domcontentloaded")
                    return
            except Exception:
                continue

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
