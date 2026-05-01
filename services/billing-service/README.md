# billing-service

Phase 12.0 Stripe-биллинг для AutoTreeGen. Архитектура — **ADR-0042**.

## Что делает

- Создаёт Stripe Checkout Sessions (POST `/billing/checkout`,
  alias `POST /billing/subscriptions/checkout`).
- Проксирует пользователя в Stripe Customer Portal (GET `/billing/portal`).
- Принимает Stripe webhooks (POST `/billing/webhooks/stripe`) с
  signature verification + idempotency log (`stripe_event_log`).
- Выдаёт текущий план пользователя (GET `/billing/subscriptions/me`),
  который другие сервисы используют для feature-gating через
  `assert_feature(...)`.
- POST'ит email-service `payment_succeeded` / `payment_failed` из
  invoice webhook handler'ов (idempotent через stripe_event_id).

## Public API (для других сервисов)

```python
from billing_service.services.entitlements import (
    Plan,
    PlanLimits,
    assert_feature,
    check_entitlement,
    get_plan_limits,
    get_user_plan,
)
```

- `get_user_plan(session, user_id) -> Plan` — async, читает
  `subscriptions` и применяет правила grace-period.
- `get_plan_limits(plan) -> PlanLimits` — pure-function.
- `assert_feature(session, user_id, feature)` — async, raise 402
  если feature недоступен на текущем плане.
- `check_entitlement(feature)` — FastAPI dependency-фабрика для
  использования внутри billing-service (caller-сервисы строят свой
  тонкий dependency через `assert_feature`, см. примеры в
  `services/parser-service/src/parser_service/billing.py`).

## ENV-настройки

См. `.env.example`. Префикс `BILLING_SERVICE_`. Ключевые:

- `BILLING_SERVICE_STRIPE_API_KEY` — `sk_test_*` / `sk_live_*`.
- `BILLING_SERVICE_STRIPE_WEBHOOK_SECRET` — `whsec_*`.
- `BILLING_SERVICE_STRIPE_PRICE_PRO` / `_PREMIUM` — Stripe Price IDs.
- `BILLING_SERVICE_EMAIL_SERVICE_URL` — куда POST'ить
  payment_succeeded/failed (пустая строка отключает fan-out).
- `BILLING_SERVICE_BILLING_ENABLED` — feature-flag (default `true`).
  При `false` все entitlement-проверки пропускают, `get_user_plan`
  возвращает PRO. Удобно для local dev / CI без Stripe.

## Запуск локально

```bash
uv run uvicorn billing_service.main:app --reload --port 8005
# webhook-туннель в dev-режиме:
stripe listen --forward-to http://localhost:8005/billing/webhooks/stripe
```
