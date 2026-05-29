from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_token
from app.config import get_settings
from app.crypto import generate_token
from app.db import get_session
from app.models import ApiToken
from app.schemas import TokenCreate, TokenCreated, TokenOut

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("", response_model=list[TokenOut], dependencies=[Depends(require_token)])
async def list_tokens(session: AsyncSession = Depends(get_session)) -> list[TokenOut]:
    rows = (
        await session.execute(
            select(ApiToken)
            .where(ApiToken.is_ui_bootstrap.is_(False))
            .order_by(desc(ApiToken.created_at))
        )
    ).scalars().all()
    return [TokenOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=TokenCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_token)],
)
async def create_token(
    payload: TokenCreate, session: AsyncSession = Depends(get_session)
) -> TokenCreated:
    raw, digest, prefix = generate_token()
    t = ApiToken(name=payload.name.strip(), token_hash=digest, prefix=prefix)
    session.add(t)
    await session.commit()
    await session.refresh(t)
    out = TokenOut.model_validate(t).model_dump()
    return TokenCreated(**out, token=raw)


@router.delete("/{token_id}", status_code=204, dependencies=[Depends(require_token)])
async def revoke_token(token_id: int, session: AsyncSession = Depends(get_session)) -> None:
    t = await session.get(ApiToken, token_id)
    if t is None:
        raise HTTPException(404, "Token not found")
    if t.is_ui_bootstrap:
        raise HTTPException(400, "Cannot revoke the UI bootstrap token")
    if t.revoked_at is None:
        await session.execute(
            update(ApiToken).where(ApiToken.id == token_id).values(revoked_at=datetime.now(timezone.utc))
        )
        await session.commit()


# --- UI bootstrap token endpoint -----------------------------------------

ui_router = APIRouter(prefix="/api", tags=["ui"])


async def _get_or_create_ui_token(session: AsyncSession) -> str:
    """Returns the raw UI bootstrap token. Stored at first creation in an env-stored
    cache file under data/, since hashed-only storage means we can't recover it.
    Strategy: create one row marked is_ui_bootstrap=True, write the raw token to
    /tmp-style path; if it's missing, rotate."""
    import os
    cache_path = "/srv/data/.ui_token"
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if raw.startswith("scrp_"):
                # Verify it still exists in DB and isn't revoked
                from app.crypto import hash_token
                digest = hash_token(raw)
                existing = (
                    await session.execute(
                        select(ApiToken).where(
                            ApiToken.token_hash == digest, ApiToken.is_ui_bootstrap.is_(True)
                        )
                    )
                ).scalar_one_or_none()
                if existing and existing.revoked_at is None:
                    return raw
    except Exception:
        pass

    # Revoke any prior bootstrap rows so we have exactly one active.
    await session.execute(
        update(ApiToken)
        .where(ApiToken.is_ui_bootstrap.is_(True), ApiToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    raw, digest, prefix = generate_token()
    t = ApiToken(name="ui-bootstrap", token_hash=digest, prefix=prefix, is_ui_bootstrap=True)
    session.add(t)
    await session.commit()
    os.makedirs("/srv/data", exist_ok=True)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(raw)
        os.chmod(cache_path, 0o600)
    except Exception:
        pass
    return raw


@ui_router.get("/ui-token")
async def ui_token(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """Returns a token usable by the SPA. Restricted to the configured UI hostname
    so it is only accessible from behind your webauth proxy."""
    expected = get_settings().ui_hostname.lower()
    host = (request.headers.get("host") or "").split(":")[0].lower()
    fwd_host = (request.headers.get("x-forwarded-host") or "").split(":")[0].lower()
    if expected not in ("*", "any") and host != expected and fwd_host != expected and host not in ("localhost", "127.0.0.1"):
        raise HTTPException(403, "UI token endpoint not available on this host")
    raw = await _get_or_create_ui_token(session)
    return {"token": raw}
