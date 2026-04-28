"""Phase 4.12 — POST /waitlist (ADR-0035 §«Lead capture»).

Один эндпоинт: принимает email + опциональную locale, идемпотентно
кладёт в `waitlist_entries`. Без auth — это публичная маркетинговая
форма с лендинга. Анти-abuse:

* email — Pydantic ``EmailStr`` валидирует формат на сервере.
* unique constraint на email возвращает 200 без mutation, чтобы
  бот, повторно нажимающий submit, не получал инфу «уже подписан».
* rate-limit подключим в Phase 4.13 / Phase 13.x (Cloud Armor).
"""

from __future__ import annotations

import logging
from typing import Annotated, Final

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from shared_models.orm import WaitlistEntry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


class WaitlistJoinRequest(BaseModel):
    """POST body — email + опциональные telemetry-метки."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    locale: str | None = Field(default=None, max_length=16)
    source: str | None = Field(default=None, max_length=32)


class WaitlistJoinResponse(BaseModel):
    """Идемпотентный ack: всегда `{"ok": true}`, не утекает «новый ли email»."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True


@router.post(
    "/waitlist",
    response_model=WaitlistJoinResponse,
    status_code=status.HTTP_200_OK,
    tags=["waitlist"],
)
async def join_waitlist(
    payload: WaitlistJoinRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WaitlistJoinResponse:
    """Идемпотентно добавить email в waitlist.

    Если запись с таким email уже есть — no-op (200), без подсказки
    «уже подписан» (anti-enumeration).
    """
    # Нормализуем — храним lowercase, чтобы duplicate-проверка работала
    # с Mary@Foo и mary@foo как один и тот же email.
    email_norm = payload.email.lower()

    existing = await session.execute(
        select(WaitlistEntry.id).where(WaitlistEntry.email == email_norm)
    )
    if existing.scalar_one_or_none() is not None:
        return WaitlistJoinResponse()

    entry = WaitlistEntry(
        email=email_norm,
        locale=payload.locale,
        source=payload.source,
    )
    session.add(entry)
    await session.flush()
    _LOG.info("waitlist join: locale=%s source=%s", payload.locale, payload.source)
    # Email НЕ логируем — privacy-by-design (CLAUDE.md §3.5).

    return WaitlistJoinResponse()
