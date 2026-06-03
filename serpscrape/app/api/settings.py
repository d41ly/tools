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
    "default_per_page_delay_ms": False,
    "default_per_keyword_delay_ms": False,
    "default_max_results": False,
    "default_engines": False,
    "default_proxy_server": False,
    "default_proxy_username": False,
    "default_proxy_password": True,
}


def _int(values: dict[str, str | None], key: str, default: int) -> int:
    v = values.get(key)
    if v in (None, ""):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


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
        default_per_page_delay_ms=_int(values, "default_per_page_delay_ms", 1500),
        default_per_keyword_delay_ms=_int(values, "default_per_keyword_delay_ms", 5000),
        default_max_results=_int(values, "default_max_results", 50),
        default_engines=(
            [e for e in values["default_engines"].split(",") if e]
            if values.get("default_engines")
            else ["google"]
        ),
        default_proxy_server=values.get("default_proxy_server") or None,
        default_proxy_username=values.get("default_proxy_username") or None,
        default_proxy_password_set=bool(values.get("default_proxy_password")),
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
        elif isinstance(raw, list):
            value = ",".join(str(x) for x in raw)
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
