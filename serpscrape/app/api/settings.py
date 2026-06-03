from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.auth import require_token
from app.crypto import encrypt
from app.db import get_session
from app.models import Setting
from app.schemas import SettingsIn, SettingsOut

router = APIRouter(prefix="/api/settings", tags=["settings"])

# (key, is_encrypted)
_FIELDS: dict[str, bool] = {
    "default_notify_email": False,
    "smtp_host": False,
    "smtp_port": False,
    "smtp_username": False,
    "smtp_password": True,
    "smtp_from": False,
    "smtp_starttls": False,
    "capsolver_api_key": True,
}


async def _read(session: AsyncSession) -> dict[str, str | None]:
    rows = (await session.execute(select(Setting))).scalars().all()
    return {r.key: r.value for r in rows}


@router.get("", response_model=SettingsOut, dependencies=[Depends(require_token)])
async def get_settings(session: AsyncSession = Depends(get_session)) -> SettingsOut:
    values = await _read(session)
    return SettingsOut(
        default_notify_email=values.get("default_notify_email"),
        smtp_host=values.get("smtp_host"),
        smtp_port=int(values["smtp_port"]) if values.get("smtp_port") else None,
        smtp_username=values.get("smtp_username"),
        smtp_password_set=bool(values.get("smtp_password")),
        smtp_from=values.get("smtp_from"),
        smtp_starttls=(values.get("smtp_starttls") or "true").lower() != "false",
        capsolver_api_key_set=bool(values.get("capsolver_api_key")),
    )


@router.put("", response_model=SettingsOut, dependencies=[Depends(require_token)])
async def update_settings(
    payload: SettingsIn,
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    data = payload.model_dump(exclude_unset=True)
    for key, raw in data.items():
        encrypted = _FIELDS.get(key, False)
        value: str | None
        if raw is None:
            value = None
        elif isinstance(raw, bool):
            value = "true" if raw else "false"
        else:
            value = str(raw)
        stored = encrypt(value) if (encrypted and value is not None) else value
        stmt = (
            insert(Setting)
            .values(key=key, value=stored, encrypted=encrypted and value is not None)
            .on_conflict_do_update(
                index_elements=[Setting.key],
                set_={"value": stored, "encrypted": encrypted and value is not None},
            )
        )
        await session.execute(stmt)
    await session.commit()
    return await get_settings(session)
