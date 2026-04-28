"""Liveness probe + Resend reachability (Phase 12.2).

``/healthz`` — лёгкий liveness probe для Cloud Run + docker-compose.
Возвращает 200 если приложение запущено. Resend reachability
кэшируется 30 секунд, чтобы каждый probe не дёргал внешний API.
"""

from __future__ import annotations

import time
from typing import Annotated, Final

import httpx
from fastapi import APIRouter, Depends

from email_service.config import Settings, get_settings

router = APIRouter()

_CACHE_TTL_SECONDS: Final = 30.0
_RESEND_HEALTH_PATH: Final = "/domains"  # любой authenticated GET-endpoint
_resend_check_cache: dict[str, float | bool] = {"ts": 0.0, "ok": True}


async def _check_resend(settings: Settings) -> bool:
    """Reachability-чек Resend: cached 30s.

    Использует ``GET /domains`` как cheap-call. 401/403 — всё равно
    «реально reachable, key invalid» считаем ok=true (alert приходит
    отдельно из send-failures).
    """
    now = time.monotonic()
    cached_ts = float(_resend_check_cache["ts"])
    if now - cached_ts < _CACHE_TTL_SECONDS:
        return bool(_resend_check_cache["ok"])

    if not settings.resend_api_key:
        _resend_check_cache.update({"ts": now, "ok": True})
        return True

    try:
        async with httpx.AsyncClient(
            base_url="https://api.resend.com",
            timeout=settings.resend_timeout_seconds,
        ) as client:
            resp = await client.get(
                _RESEND_HEALTH_PATH,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
        ok = bool(resp.status_code < 500)
    except httpx.HTTPError:
        ok = False

    _resend_check_cache.update({"ts": now, "ok": ok})
    return ok


@router.get("/healthz", tags=["meta"])
async def healthz(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Liveness + Resend reachability."""
    resend_ok = await _check_resend(settings)
    return {
        "status": "ok",
        "resend_reachable": resend_ok,
        "billing_enabled": settings.enabled,
    }
