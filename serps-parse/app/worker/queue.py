from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text, update

from app.db import session_scope
from app.models import Task
from app.worker.runner import run_task

log = logging.getLogger("worker.queue")

_IDLE_SLEEP_SECONDS = 2.0


async def recover_on_startup() -> None:
    """Mark any 'running' tasks as failed because the process restarted mid-execution."""
    async with session_scope() as s:
        await s.execute(
            update(Task)
            .where(Task.status == "running")
            .values(status="failed", error_message="interrupted by server restart")
        )


async def _claim_next() -> int | None:
    """Atomically claim the oldest queued task. Returns its id, or None if none."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id FROM tasks WHERE status = 'queued' "
                    "ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
            )
        ).first()
        if row is None:
            return None
        task_id = int(row[0])
        # Mark running here so the row exits the queue immediately even before run_task sets it.
        # run_task itself will re-set status='running' and started_at.
        await s.execute(
            update(Task).where(Task.id == task_id).values(status="running")
        )
        return task_id


async def queue_loop(stop_event: asyncio.Event) -> None:
    log.info("queue loop started")
    while not stop_event.is_set():
        try:
            task_id = await _claim_next()
        except Exception:
            log.exception("queue claim failed")
            await asyncio.sleep(_IDLE_SLEEP_SECONDS)
            continue
        if task_id is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            await run_task(task_id)
        except Exception:
            log.exception("run_task crashed for %s", task_id)
            # Mark failed so it doesn't get re-claimed
            try:
                async with session_scope() as s:
                    await s.execute(
                        update(Task)
                        .where(Task.id == task_id, Task.status == "running")
                        .values(status="failed", error_message="internal worker error")
                    )
            except Exception:
                log.exception("could not mark %s failed", task_id)
    log.info("queue loop stopped")
