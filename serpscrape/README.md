# SERP Scraper

Self-hosted tool that queries **Google**, **Bing**, and **DuckDuckGo** for the first ~100 organic
results of a keyword (using a headless Chromium browser via Playwright), stores the results in
PostgreSQL, sends an email notification on completion, and exposes a clean web UI plus a
token-authenticated REST API.

> **Deployment model:** the app has no built-in user login. It is intended to run **behind a
> webauth reverse proxy** (e.g. oauth2-proxy, Cloudflare Access, Authelia). The REST API is
> protected by bearer tokens you create from the Settings page.

## Features

- Three search engines, ~100 results each per keyword (paginated/scrolled)
- Multiple keywords per task, queued and rate-limited
- Two rate-limit knobs per task: **per-page delay** and **per-keyword delay**
- Per-task **proxy** (HTTP / HTTPS / SOCKS5; optional auth — credentials encrypted at rest)
- **Geo-targeting** by ISO-3166 country (locale + timezone + Accept-Language + search engine hints)
- Anti-bot: randomized realistic user agents and viewports, jittered human-like delays,
  `playwright-stealth`, and explicit **captcha detection** (task fails with reason rather than
  silently returning empty results)
- Pause / resume / cancel running and queued tasks
- **Email notification** via SMTP on task completion (credentials encrypted at rest)
- REST API with bearer-token auth (tokens hashed with SHA-256 at rest, raw value shown once)
- Beautiful dark-mode UI: New Task, History, Settings
- Restart-safe: queued tasks resume, mid-flight tasks marked `failed` with reason
  `"interrupted by server restart"`

## Quick start (Docker Compose)

1. Generate an encryption key:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Copy `.env.example` → `.env` and fill in `APP_SECRET_KEY`, `POSTGRES_PASSWORD`, and the
   `DATABASE_URL` to match. Set `UI_HOSTNAME` to the hostname your webauth proxy uses (or
   leave `localhost` for local testing) and `PUBLIC_BASE_URL` to the URL the UI is served at.

3. Bring up the stack:

   ```bash
   docker compose up --build -d
   ```

4. Open `http://localhost:8000/` (or your webauth-fronted hostname). The SPA bootstraps its
   own session token via `GET /api/ui-token`, which is restricted to the configured
   `UI_HOSTNAME`.

5. In **Settings**, configure SMTP (host/port/from + optional user/pass) and a default
   notification email. Create one or more **API tokens** for external automation.

## REST API

Every endpoint requires `Authorization: Bearer <token>`.

### Create a task

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer scrp_..." \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": ["best running shoes", "noise cancelling headphones"],
    "engines": ["google", "bing", "duckduckgo"],
    "country": "US",
    "per_page_delay_ms": 1500,
    "per_keyword_delay_ms": 60000,
    "max_results": 100,
    "notify_email": "ops@example.com",
    "proxy": {"server": "http://1.2.3.4:8080", "username": "u", "password": "p"}
  }'
```

`max_results` (1–100) caps how many results are fetched per engine per keyword.

### List tasks / get one / control / delete

```bash
curl -H "Authorization: Bearer $T" http://localhost:8000/api/tasks
curl -H "Authorization: Bearer $T" http://localhost:8000/api/tasks/123
curl -X PATCH -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"action":"pause"}' http://localhost:8000/api/tasks/123
# delete one task (and its results), or many at once
curl -X DELETE -H "Authorization: Bearer $T" http://localhost:8000/api/tasks/123
curl -X POST -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"ids":[123,124,125]}' http://localhost:8000/api/tasks/bulk-delete
```

`action` is one of `pause`, `resume`, `cancel`.

### Get results / export

```bash
curl -H "Authorization: Bearer $T" \
  "http://localhost:8000/api/tasks/123/results?engine=google&keyword=best+running+shoes&limit=200"
curl -H "Authorization: Bearer $T" \
  "http://localhost:8000/api/tasks/123/summary"
# export all results of a task as csv / xlsx / tsv (tsv pastes straight into Google Sheets)
curl -H "Authorization: Bearer $T" \
  "http://localhost:8000/api/tasks/123/export?format=xlsx" -o results.xlsx
```

### Settings & tokens

```bash
curl -H "Authorization: Bearer $T" http://localhost:8000/api/settings
curl -X PUT -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"smtp_host":"smtp.example.com","smtp_port":587,"smtp_password":"hunter2"}' \
  http://localhost:8000/api/settings

curl -X POST -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"name":"ci-job"}' http://localhost:8000/api/tokens
curl -X DELETE -H "Authorization: Bearer $T" http://localhost:8000/api/tokens/42
```

Interactive API docs at `/api/docs`.

## Architecture

- **FastAPI** single process. The background worker is an `asyncio` task started in the
  app's lifespan handler — one task at a time, claimed via `SELECT ... FOR UPDATE SKIP LOCKED`
  so multiple replicas would coexist safely (default deployment is a single replica).
- **PostgreSQL 16** with four tables: `settings`, `api_tokens`, `tasks`, `task_results`.
  Migrations live in `alembic/versions/`.
- **Playwright** (Chromium) launched per scrape with stealth, randomized viewport, and the
  user-supplied proxy and country locale.
- **Static SPA** (vanilla HTML + Tailwind via CDN + Alpine.js) served by the same FastAPI
  process. No build step.

## Anti-detection & captchas

Search engines fingerprint headless browsers aggressively. Symptoms when detected:
Google shows a captcha, Bing returns *decoy* results (plausible but unrelated, often in a
random language), DuckDuckGo returns nothing. To reduce this:

- **Headful under Xvfb** — Chromium runs in headful mode on a virtual display (started by the
  entrypoint), which is far harder to fingerprint than true headless. Set `SCRAPER_HEADFUL=0`
  to force headless (e.g. local dev without Xvfb); the launcher also auto-falls back to
  headless if no display is available.
- **Fingerprint evasions** — `playwright-stealth` plus an init script that normalises
  `navigator.webdriver`, `plugins`, `languages`, the `chrome` object, WebGL vendor/renderer,
  and device specs. The real bundled-Chromium user agent is used as-is (a hand-rolled UA with a
  mismatched version is itself a tell).
- **Human-like behaviour** — variable jittered delays, small randomized mouse moves and scrolls
  on every page, in addition to the per-task page/keyword delays.
- **Capsolver (optional)** — set a Capsolver API key in **Settings**. When a reCAPTCHA /
  hCaptcha / Cloudflare Turnstile is detected, the token is solved remotely, injected, and the
  challenge submitted, then scraping continues. Uses
  [python3-capsolver](https://github.com/AndreiDrang/python3-capsolver). The key is encrypted at
  rest.
- **Proxies** — for **Google** especially, a datacenter IP will still get blocked; configure a
  **residential proxy** per task. Bing/DuckDuckGo usually work without one once headful is on.

If an engine is blocked mid-run, results already collected are kept and the task completes with
a note rather than failing outright.

## Security notes

- `APP_SECRET_KEY` is a Fernet key used to encrypt SMTP password and proxy credentials at rest.
  Losing it makes those values unrecoverable; the app refuses to start without one.
- API tokens are stored as SHA-256 hashes; the raw value is displayed once on creation.
- The SPA uses a single internal "UI bootstrap" token cached on disk under `data/.ui_token`.
  `GET /api/ui-token` is gated to the configured `UI_HOSTNAME` (plus `localhost` for dev)
  — protect it with your webauth proxy.
- Database credentials are read from `DATABASE_URL` and never stored in the app DB.

## Local development (without Docker)

Requires Python 3.12+ and a running PostgreSQL instance.

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
export DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/serp
export APP_SECRET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
alembic upgrade head
uvicorn app.main:app --reload
```

## License

Choose your own — none included.
