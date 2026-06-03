from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import hash_token
from app.db import get_session
from app.models import ApiToken


async def require_token(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApiToken:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    raw = authorization.split(None, 1)[1].strip()
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Empty bearer token")
    digest = hash_token(raw)
    row = (
        await session.execute(select(ApiToken).where(ApiToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked token")
    await session.execute(
        update(ApiToken)
        .where(ApiToken.id == row.id)
        .values(last_used_at=datetime.now(timezone.utc))
    )
    await session.commit()
    return row
