from __future__ import annotations

from fastapi import APIRouter

from app.scrapers.geo import countries

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/countries")
async def list_countries() -> list[dict[str, str]]:
    return countries()


@router.get("/engines")
async def list_engines() -> list[str]:
    return ["google", "bing", "duckduckgo"]


@router.get("/health")
async def health() -> dict:
    return {"ok": True}
