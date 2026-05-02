"""Liveness probe."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Простая проверка что приложение запущено."""
    return {"status": "ok"}
