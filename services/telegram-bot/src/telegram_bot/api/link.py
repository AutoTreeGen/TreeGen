"""POST /telegram/link/confirm — consume one-time token, attach chat to user.

Phase 14.0 trust model: caller (api-gateway) уже проверил Clerk-JWT и
передаёт ``user_id`` в body. Phase 14.x добавит machine-token проверку
самого api-gateway. См. ADR-0040 §«Account linking flow».
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from shared_models.orm import TelegramUserLink
from shared_models.types import new_uuid
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_bot.config import Settings, get_settings
from telegram_bot.database import get_session
from telegram_bot.schemas import LinkConfirmRequest, LinkConfirmResponse
from telegram_bot.services.link_tokens import LinkTokenStore

router = APIRouter(prefix="/telegram")
_LOG: Final = logging.getLogger(__name__)

# Singleton-инстансы; init в main.lifespan, override в тестах.
_redis: Redis | None = None
_link_tokens: LinkTokenStore | None = None


def set_link_tokens(store: LinkTokenStore | None) -> None:
    """Тестовая утилита: подменить ``LinkTokenStore``."""
    global _link_tokens  # noqa: PLW0603
    _link_tokens = store


def set_redis(client: Redis | None) -> None:
    """Тестовая утилита: подменить Redis-клиент (используется lifespan'ом)."""
    global _redis  # noqa: PLW0603
    _redis = client


def get_redis() -> Redis:
    """Достать Redis-клиент или ``RuntimeError``."""
    if _redis is None:
        msg = "Redis client not initialized; call set_redis() or lifespan startup."
        raise RuntimeError(msg)
    return _redis


def get_link_tokens() -> LinkTokenStore:
    """Достать ``LinkTokenStore`` или ``RuntimeError``."""
    if _link_tokens is None:
        msg = "LinkTokenStore not initialized; call set_link_tokens() or lifespan."
        raise RuntimeError(msg)
    return _link_tokens


@router.post(
    "/link/confirm",
    response_model=LinkConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm Telegram account link (one-time token)",
)
async def confirm_link(
    body: LinkConfirmRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],  # noqa: ARG001
    link_tokens: Annotated[LinkTokenStore, Depends(get_link_tokens)],
) -> LinkConfirmResponse:
    """Consume one-time link-token и создать ``telegram_user_links`` row."""
    payload = await link_tokens.consume(body.token)
    if payload is None:
        # 410 Gone — токен истёк, уже использован, или не существовал.
        # 404 был бы менее точен (мы не различаем «не было» от «exhausted»).
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Link token expired, already used, or invalid.",
        )

    # Проверяем, не привязан ли уже этот chat к другому user'у. Это
    # покрывается UNIQUE constraint'ом (`uq_telegram_user_links_tg_chat_id`),
    # но явная проверка — better error message и lower error-rate в логах.
    existing = await session.execute(
        select(TelegramUserLink).where(
            TelegramUserLink.tg_chat_id == payload.tg_chat_id,
            TelegramUserLink.revoked_at.is_(None),
        ),
    )
    duplicate = existing.scalar_one_or_none()
    if duplicate is not None and duplicate.user_id != body.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram chat already linked to another user.",
        )
    if duplicate is not None and duplicate.user_id == body.user_id:
        # Повторный confirm для того же user'а — идемпотентно возвращаем
        # существующую запись (чтобы клиент мог retry'ить безопасно).
        return _to_response(duplicate)

    now = dt.datetime.now(dt.UTC)
    # Generate id eagerly (а не через mapped_column default=) — это позволяет
    # вернуть link_id в response без дополнительного flush'а и упрощает
    # unit-тесты, где session замокан.
    link = TelegramUserLink(
        id=new_uuid(),
        user_id=body.user_id,
        tg_chat_id=payload.tg_chat_id,
        tg_user_id=payload.tg_user_id,
        linked_at=now,
    )
    session.add(link)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Race с другим concurrent /confirm на тот же chat_id —
        # UNIQUE constraint поймал. Возвращаем 409 Conflict.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Link conflict — concurrent link request, please retry.",
        ) from exc

    return _to_response(link)


def _to_response(link: TelegramUserLink) -> LinkConfirmResponse:
    """Сериализовать ORM-row в ответ."""
    return LinkConfirmResponse(
        link_id=link.id,
        user_id=link.user_id,
        tg_chat_id=link.tg_chat_id,
        linked_at=link.linked_at.isoformat(),
    )
