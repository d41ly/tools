from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import asc as sql_asc
from sqlalchemy import delete as sql_delete
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_token
from app.crypto import decrypt, encrypt
from app.db import get_session
from app.models import Setting, Task
from app.schemas import BulkDelete, TaskAction, TaskCreate, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_to_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        name=t.name,
        keywords=t.keywords,
        engines=t.engines,
        country=t.country,
        per_page_delay_ms=t.per_page_delay_ms,
        per_keyword_delay_ms=t.per_keyword_delay_ms,
        max_results=t.max_results,
        notify_email=t.notify_email,
        has_proxy=bool(t.proxy_config),
        status=t.status,
        error_message=t.error_message,
        progress=t.progress or {},
        created_at=t.created_at,
        started_at=t.started_at,
        completed_at=t.completed_at,
    )


def _autoname(payload: TaskCreate) -> str:
    head = payload.keywords[0]
    extra = f" (+{len(payload.keywords) - 1} more)" if len(payload.keywords) > 1 else ""
    engines = "/".join(payload.engines)
    return f"{engines} | {head}{extra}"[:255]


async def _default_notify_email(session: AsyncSession) -> str | None:
    row = (
        await session.execute(
            select(Setting.value, Setting.encrypted).where(Setting.key == "default_notify_email")
        )
    ).first()
    if not row or row[0] is None:
        return None
    return row[0]  # not encrypted


async def _default_proxy(session: AsyncSession) -> tuple[str | None, str | None]:
    """Returns (default_proxy_server, decrypted default_proxy_password)."""
    rows = (
        await session.execute(
            select(Setting).where(
                Setting.key.in_(["default_proxy_server", "default_proxy_password"])
            )
        )
    ).scalars().all()
    server = password = None
    for r in rows:
        if r.value is None:
            continue
        if r.key == "default_proxy_server":
            server = r.value
        elif r.key == "default_proxy_password":
            password = decrypt(r.value) if r.encrypted else r.value
    return server, password


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_token)])
async def create_task(payload: TaskCreate, session: AsyncSession = Depends(get_session)) -> TaskOut:
    notify = payload.notify_email or await _default_notify_email(session)
    proxy_enc = None
    if payload.proxy is not None:
        proxy = payload.proxy.model_dump()
        # The New Task form pre-fills the saved default proxy's server/username but
        # never the (encrypted) password. If the password is blank and the server
        # matches the configured default, substitute the saved password here.
        if not proxy.get("password") and proxy.get("server"):
            d_server, d_password = await _default_proxy(session)
            if d_password and proxy["server"] == d_server:
                proxy["password"] = d_password
        proxy_enc = encrypt(json.dumps(proxy))
    t = Task(
        name=payload.name or _autoname(payload),
        keywords=payload.keywords,
        engines=payload.engines,
        country=payload.country,
        proxy_config=proxy_enc,
        per_page_delay_ms=payload.per_page_delay_ms,
        per_keyword_delay_ms=payload.per_keyword_delay_ms,
        max_results=payload.max_results,
        notify_email=notify,
        status="queued",
        progress={"done": 0, "total": len(payload.keywords) * len(payload.engines)},
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return _task_to_out(t)


@router.get("", response_model=dict, dependencies=[Depends(require_token)])
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="search task name (case-insensitive)"),
    sort: str = Query(default="created_at", pattern="^(created_at|status)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    filters = []
    if status_filter:
        filters.append(Task.status == status_filter)
    if q:
        filters.append(Task.name.ilike(f"%{q.strip()}%"))
    if created_after:
        filters.append(Task.created_at >= created_after)
    if created_before:
        filters.append(Task.created_at < created_before)

    base = select(Task)
    count_q = select(func.count()).select_from(Task)
    for f in filters:
        base = base.where(f)
        count_q = count_q.where(f)

    sort_col = Task.status if sort == "status" else Task.created_at
    direction = sql_asc if order == "asc" else desc
    # stable secondary order by id so equal sort keys paginate deterministically
    base = base.order_by(direction(sort_col), desc(Task.id))

    total = (await session.execute(count_q)).scalar_one()
    rows = (await session.execute(base.limit(limit).offset(offset))).scalars().all()
    return {
        "total": int(total),
        "items": [_task_to_out(t).model_dump(mode="json") for t in rows],
    }


@router.get("/{task_id}", response_model=TaskOut, dependencies=[Depends(require_token)])
async def get_task(task_id: int, session: AsyncSession = Depends(get_session)) -> TaskOut:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    return _task_to_out(t)


@router.patch("/{task_id}", response_model=TaskOut, dependencies=[Depends(require_token)])
async def control_task(
    task_id: int, payload: TaskAction, session: AsyncSession = Depends(get_session)
) -> TaskOut:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    action = payload.action
    if action == "pause":
        if t.status not in ("queued", "running"):
            raise HTTPException(409, f"Cannot pause from status '{t.status}'")
        t.status = "paused"
    elif action == "resume":
        if t.status != "paused":
            raise HTTPException(409, f"Cannot resume from status '{t.status}'")
        t.status = "queued"
    elif action == "cancel":
        if t.status in ("completed", "canceled", "failed"):
            raise HTTPException(409, f"Cannot cancel from status '{t.status}'")
        # If running, the runner will observe and finalize. If queued/paused, finalize here.
        if t.status in ("queued", "paused"):
            t.status = "canceled"
            t.completed_at = datetime.now(timezone.utc)
        else:
            t.status = "canceled"
    await session.commit()
    await session.refresh(t)
    return _task_to_out(t)


@router.post("/bulk-delete", response_model=dict, dependencies=[Depends(require_token)])
async def bulk_delete(payload: BulkDelete, session: AsyncSession = Depends(get_session)) -> dict:
    # Cancel any still-active rows first so the worker stops touching them, then
    # delete. task_results rows are removed via ON DELETE CASCADE.
    await session.execute(
        update(Task)
        .where(Task.id.in_(payload.ids), Task.status.in_(("queued", "running", "paused")))
        .values(status="canceled", completed_at=datetime.now(timezone.utc))
    )
    res = await session.execute(sql_delete(Task).where(Task.id.in_(payload.ids)))
    await session.commit()
    return {"deleted": int(res.rowcount or 0)}


@router.delete("/{task_id}", status_code=204, response_class=Response, dependencies=[Depends(require_token)])
async def delete_task(task_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    await session.delete(t)
    await session.commit()
    return Response(status_code=204)
