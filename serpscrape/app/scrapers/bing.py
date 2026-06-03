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
        mkt = g["locale"]  # e.g. "en-US" — Bing's authoritative market selector
        mkt_l = mkt.lower()
        cc = ctx.country.lower()
        # ensearch=1 forces Bing's English experience from a non-English-geolocated
        # IP; only meaningful when the target language is English.
        ensearch = "&ensearch=1" if g["lang"] == "en" else ""

        # Lock Bing to the requested market. Without this, Bing geolocates by the
        # server's egress IP and returns mixed-language/irrelevant decoy results.
        try:
            await page.context.add_cookies(
                [
                    {"name": "_EDGE_S", "value": f"ui={mkt_l}&mkt={mkt_l}", "domain": ".bing.com", "path": "/"},
                    {"name": "_EDGE_CD", "value": f"m={mkt_l}&u={mkt_l}", "domain": ".bing.com", "path": "/"},
                    {
                        "name": "SRCHHPGUSR",
                        "value": f"SRCHLANG={g['lang']}&BRW=NOTP&BRH=S&CW=1920&CH=1080&DPR=1&UTC=0&MARKET={mkt_l}",
                        "domain": ".bing.com",
                        "path": "/",
                    },
                ]
            )
        except Exception:
            pass

        # Plant the market by hitting the homepage with setmkt first (this is how a
        # real user switching region establishes the cookie), then search.
        try:
            await page.goto(
                f"https://www.bing.com/?mkt={mkt}&setmkt={mkt}&setlang={g['lang']}{ensearch}",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await human_dwell(page)
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
                f"&mkt={mkt}&setmkt={mkt}&setlang={g['lang']}&cc={cc}{ensearch}&first={first}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if await guard_block(page, ctx, self._check_captcha, bool(results)) == "break":
                break
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
