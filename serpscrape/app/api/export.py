from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_token
from app.db import get_session
from app.models import Task, TaskResult

router = APIRouter(prefix="/api/tasks", tags=["export"])

_HEADERS = ["engine", "keyword", "position", "title", "url", "description", "scraped_at"]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")[:60] or "task"


async def _fetch(session: AsyncSession, task_id: int, engine: str | None, keyword: str | None):
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    q = select(TaskResult).where(TaskResult.task_id == task_id)
    if engine:
        q = q.where(TaskResult.engine == engine)
    if keyword:
        q = q.where(TaskResult.keyword == keyword)
    rows = (
        await session.execute(
            q.order_by(TaskResult.engine, TaskResult.keyword, TaskResult.position)
        )
    ).scalars().all()
    return t, rows


@router.get("/{task_id}/export", dependencies=[Depends(require_token)])
async def export_results(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    fmt: str = Query(default="csv", alias="format", pattern="^(csv|xlsx|tsv)$"),
    engine: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> StreamingResponse:
    task, rows = await _fetch(session, task_id, engine, keyword)
    base = f"task_{task_id}_{_safe_name(task.name)}"

    if fmt in ("csv", "tsv"):
        delimiter = "\t" if fmt == "tsv" else ","
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=delimiter)
        writer.writerow(_HEADERS)
        for r in rows:
            writer.writerow(
                [
                    r.engine,
                    r.keyword,
                    r.position,
                    r.title or "",
                    r.url,
                    r.description or "",
                    r.scraped_at.isoformat() if r.scraped_at else "",
                ]
            )
        if fmt == "tsv":
            # text/plain so the client can read it and copy to clipboard for Sheets.
            return StreamingResponse(
                io.BytesIO(buf.getvalue().encode("utf-8")),
                media_type="text/plain; charset=utf-8",
            )
        data = buf.getvalue().encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
        )

    # xlsx
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "results"
    ws.append(_HEADERS)
    for r in rows:
        ws.append(
            [
                r.engine,
                r.keyword,
                r.position,
                r.title or "",
                r.url,
                r.description or "",
                r.scraped_at.isoformat() if r.scraped_at else "",
            ]
        )
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'},
    )
