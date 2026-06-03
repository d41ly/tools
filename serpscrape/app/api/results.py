from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_token
from app.db import get_session
from app.models import Task, TaskResult
from app.schemas import ResultOut

router = APIRouter(prefix="/api/tasks", tags=["results"])


@router.get("/{task_id}/results", response_model=dict, dependencies=[Depends(require_token)])
async def list_results(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    engine: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    q = select(TaskResult).where(TaskResult.task_id == task_id)
    count_q = select(func.count()).select_from(TaskResult).where(TaskResult.task_id == task_id)
    if engine:
        q = q.where(TaskResult.engine == engine)
        count_q = count_q.where(TaskResult.engine == engine)
    if keyword:
        q = q.where(TaskResult.keyword == keyword)
        count_q = count_q.where(TaskResult.keyword == keyword)
    total = (await session.execute(count_q)).scalar_one()
    rows = (
        await session.execute(
            q.order_by(TaskResult.engine, TaskResult.keyword, TaskResult.position)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "total": int(total),
        "items": [ResultOut.model_validate(r).model_dump(mode="json") for r in rows],
    }


@router.get("/{task_id}/summary", response_model=dict, dependencies=[Depends(require_token)])
async def results_summary(
    task_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    rows = (
        await session.execute(
            select(TaskResult.engine, TaskResult.keyword, func.count(TaskResult.id))
            .where(TaskResult.task_id == task_id)
            .group_by(TaskResult.engine, TaskResult.keyword)
            .order_by(TaskResult.engine, TaskResult.keyword)
        )
    ).all()
    return {
        "groups": [
            {"engine": eng, "keyword": kw, "count": int(n)} for eng, kw, n in rows
        ]
    }
