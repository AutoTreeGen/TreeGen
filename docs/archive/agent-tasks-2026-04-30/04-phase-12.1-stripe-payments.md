# Agent 4 — Phase 12.1: Stripe Checkout + Customer Portal

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (`F:\Projects\TreeGen`).

## ⚠️ Этот агент — **единственный**, кому разрешено

- Создавать новую Alembic-миграцию (**0016**).
- Менять `packages/shared-models/` (добавить модель `Subscription`).
- Регистрировать новый workspace member в корневом `pyproject.toml` (для `services/payment-service`).

Если другие агенты работают параллельно — **дождись их merge** перед своим PR, чтобы избежать гонки за номер миграции. Если миграции в `main` уже 0017+, сдвинь номер.

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md`.
2. `ROADMAP.md` — «Фаза 12 — Платежи и тарифы» (§16). Тарифы: Beginner / Advanced / Super / Private Investigation.
3. `docs/data-model.md` — на чём строится связь `User ↔ Subscription`.
4. `services/parser-service/` — образец структуры FastAPI-сервиса; `packages/shared-models/` — где живут общие ORM-модели.
5. ADR-0010 (auth, Clerk) — узнаешь, как идентифицируется пользователь.

## Задача

Создать `services/payment-service` со scaffold-интеграцией Stripe. **Только Stripe** — PayPal/Coinbase/feature flags/metering — это Phase 12.2+ (отдельные подзадачи).

## Scope

### Новый сервис `services/payment-service/`

По образцу `services/dna-service/`:

- `pyproject.toml` — добавь `stripe>=9.0` в зависимости подпакета (НЕ в root).
- `src/payment_service/main.py` — FastAPI app + `apply_security_middleware(app, "payment-service")`.
- `src/payment_service/api/billing.py` — роутер.
- `src/payment_service/services/stripe_client.py` — обёртка над `stripe` SDK (async-friendly через `asyncio.to_thread`).
- `src/payment_service/config.py` — Settings.
- `tests/`.

Зарегистрировать в корневом `pyproject.toml` как workspace member.

### ORM-модель в `packages/shared-models/`

```python
class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(64), unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    tier: Mapped[str] = mapped_column(String(32))  # 'beginner' | 'advanced' | 'super' | 'free'
    status: Mapped[str] = mapped_column(String(32))  # stripe statuses: active, past_due, canceled, incomplete, ...
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at, updated_at, deleted_at  # стандартный набор как у других моделей
```

Миграция Alembic **0016**: создать таблицу с индексами на `user_id`, `stripe_customer_id`, `stripe_subscription_id`. Партиал-индекс на `WHERE deleted_at IS NULL` если паттерн в проекте.

### Эндпоинты

- `POST /billing/checkout` — body: `{ tier: 'beginner'|'advanced'|'super', success_url, cancel_url }`. Создаёт Stripe Customer (если ещё нет), создаёт Checkout Session (mode=subscription, price_id из env-map по tier). Возврат: `{ checkout_url }`.
- `POST /billing/webhook` — публичный, **проверяет подпись Stripe** (`stripe.Webhook.construct_event`). Обрабатывает: `checkout.session.completed`, `customer.subscription.created/updated/deleted`, `invoice.payment_failed`. Идемпотентен по `event.id` (Redis SET с TTL 7 дней).
- `POST /billing/portal` — body: `{ return_url }`. Создаёт Customer Portal session. Возврат: `{ portal_url }`.
- `GET /billing/subscription` — текущая подписка пользователя. Если нет — возвращает `tier='free'`, `status='active'`.
- `GET /healthz`.

### Конфиг (env-vars)

`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_BEGINNER`, `STRIPE_PRICE_ADVANCED`, `STRIPE_PRICE_SUPER`. Если не заданы — `503` с понятным сообщением (кроме `/healthz`).

### ADR-0056 в `docs/adr/`

- Stripe как первый провайдер (US/EU coverage, dev experience). PayPal/Coinbase в Phase 12.2/12.3.
- Subscription как single-source-of-truth, синхронизация через webhooks (НЕ через polling).
- Webhook idempotency через Redis (event.id).
- Открытый вопрос: data residency — где живёт payment_service в prod (вероятно EU regional, см. ADR data residency если есть).

## Тесты (> 80%)

- `tests/test_stripe_client.py` — unit на обёртку, мок `stripe.checkout.Session.create`, `stripe.billing_portal.Session.create`, обработку Stripe errors.
- `tests/test_webhook.py` — проверка верификации подписи (валидная/невалидная — 400), идемпотентность повторного event.id, обновление Subscription для каждого типа event.
- `tests/test_endpoints.py` — TestClient: 503 без env, 401 без auth, успешные сценарии с моком.
- `tests/test_security_headers.py` — smoke.
- Маркер `@pytest.mark.integration` для теста, который реально стучится в Stripe test mode (skipped в CI).
- `packages/shared-models/tests/test_subscription_model.py` — ORM конструктор, JSON-сериализация.

## Запреты

- ❌ Реально запускать Stripe webhook listener в тестах (только моки + signature verification на тестовых fixture'ах из Stripe docs).
- ❌ Класть реальный `STRIPE_SECRET_KEY` куда-либо — даже в `.env.example` указывай `sk_test_REPLACE_ME`.
- ❌ Обрабатывать платежи на нашей стороне (всё через Stripe-hosted Checkout — PCI scope минимизирован).

## Процесс

1. `git checkout -b feat/phase-12.1-stripe-payments`
2. Перед стартом: `git pull origin main` и проверь, что миграции в `main` остановились на 0015. Если 0016+ уже занят — сдвинь свой номер.
3. Коммиты: `feat(shared-models): add Subscription ORM`, `feat(infra): alembic 0016 subscriptions table`, `feat(payment-service): scaffold + stripe_client`, `feat(payment-service): checkout/portal/webhook endpoints`, `docs(adr): add ADR-0056`, `test(payment-service): ...`.
4. `uv run pre-commit run --all-files` + `uv run pytest packages/shared-models services/payment-service` перед каждым коммитом.
5. После добавления зависимостей в подпакет — `uv sync --all-extras --all-packages`.
6. **НЕ мержить, НЕ пушить в `main`. Финальный merge — последним в очереди после остальных 5 агентов** (см. `.agent-tasks/README.md`).

## Финальный отчёт

- Ветка, коммиты, pytest, файлы, ADR-0056, env-vars для prod, как локально протестить webhook (`stripe listen --forward-to localhost:8000/billing/webhook`), известные ограничения (multi-currency, taxes — TODO).
