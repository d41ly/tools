from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt
from app.db import SessionLocal, session_scope
from app.models import Task, TaskResult
from app.scrapers import SCRAPERS, CaptchaError
from app.scrapers.base import ScrapeContext, human_sleep

log = logging.getLogger("worker.runner")


class TaskInterrupted(Exception):
    """Raised internally when the worker observes a pause/cancel during execution."""

    def __init__(self, new_status: str, message: str | None = None) -> None:
        self.new_status = new_status
        self.message = message


async def _current_status(session: AsyncSession, task_id: int) -> str | None:
    return (
        await session.execute(select(Task.status).where(Task.id == task_id))
    ).scalar_one_or_none()


async def _check_control(task_id: int) -> None:
    async with SessionLocal() as s:
        status = await _current_status(s, task_id)
    if status == "paused":
        raise TaskInterrupted("paused")
    if status == "canceled":
        raise TaskInterrupted("canceled")
    if status is None:
        raise TaskInterrupted("canceled", "Task deleted")


async def _set_progress(task_id: int, done: int, total: int, current: str | None = None) -> None:
    async with session_scope() as s:
        await s.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(progress={"done": done, "total": total, "current": current})
        )


async def run_task(task_id: int) -> None:
    """Run a single task end-to-end. Caller is responsible for queue claim semantics."""
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return
        if task.status not in ("queued", "paused"):
            return
        task.status = "running"
        task.started_at = task.started_at or datetime.now(timezone.utc)
        task.error_message = None
        await s.commit()
        # Detach all needed attributes
        keywords = list(task.keywords)
        engines = list(task.engines)
        country = task.country
        proxy_enc = task.proxy_config
        per_page = task.per_page_delay_ms
        per_kw = task.per_keyword_delay_ms

    proxy = None
    if proxy_enc:
        try:
            proxy = json.loads(decrypt(proxy_enc))
        except Exception as exc:
            log.exception("Failed to decrypt proxy config for task %s", task_id)
            await _fail(task_id, f"proxy decryption failed: {exc}")
            return

    ctx = ScrapeContext(country=country, proxy=proxy, per_page_delay_ms=per_page)
    combos = [(kw, eng) for kw in keywords for eng in engines]
    total = len(combos)
    log.info("task %s starting: %d combos", task_id, total)

    try:
        last_kw = None
        for idx, (kw, eng) in enumerate(combos):
            await _check_control(task_id)
            if last_kw is not None and kw != last_kw and per_kw > 0:
                # delay between keywords (split across remaining time in chunks to be responsive)
                await _sleep_responsive(task_id, per_kw)
            last_kw = kw
            await _set_progress(task_id, idx, total, current=f"{eng}: {kw}")
            scraper_cls = SCRAPERS.get(eng)
            if scraper_cls is None:
                log.warning("Unknown engine %s on task %s, skipping", eng, task_id)
                continue
            scraper = scraper_cls()
            try:
                results = await scraper.scrape(kw, ctx)
            except CaptchaError as exc:
                await _fail(task_id, f"captcha encountered ({eng}/{kw}): {exc}")
                return
            except Exception as exc:
                log.exception("scrape failed task=%s engine=%s keyword=%s", task_id, eng, kw)
                await _fail(task_id, f"{eng}/{kw} failed: {exc.__class__.__name__}: {exc}")
                return
            if results:
                async with session_scope() as s:
                    s.add_all(
                        [
                            TaskResult(
                                task_id=task_id,
                                engine=eng,
                                keyword=kw,
                                position=r.position,
                                title=r.title,
                                description=r.description,
                                url=r.url,
                            )
                            for r in results
                        ]
                    )
        await _check_control(task_id)
        await _set_progress(task_id, total, total, current=None)
        async with session_scope() as s:
            await s.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(status="completed", completed_at=datetime.now(timezone.utc))
            )
        log.info("task %s completed", task_id)
        # Notify outside the transaction
        from app.notify.email import send_completion  # local import to avoid cycles
        try:
            await send_completion(task_id)
        except Exception:
            log.exception("notification failed for task %s", task_id)

    except TaskInterrupted as ti:
        async with session_scope() as s:
            if ti.new_status == "canceled":
                await s.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        status="canceled",
                        completed_at=datetime.now(timezone.utc),
                        error_message=ti.message,
                    )
                )
                log.info("task %s canceled", task_id)
            elif ti.new_status == "paused":
                # status already paused by user; leave it
                log.info("task %s paused", task_id)


async def _sleep_responsive(task_id: int, total_ms: int) -> None:
    """Sleep but check task control every ~1s so pause/cancel is observed quickly."""
    chunk = 1000
    remaining = total_ms
    while remaining > 0:
        step = min(chunk, remaining)
        await human_sleep(step)
        remaining -= step
        await _check_control(task_id)


async def _fail(task_id: int, message: str) -> None:
    async with session_scope() as s:
        await s.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(
                status="failed",
                completed_at=datetime.now(timezone.utc),
                error_message=message[:2000],
            )
        )
    log.warning("task %s failed: %s", task_id, message)
