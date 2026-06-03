"""Capsolver integration (https://github.com/AndreiDrang/python3-capsolver).

When a search engine throws a reCAPTCHA / hCaptcha / Cloudflare Turnstile and the
user has configured a Capsolver API key, we detect the widget, solve it remotely,
inject the token, and submit — so the scraper can carry on.

This is best-effort: Capsolver reliably returns a token, but injecting/submitting
varies per challenge page. Google's "/sorry/" interstitial in particular is the
hardest; headful mode + a residential proxy remain the most reliable for Google.
"""
from __future__ import annotations

import logging

from playwright.async_api import Page

log = logging.getLogger("scrapers.captcha")

# Identify a captcha widget and extract its sitekey + type.
_DETECT_JS = r"""
() => {
  // reCAPTCHA (explicit element)
  let el = document.querySelector('.g-recaptcha[data-sitekey], [data-sitekey][data-callback], div.g-recaptcha');
  if (el && el.getAttribute('data-sitekey')) {
    const ent = !!document.querySelector('script[src*="recaptcha/enterprise"]');
    return { type: ent ? 'recaptcha_ent' : 'recaptcha', sitekey: el.getAttribute('data-sitekey') };
  }
  // reCAPTCHA via iframe
  let f = document.querySelector('iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"]');
  if (f) {
    try {
      const u = new URL(f.src, location.href);
      const k = u.searchParams.get('k');
      if (k) return { type: f.src.includes('enterprise') ? 'recaptcha_ent' : 'recaptcha', sitekey: k };
    } catch (e) {}
  }
  // Cloudflare Turnstile
  let t = document.querySelector('.cf-turnstile[data-sitekey], [data-sitekey][data-action]');
  if (t && t.getAttribute('data-sitekey')) return { type: 'turnstile', sitekey: t.getAttribute('data-sitekey') };
  // hCaptcha
  let h = document.querySelector('.h-captcha[data-sitekey]');
  if (h && h.getAttribute('data-sitekey')) return { type: 'hcaptcha', sitekey: h.getAttribute('data-sitekey') };
  return null;
}
"""

# Inject the solved token into the standard response fields.
_INJECT_JS = r"""
(token) => {
  const setVal = (sel) => document.querySelectorAll(sel).forEach((e) => {
    e.value = token; e.innerHTML = token; e.style.display = 'block';
  });
  let ta = document.getElementById('g-recaptcha-response');
  if (ta) { ta.value = token; ta.style.display = 'block'; }
  setVal('textarea[name="g-recaptcha-response"]');
  setVal('input[name="cf-turnstile-response"]');
  setVal('textarea[name="h-captcha-response"]');
  setVal('#h-captcha-response');
  // Best-effort: invoke a reCAPTCHA callback if the page registered one.
  try {
    const cfg = window.___grecaptcha_cfg;
    if (cfg && cfg.clients) {
      for (const cid in cfg.clients) {
        const c = cfg.clients[cid];
        for (const k in c) {
          const o = c[k];
          if (o && typeof o === 'object') {
            for (const j in o) {
              const w = o[j];
              if (w && typeof w === 'object' && typeof w.callback === 'function') {
                try { w.callback(token); } catch (e) {}
              }
            }
          }
        }
      }
    }
  } catch (e) {}
}
"""

_SUBMIT_JS = r"""
() => {
  const ta = document.getElementById('g-recaptcha-response')
    || document.querySelector('[name="g-recaptcha-response"], [name="cf-turnstile-response"], [name="h-captcha-response"]');
  const form = ta ? ta.closest('form') : document.querySelector('form');
  if (form) {
    const btn = form.querySelector('button[type=submit], input[type=submit]');
    if (btn) { btn.click(); return true; }
    form.submit();
    return true;
  }
  const anyBtn = document.querySelector('button[type=submit], input[type=submit]');
  if (anyBtn) { anyBtn.click(); return true; }
  return false;
}
"""


def _extract_token(result) -> str | None:
    """python3-capsolver may return a dict or a pydantic model across versions."""
    d = result
    for attr in ("model_dump", "dict"):
        fn = getattr(result, attr, None)
        if callable(fn):
            try:
                d = fn()
                break
            except Exception:
                pass
    if not isinstance(d, dict):
        # last resort: attribute access
        err = getattr(result, "errorId", 0)
        sol = getattr(result, "solution", None)
        d = {"errorId": err, "solution": sol}
    if d.get("errorId") not in (0, None):
        log.warning("capsolver returned error: %s", d.get("errorCode") or d.get("errorDescription"))
        return None
    sol = d.get("solution")
    if isinstance(sol, dict):
        return sol.get("gRecaptchaResponse") or sol.get("token") or sol.get("text")
    if isinstance(sol, str):
        return sol
    return None


async def _request_token(api_key: str, ctype: str, url: str, sitekey: str) -> str | None:
    try:
        from python3_capsolver.core.enum import CaptchaTypeEnm
    except Exception:
        log.warning("python3-capsolver not installed; cannot solve captcha")
        return None

    payload = {"websiteURL": url, "websiteKey": sitekey}
    try:
        if ctype in ("recaptcha", "recaptcha_ent"):
            from python3_capsolver.recaptcha import ReCaptcha

            ct = getattr(
                CaptchaTypeEnm,
                "ReCaptchaV2EnterpriseTaskProxyLess" if ctype == "recaptcha_ent" else "ReCaptchaV2TaskProxyLess",
            )
            solver = ReCaptcha(api_key=api_key, captcha_type=ct)
        elif ctype == "turnstile":
            from python3_capsolver.cloudflare import CloudFlare

            solver = CloudFlare(api_key=api_key, captcha_type=CaptchaTypeEnm.AntiTurnstileTaskProxyLess)
        elif ctype == "hcaptcha":
            from python3_capsolver.hcaptcha import HCaptcha

            solver = HCaptcha(api_key=api_key, captcha_type=CaptchaTypeEnm.HCaptchaTaskProxyLess)
        else:
            return None

        result = await solver.aio_captcha_handler(task_payload=payload)
        return _extract_token(result)
    except TypeError:
        # Older/newer signature: parameters passed to constructor instead.
        try:
            result = await solver.aio_captcha_handler()  # type: ignore[possibly-undefined]
            return _extract_token(result)
        except Exception as exc:
            log.warning("capsolver call failed: %s", exc)
            return None
    except Exception as exc:
        log.warning("capsolver call failed: %s", exc)
        return None


async def solve(page: Page, api_key: str | None) -> bool:
    """Detect a captcha on the current page and try to solve it via Capsolver.
    Returns True if a token was obtained, injected, and the page (re)loaded."""
    if not api_key:
        return False
    try:
        info = await page.evaluate(_DETECT_JS)
    except Exception:
        info = None
    if not info or not info.get("sitekey"):
        log.info("captcha present but no recognizable sitekey; cannot auto-solve")
        return False

    log.info("solving %s via Capsolver (sitekey=%s…)", info["type"], str(info["sitekey"])[:12])
    token = await _request_token(api_key, info["type"], page.url, info["sitekey"])
    if not token:
        return False
    try:
        await page.evaluate(_INJECT_JS, token)
        await page.evaluate(_SUBMIT_JS)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        return True
    except Exception as exc:
        log.warning("token injection/submit failed: %s", exc)
        return False
