#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] waiting for database..."
python - <<'PY'
import os, time, socket
from urllib.parse import urlparse
url = urlparse(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
host, port = url.hostname, url.port or 5432
deadline = time.time() + 60
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] db reachable at {host}:{port}")
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit(f"[entrypoint] db unreachable at {host}:{port}")
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
