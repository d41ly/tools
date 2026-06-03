"""Capture what a search engine actually returned, for diagnosing empty/blocked
scrapes. On failure we log a concise summary (final URL, title, visible-text
snippet) to the worker log AND save the full HTML + a screenshot per task under
SCRAPER_DIAG_DIR, so problems are inspectable after the fact."""
from __future__ import annotations

import logging
import os
import re
import time

from playwright.async_api import Page

log = logging.getLogger("scrapers.diagnostics")

DIAG_DIR = os.environ.get("SCRAPER_DIAG_DIR", "/srv/data/diagnostics")


def _safe(s: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "x")[:60]


async def capture(
    page: Page,
    engine: str,
    keyword: str,
    task_id: int | None = None,
    reason: str = "zero-results",
) -> None:
    url = title = "?"
    try:
        url = page.url
        title = await page.title()
    except Exception:
        pass
    snippet = ""
    try:
        snippet = (await page.locator("body").inner_text(timeout=2000)).strip()
        snippet = re.sub(r"\s+", " ", snippet)
    except Exception:
        pass
    html = ""
    try:
        html = await page.content()
    except Exception:
        pass

    log.warning(
        "DIAG [%s] kw=%r task=%s reason=%s | url=%s | title=%r | html=%dB | text=%r",
        engine, keyword, task_id, reason, url, title, len(html), snippet[:400],
    )

    # Persist full artifacts (best-effort).
    try:
        ts = int(time.time())
        d = os.path.join(DIAG_DIR, f"task_{task_id if task_id is not None else 'adhoc'}")
        os.makedirs(d, exist_ok=True)
        base = f"{ts}_{engine}__{_safe(keyword)}"
        if html:
            with open(os.path.join(d, base + ".html"), "w", encoding="utf-8") as f:
                f.write(f"<!-- url={url} title={title} reason={reason} -->\n{html}")
        try:
            await page.screenshot(path=os.path.join(d, base + ".png"))
        except Exception:
            pass
        log.warning("DIAG artifacts: %s/%s.{html,png}", d, base)
    except Exception as e:
        log.warning("DIAG save failed: %s", e)
