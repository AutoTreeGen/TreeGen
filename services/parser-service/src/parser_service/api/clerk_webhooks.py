"""Clerk webhook receiver (Phase 4.10, ADR-0033).

Clerk шлёт Svix-подписанные webhook'и на ``user.created`` /
``user.updated`` / ``user.deleted`` события. Этот endpoint:

* Верифицирует подпись (Svix headers ``svix-id``, ``svix-timestamp``,
  ``svix-signature``) против секрета из ENV.
* Идемпотентно мапит payload → ``users``-row (create/update/soft-delete).

JIT-create в :mod:`parser_service.services.user_sync` остаётся primary
flow (первый user-API-вызов сразу делает row); webhook — secondary
canonical (бэкфил email/display_name из Clerk dashboard, если они
изменились вне нашего pipeline).

503 если ``clerk_webhook_secret`` пуст в env — иначе любой каркас
без секрета молча "принимал" бы webhook'и без подписи.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from shared_models.orm import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from parser_service.config import Settings, get_settings
from parser_service.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter()


def _extract_email(payload: dict[str, Any]) -> str | None:
    """Достать primary email из Clerk webhook user-object'а.

    Структура: ``data.email_addresses`` — list of {id, email_address, ...},
    ``data.primary_email_address_id`` — pointer на primary item.
    """
    data = payload.get("data") or {}
    primary_id = data.get("primary_email_address_id")
    addresses = data.get("email_addresses") or []
    if not isinstance(addresses, list):
        return None
    for addr in addresses:
        if not isinstance(addr, dict):
            continue
        if addr.get("id") == primary_id:
            email = addr.get("email_address")
            if isinstance(email, str) and email:
                return email
    # fallback: первый из списка.
    for addr in addresses:
        if isinstance(addr, dict):
            email = addr.get("email_address")
            if isinstance(email, str) and email:
                return email
    return None


def _extract_display_name(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") or {}
    first = data.get("first_name") or ""
    last = data.get("last_name") or ""
    full = f"{first} {last}".strip()
    return full or None


def _verify_svix_signature(
    *,
    secret: str,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    body: bytes,
) -> None:
    """Проверить Svix HMAC-подпись webhook'а.

    Импорт ``svix`` — лениво, потому что библиотека опциональная: в
    тестах используется fake-flow без подписи (см. fixture
    ``clerk_webhook_no_signature_check`` в parser-service tests).

    Raises:
        HTTPException 401, если подпись отсутствует или не совпала.
    """
    if not (svix_id and svix_timestamp and svix_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Svix signature headers",
        )

    headers = {
        "svix-id": svix_id,
        "svix-timestamp": svix_timestamp,
        "svix-signature": svix_signature,
    }
    try:
        Webhook(secret).verify(body, headers)
    except WebhookVerificationError as exc:
        logger.warning("Clerk webhook signature verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature verification failed",
        ) from exc


@router.post(
    "/webhooks/clerk",
    status_code=status.HTTP_200_OK,
    summary="Clerk webhook: user.created / user.updated / user.deleted",
)
async def clerk_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[str | None, Header(alias="svix-timestamp")] = None,
    svix_signature: Annotated[str | None, Header(alias="svix-signature")] = None,
) -> dict[str, str]:
    """Принять Clerk webhook и обновить локальный ``users`` row.

    * 503 — если ``clerk_webhook_secret`` не задан (явная защита от
      молчаливого принятия unsigned webhook'ов).
    * 401 — если подпись не прошла верификацию.
    * 200 — на любой обработанный payload (включая no-op для unknown
      типов событий — Clerk не должен ретраить).
    """
    if not settings.clerk_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Clerk webhook receiver disabled: PARSER_SERVICE_CLERK_WEBHOOK_SECRET is empty."
            ),
        )

    body = await request.body()
    _verify_svix_signature(
        secret=settings.clerk_webhook_secret,
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
        body=body,
    )

    payload = await request.json()
    event_type = payload.get("type")
    data = payload.get("data") or {}
    clerk_user_id = data.get("id")
    if not isinstance(clerk_user_id, str) or not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clerk webhook payload missing data.id",
        )

    if event_type in {"user.created", "user.updated"}:
        await _upsert_user_from_payload(session, payload=payload, clerk_user_id=clerk_user_id)
    elif event_type == "user.deleted":
        await _soft_delete_user(session, clerk_user_id=clerk_user_id)
    else:
        logger.info("Clerk webhook ignored: unknown event_type=%s", event_type)
    return {"status": "ok"}


async def _upsert_user_from_payload(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    clerk_user_id: str,
) -> None:
    """Insert или update users-row на ``user.created`` / ``user.updated``."""
    email = _extract_email(payload)
    display_name = _extract_display_name(payload)

    existing = (
        await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    ).scalar_one_or_none()
    if existing is None:
        # Минимальный валидный row: email и external_auth_id NOT NULL.
        # Если Clerk не прислал email (редко), fallback на placeholder.
        row_email = email or f"{clerk_user_id}@clerk.local"
        existing = User(
            email=row_email,
            external_auth_id=f"clerk:{clerk_user_id}",
            clerk_user_id=clerk_user_id,
            display_name=display_name,
            locale="en",
        )
        session.add(existing)
    else:
        if email and existing.email != email:
            existing.email = email
        if display_name and existing.display_name != display_name:
            existing.display_name = display_name
    await session.flush()


async def _soft_delete_user(session: AsyncSession, *, clerk_user_id: str) -> None:
    """Soft-delete users row на Clerk ``user.deleted`` event.

    ``deleted_at = NOW()`` через ORM mixin'а; downstream-tree-data
    остаётся (FK с ``ondelete='RESTRICT'``), пока user-инициированный
    GDPR-flow не удалит её явно.
    """
    existing = (
        await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    ).scalar_one_or_none()
    if existing is None:
        # Ничего не сделать — webhook пришёл на user'а, которого мы
        # никогда не видели (например, Clerk-only registration без
        # API-вызова). 200 OK, idempotent no-op.
        return
    if existing.deleted_at is None:
        existing.deleted_at = dt.datetime.now(dt.UTC)
        await session.flush()


__all__ = ["router"]
