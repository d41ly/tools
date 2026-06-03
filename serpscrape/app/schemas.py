from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Engine = Literal["google", "bing", "duckduckgo"]
TaskStatus = Literal["queued", "running", "paused", "completed", "canceled", "failed"]


class ProxyConfig(BaseModel):
    server: str = Field(..., description="e.g. http://1.2.3.4:8080 or socks5://...")
    username: str | None = None
    password: str | None = None


class TaskCreate(BaseModel):
    name: str | None = None
    keywords: list[str] = Field(..., min_length=1, max_length=200)
    engines: list[Engine] = Field(..., min_length=1)
    country: str = Field(default="US", min_length=2, max_length=2)
    per_page_delay_ms: int = Field(default=1500, ge=0, le=600_000)
    per_keyword_delay_ms: int = Field(default=5000, ge=0, le=3_600_000)
    max_results: int = Field(default=100, ge=1, le=100)
    notify_email: EmailStr | None = None
    proxy: ProxyConfig | None = None

    @field_validator("keywords")
    @classmethod
    def _strip_keywords(cls, v: list[str]) -> list[str]:
        out = [k.strip() for k in v if k.strip()]
        if not out:
            raise ValueError("At least one non-empty keyword required")
        return out

    @field_validator("country")
    @classmethod
    def _upper_country(cls, v: str) -> str:
        return v.upper()

    @field_validator("engines")
    @classmethod
    def _dedupe_engines(cls, v: list[str]) -> list[str]:
        seen, out = set(), []
        for e in v:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out


class TaskOut(BaseModel):
    id: int
    name: str
    keywords: list[str]
    engines: list[str]
    country: str
    per_page_delay_ms: int
    per_keyword_delay_ms: int
    max_results: int
    notify_email: str | None
    has_proxy: bool
    status: TaskStatus
    error_message: str | None
    progress: dict
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TaskAction(BaseModel):
    action: Literal["pause", "resume", "cancel"]


class BulkDelete(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=1000)


class ResultOut(BaseModel):
    id: int
    engine: str
    keyword: str
    position: int
    title: str | None
    description: str | None
    url: str
    scraped_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class TokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TokenCreated(TokenOut):
    token: str


class SettingsIn(BaseModel):
    default_notify_email: EmailStr | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: EmailStr | None = None
    smtp_starttls: bool | None = None
    capsolver_api_key: str | None = None


class SettingsOut(BaseModel):
    default_notify_email: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password_set: bool = False
    smtp_from: str | None = None
    smtp_starttls: bool = True
    capsolver_api_key_set: bool = False
