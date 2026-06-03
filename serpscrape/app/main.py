from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import export as export_api
from app.api import misc as misc_api
from app.api import results as results_api
from app.api import settings as settings_api
from app.api import tasks as tasks_api
from app.api import tokens as tokens_api
from app.config import get_settings
from app.worker.queue import queue_loop, recover_on_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
)
log = logging.getLogger("app.main")

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate critical config eagerly
    s = get_settings()
    log.info("starting SERP scraper (ui_hostname=%s)", s.ui_hostname)
    # Make sure encryption key is usable
    from app.crypto import _fernet  # type: ignore
    _fernet()

    await recover_on_startup()

    stop = asyncio.Event()
    worker_task = asyncio.create_task(queue_loop(stop), name="queue-loop")
    try:
        yield
    finally:
        log.info("shutting down worker...")
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except asyncio.TimeoutError:
            worker_task.cancel()


app = FastAPI(
    title="SERP Scraper",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.include_router(tasks_api.router)
app.include_router(results_api.router)
app.include_router(export_api.router)
app.include_router(settings_api.router)
app.include_router(tokens_api.router)
app.include_router(tokens_api.ui_router)
app.include_router(misc_api.router)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


# Static assets (app.js, styles.css). The index is served above for the root.
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.exception_handler(Exception)
async def _unhandled(_, exc):  # type: ignore[no-untyped-def]
    log.exception("unhandled exception in request")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
