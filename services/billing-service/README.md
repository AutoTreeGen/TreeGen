# billing-service

Phase 12.0 Stripe-биллинг для AutoTreeGen. Архитектура — **ADR-0034**.

## Что делает

- Создаёт Stripe Checkout Sessions (POST `/billing/checkout`).
- Проксирует пользователя в Stripe Customer Portal (GET `/billing/portal`).
- Принимает Stripe webhooks (POST `/billing/webhooks/stripe`) с
  signature verification + idempotency log.
- Выдаёт текущий план пользователя (`get_user_plan(session, user_id)`),
  который другие сервисы используют для feature-gating через
  `check_entitlement(...)` FastAPI dependency.

## Public API (для других сервисов)

```python
from billing_service.services.entitlements import (
    Plan,
    PlanLimits,
    check_entitlement,
    get_plan_limits,
    get_user_plan,
)
```

- `get_user_plan(session, user_id) -> Plan` — async, читает
  `stripe_subscriptions` и применяет правила grace-period.
- `get_plan_limits(plan) -> PlanLimits` — pure-function, возвращает
  числовые/булевые лимиты для UI и для серверных проверок.
- `check_entitlement(feature)` — FastAPI dependency-фабрика,
  возвращающая `Depends`-callable, который 402'ит при отсутствии
  доступа.

## ENV-настройки

Префикс `BILLING_SERVICE_`:

- `BILLING_SERVICE_STRIPE_API_KEY` — `sk_test_*` / `sk_live_*`.
- `BILLING_SERVICE_STRIPE_WEBHOOK_SECRET` — `whsec_*` для
  подписи webhook'ов.
- `BILLING_SERVICE_STRIPE_PRICE_PRO` — `price_*` Stripe Price ID для
  Pro-плана.
- `BILLING_SERVICE_BILLING_ENABLED` — feature-flag (default `true`).
  При `false` все entitlement-проверки пропускают, `get_user_plan`
  возвращает PRO. Удобно для local dev / интеграционных тестов
  без реальной Stripe-интеграции.
- `BILLING_SERVICE_CHECKOUT_SUCCESS_URL` / `BILLING_SERVICE_CHECKOUT_CANCEL_URL` —
  куда Stripe редиректит после Checkout.
- `BILLING_SERVICE_PORTAL_RETURN_URL` — куда Customer Portal вернёт
  пользователя.

## Запуск локально

```bash
uv run uvicorn billing_service.main:app --reload --port 8003
# webhook-туннель в dev-режиме:
stripe listen --forward-to http://localhost:8003/billing/webhooks/stripe
```
