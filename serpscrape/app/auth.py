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
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    session: AsyncSession = Depends(get_session),
) -> ApiToken:
    # The SPA sends the token via X-API-Token (NOT Authorization) so it doesn't
    # clobber an upstream HTTP Basic Auth header (nginx auth_basic) — sending
    # `Authorization: Bearer` would replace the browser's cached Basic credentials
    # and make the proxy re-prompt for auth endlessly. External API clients may
    # still use the standard `Authorization: Bearer <token>` header.
    raw: str | None = None
    if x_api_token:
        raw = x_api_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(None, 1)[1].strip()
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API token")
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
