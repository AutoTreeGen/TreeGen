"""Stripe webhook endpoint (Phase 12.0).

Жёсткие требования (см. ADR-0042 §«Webhook security»):

1. **Signature verification** обязательна — валидируем ``Stripe-Signature``
   header против raw-body **до** парсинга JSON. Любая невалидная подпись
   → 400 без обработки. ``stripe.Webhook.construct_event`` делает это
   за нас (constant-time HMAC, retry tolerance window).
2. **Idempotency** — каждый ``stripe_event_id`` обрабатывается ровно
   один раз. Повторный event (Stripe at-least-once) → 200 OK без
   side-effects.
3. **Resilience** — exception в обработчике помечает event как FAILED
   и возвращает 500. Stripe ретраит → следующая попытка попробует
   обработать заново (idempotency-чек смотрит только на PROCESSED).

Phase 12.x: dead letter queue для events, фейлящихся ≥3 раз
(см. ADR-0042 §«Webhook resilience»).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from shared_models.enums import StripeEventStatus
from shared_models.orm import StripeEventLog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_service.config import Settings, get_settings
from billing_service.database import get_session
from billing_service.services.event_handlers import EVENT_HANDLERS
from billing_service.services.stripe_client import construct_webhook_event

router = APIRouter(prefix="/billing/webhooks")
_LOG: Final = logging.getLogger(__name__)


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, Any]:
    """Принять Stripe webhook event и обработать.

    Возвращает ``{"received": true, "deduplicated": bool, "processed": bool}``.

    Status codes:

    * 200 — event верифицирован и (либо обработан, либо признан дублем).
    * 400 — отсутствует/невалидна подпись или payload не парсится.
    * 500 — обработчик event'а бросил exception (Stripe ретраит).
    """
    if not settings.stripe_webhook_secret:
        # Misconfiguration: webhook включён, но секрет не задан → отказываем
        # с 500, чтобы alert'ило Sentry, а не молча принимать unsigned events.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_WEBHOOK_SECRET is not configured.",
        )
    if stripe_signature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header.",
        )

    # КРИТИЧНО: читаем raw body, а не парсим Pydantic'ом. Stripe
    # требует exact-bytes для HMAC-проверки; любое JSON-перекодирование
    # ломает подпись. FastAPI отдаёт сырой body через request.body().
    raw_body = await request.body()

    try:
        event = construct_webhook_event(
            raw_body,
            stripe_signature,
            settings.stripe_webhook_secret,
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        # ValueError — невалидный JSON; SignatureVerificationError — неверная подпись.
        _LOG.warning("Stripe webhook rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature.",
        ) from exc

    event_id = event.get("id")
    event_type = event.get("type", "")
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event has no id.",
        )

    # Idempotency: пытаемся INSERT row с unique-constraint'ом.
    # Если такой event_id уже есть — это retry от Stripe, dedup.
    record = StripeEventLog(
        stripe_event_id=event_id,
        kind=event_type,
        status=StripeEventStatus.RECEIVED.value,
        payload=event,
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(StripeEventLog).where(StripeEventLog.stripe_event_id == event_id)
            )
        ).scalar_one_or_none()
        already_processed = (
            existing is not None and existing.status == StripeEventStatus.PROCESSED.value
        )
        _LOG.info(
            "Duplicate Stripe event id=%s type=%s already_processed=%s",
            event_id,
            event_type,
            already_processed,
        )
        return {"received": True, "deduplicated": True, "processed": already_processed}

    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        # Unknown event type — это OK: Stripe отдаёт много событий, мы
        # подписываемся только на нужные. Помечаем как PROCESSED
        # (no-op обработка) и возвращаем 200.
        record.status = StripeEventStatus.PROCESSED.value
        await session.flush()
        _LOG.debug("Stripe event id=%s type=%s — no handler, no-op", event_id, event_type)
        return {"received": True, "deduplicated": False, "processed": False}

    try:
        await handler(session, event)
    except Exception as exc:
        record.status = StripeEventStatus.FAILED.value
        record.error = f"{type(exc).__name__}: {exc}"
        await session.flush()
        _LOG.exception(
            "Stripe webhook handler failed: id=%s type=%s",
            event_id,
            event_type,
        )
        # 500 → Stripe пере-доставит event позже; идемпотентность
        # отсечёт уже-успешные события, а этот мы попробуем заново.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook handler failed",
        ) from exc

    record.status = StripeEventStatus.PROCESSED.value
    await session.flush()

    return {"received": True, "deduplicated": False, "processed": True}
