#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] waiting for database..."
# Probe with the REAL driver (asyncpg) and the actual DSN — the same path the app
# uses — instead of a raw TCP socket. This validates routing, auth, and pg_hba, and
# prints the real error each attempt so failures are diagnosable rather than a bare
# "unreachable". Override the window with DB_WAIT_SECONDS (default 60).
python - <<'PY'
import asyncio, os, re, sys, time
import asyncpg

dsn = re.sub(r"\+asyncpg", "", os.environ["DATABASE_URL"], count=1)  # libpq DSN
wait = int(os.environ.get("DB_WAIT_SECONDS", "60"))
deadline = time.time() + wait
last = None

async def probe():
    conn = await asyncpg.connect(dsn=dsn, timeout=5)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()

attempt = 0
while time.time() < deadline:
    attempt += 1
    try:
        asyncio.run(probe())
        print("[entrypoint] database connection OK", flush=True)
        sys.exit(0)
    except Exception as e:
        last = e
        print(f"[entrypoint] db not ready (attempt {attempt}): {type(e).__name__}: {e}", flush=True)
        time.sleep(2)

print(f"[entrypoint] giving up after {wait}s — last error: {type(last).__name__}: {last}", file=sys.stderr, flush=True)
sys.exit(1)
PY

echo "[entrypoint] running migrations..."
alembic upgrade head

# Start a virtual X display so Chromium can run headful (anti-detection).
echo "[entrypoint] starting Xvfb on :99..."
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
sleep 1

echo "[entrypoint] starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
