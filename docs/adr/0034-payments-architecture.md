# ADR-0034: Payments architecture (Stripe-first)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `payments`, `stripe`, `gdpr`, `phase-12`

## Контекст

ROADMAP §16 (Phase 12) фиксирует план перехода на платную модель:
тарифы Beginner/Advanced/Super, Stripe + PayPal + Coinbase Commerce,
feature flags по tier'у, usage metering, billing-emails.

Phase 12.0 — **скелет** этой системы: одна простая монетизация (Free vs
Pro) на одном провайдере (Stripe), с правильной архитектурой и
расширяемостью. Без этого скелета каждое последующее усложнение (PayPal,
crypto, granular tiers, dunning emails) будет работой с нуля.

CLAUDE.md §4 уже фиксирует Stripe как первую платёжку. Этот ADR
формализует **почему именно Stripe**, **как** обрабатываем платёжные
события, **какие** privacy-границы и **что** отложено в Phase 12.x.

## Рассмотренные варианты

### Вариант A — Stripe (выбран)

- ✅ Низкие fees: 1.4% + €0.25 для EU, 2.9% + $0.30 для US.
- ✅ Полная EU + US + UK покрытость; SEPA, BACS, IDEAL out-of-box.
- ✅ Best-in-class API: documented webhook spec, signed events,
  declarative pricing models, Customer Portal (selfservice).
- ✅ Tax compliance из коробки (Stripe Tax).
- ✅ Long-term: Stripe Billing подходит и для usage-based metering
  (Phase 12.5+).
- ❌ Stripe SDK — sync (``stripe.Customer.create``); async обёртку
  пишем сами или используем ``asyncio.to_thread``.

### Вариант B — LemonSqueezy

- ✅ Merchant of record: они платят VAT/taxes за нас.
- ❌ ~5% fees (vs 1.4–2.9% Stripe).
- ❌ Менее гибкий API; нет PaymentIntent equivalent для одноразовых
  custom-flows; webhook spec проще, но без replay tolerance.
- ❌ Risk of vendor lock-in — миграция в Stripe позже = миграция базы
  customers, потеря history.

### Вариант C — Paddle

- ✅ Также merchant of record.
- ❌ ~5% + $0.50 fixed fee.
- ❌ Approval-based onboarding для new merchants (~2 недели).
- ❌ Меньше документации по advanced flow'ам (Customer Portal,
  metered billing).

### Вариант D — Self-hosted (оставить только bank transfers)

- ❌ GDPR + PCI compliance — наш собственный обвес: storing PAN,
  SAQ-D-Merchant, regular audits. Несоразмерно для скелета.

## Решение

Выбран **Вариант A (Stripe)** для Phase 12.0. PayPal (Phase 12.1) и
Coinbase Commerce (Phase 12.2) — отдельный provider-плагин, без
переписывания core-логики.

Архитектура:

1. **Отдельный `billing-service` микросервис.** Изолирует Stripe SDK,
   webhook signature verification, idempotency log, Customer Portal
   redirect'ы. Зависит только от ``shared-models`` (для ORM таблиц).
2. **Ровно одна таблица подписок per user.** ``stripe_subscriptions``
   с unique constraint на ``user_id`` — переход FREE→PRO→FREE
   обновляет ту же строку. Stripe-side history остаётся в Stripe
   Dashboard; дублировать незачем.
3. **Idempotency через ``stripe_events`` table.** Каждый ``evt_*``
   ID — unique row. Дубль → 200 OK без side-effects.
4. **Plan limits — single source of truth.**
   ``billing_service.services.entitlements.PlanLimits`` фиксирует
   лимиты:
   - **FREE:** 1 tree, 100 persons, без DNA, без FS-импорта.
   - **PRO:** unlimited trees, unlimited persons, DNA upload, FS-импорт.
   UI и backend читают их из одного места (Phase 12.x: SSR-фетч на
   /pricing-странице).
5. **Feature-gating через FastAPI dependency.** В каждом сервисе —
   тонкий wrapper (``parser_service.billing.require_feature``,
   ``dna_service.billing.require_feature``) над общей бизнес-логикой
   ``assert_feature``. 402 Payment Required + structured detail.
6. **Mock auth — X-User-Id header.** Pre-Phase-4.10 (Clerk JWT). Тот же
   pattern, что в notification-service (см. ADR-0024).
7. **Async-only API в billing-service.** Сам Stripe SDK sync; обёртки
   ``asyncio.to_thread`` добавим в Phase 12.x при росте нагрузки.

### Webhook security

Жёсткие требования (см. также Stripe docs «Best practices»):

1. **Signature verification обязательна.** ``stripe.Webhook.construct_event``
   валидирует HMAC-SHA256 подпись против raw-body **до** парсинга.
   Невалидная подпись → 400, без обработки. Никакого fallback'а
   «попробуем без подписи» — это двери открыты.
2. **Raw-body, не parsed JSON.** Stripe требует exact-bytes для HMAC;
   FastAPI'шный ``await request.body()`` отдаёт сырой ``bytes``,
   запрос подписи проходит.
3. **Replay protection.** ``stripe-signature`` header содержит
   ``t=<timestamp>``; SDK проверяет, что timestamp в окне 5 минут
   (default tolerance). Опасения по поводу повторов решает idempotency
   log (см. ниже).
4. **Idempotency.** ``stripe_events.stripe_event_id`` UNIQUE. Дубль
   (Stripe at-least-once retry) → 200 без side-effects.
5. **Webhook secret в Secret Manager (production).** Локально — `.env`
   с ``BILLING_SERVICE_STRIPE_WEBHOOK_SECRET=whsec_*``. ENV-name
   зарезервировано в `.env.example`.

### Webhook resilience

Phase 12.0:

- Exception в обработчике → 500. Stripe ретраит (exponential backoff,
  до 3 дней).
- Idempotency-чек смотрит на ``status=PROCESSED``; FAILED-events
  пере-обрабатываются.
- Любые unknown event types — no-op, 200, помечаем PROCESSED.

Phase 12.1+:

- **Dead letter queue.** Если event фейлится ≥3 раз подряд, кладём
  его в ``stripe_events_dlq`` для ручного review. Пока без этого
  — событий мало, manual replay через Stripe Dashboard +
  ``processed_at IS NULL`` query закрывает.
- **Alerting.** Sentry alert на ``status=FAILED`` события через
  Cloud Logging.

### Failed payment policy

- **PAST_DUE.** Stripe сам делает 4 retry-attempts за 7 дней
  (default). Мы зеркалируем status в БД, в эти 7 дней user
  продолжает иметь Pro-доступ (см. ``get_user_plan`` grace logic,
  ``settings.past_due_grace_days=7``). Это даёт окно для апдейта
  карты через Customer Portal.
- **CANCELED.** После grace period или явного cancel'а в Customer
  Portal — Stripe закрывает subscription, мы получаем
  ``customer.subscription.deleted``, помечаем status=CANCELED.
  С этого момента ``get_user_plan`` возвращает FREE → фичи
  немедленно off.
- **Email-уведомления.** Phase 12.x. Сейчас — только Stripe-нативные
  emails (включаются в Stripe Dashboard).

### GDPR / Privacy

- **Customer data в Stripe.** Имя, billing address, карта — хранятся
  только на стороне Stripe. Мы храним:
  1. Маппинг ``user_id`` → ``stripe_customer_id``.
  2. Subscription state (plan, status, period_end).
  3. Webhook event log для idempotency (90-day TTL — Phase 12.1).
- **Data subject rights:**
  - **Right to deletion:** при удалении user'а делаем
    ``stripe.Customer.delete(stripe_customer_id)`` + ON DELETE CASCADE
    наши таблицы. История платежей у Stripe останется ≥6 лет
    (legal/financial req); эта retention за пределами нашего контроля.
  - **Right to access:** экспорт через Customer Portal (Stripe сам
    отдаёт invoice PDF, receipt).
  - **Data portability:** не релевантно — billing data не переносима.
- **PCI scope.** Мы НЕ трогаем PAN: Checkout открывается на
  Stripe-домене, Customer Portal — тоже Stripe-hosted. Наш сервер не
  видит карту никогда.

### Feature flag для local dev / CI

``BILLING_SERVICE_BILLING_ENABLED=false`` (default в `.env.example`)
переводит сервис в bypass-режим:

- ``get_user_plan`` всегда возвращает PRO.
- ``check_entitlement`` / ``require_feature`` пропускают любые запросы
  без 402.
- Checkout / Portal endpoint'ы возвращают 503 (служба отключена).

Это решает три задачи:

1. Локальная разработка без Stripe-аккаунта работает «как Pro».
2. CI-тесты других сервисов (parser-service, dna-service) не
   требуют поднимать billing-service.
3. Prод-ENV выставляет ``BILLING_ENABLED=true`` явно — нет риска
   «забыли включить и все user'ы получили free Pro».

## Последствия

**Положительные:**

- Микросервис чистый: один Stripe SDK + 3 ORM-модели + 4 endpoint'а.
  Phase 12.x растёт по понятным линиям.
- Privacy-boundary explicit и легко проверяема в code review.
- Тесты идут без Stripe-аккаунта (signature mocking + payload
  fixtures).

**Отрицательные / стоимость:**

- Дублирование auth-логики между billing-service и
  parser-service/dna-service (тонкие wrapper'ы вокруг
  ``assert_feature``). Упростится в Phase 4.10 — единый JWT
  middleware закроет все сервисы.
- Stripe SDK sync. На Pro-волне (≤100 запросов/сек) нет проблемы;
  Phase 12.x: ``asyncio.to_thread`` + connection pool tuning.

**Риски:**

- **Stripe outage.** ``checkout`` endpoint вернёт 5xx, user не сможет
  купить. Существующие подписки продолжают работать (мы читаем local
  DB). Mitigation: status page link на pricing-page.
- **Webhook delivery delay.** Stripe иногда доставляет с задержкой
  до часа в incidents. UI на ``settings/billing`` не покажет новый
  plan мгновенно после payment. Mitigation: при success-redirect
  с checkout (``checkout=success`` query param) показать banner
  «Payment processing — your plan will update shortly».
- **Concurrent updates.** Если приходит ``customer.subscription.updated``
  и ``invoice.payment_succeeded`` параллельно (Stripe doesn't
  guarantee order), последний writer выигрывает. Acceptable
  (оба event'а несут консистентную инфу), но Phase 12.x: ETag-
  optimistic locking на ``StripeSubscription``.

**Что нужно сделать в коде (Phase 12.0):**

- ✅ ORM: ``StripeCustomer``, ``StripeSubscription``, ``StripeEvent``.
- ✅ Alembic миграция 0014.
- ✅ ``services/billing-service/`` (config, database, schemas,
  api/{checkout,webhooks,health}, services/{entitlements,
  stripe_client,event_handlers}).
- ✅ ``parser_service.billing`` / ``dna_service.billing`` —
  per-service wrappers.
- ✅ Гейтинг в ``POST /imports``, ``POST /imports/familysearch``,
  ``POST /imports/familysearch/import``, ``POST /dna-uploads``.
- ✅ Frontend: ``/pricing``, ``/settings/billing``, upgrade-modal.
- ✅ docker-compose: billing-service.
- ✅ Terraform: billing-service Cloud Run + webhook secret.

**Что отложено в Phase 12.x:**

- 12.1: PayPal Subscriptions API (тот же ``stripe_*`` table-набор +
  поле ``provider``? — переименуем в ``billing_*`` через миграцию).
- 12.2: Coinbase Commerce (для crypto).
- 12.3: Granular tiers (Advanced/Super из ROADMAP §16) +
  per-tier numeric quota (max_persons enforcement в endpoint'ах).
- 12.4: Usage metering (LLM calls, FS imports/day).
- 12.5: Billing-emails (Mailgun + ADR-0029 channels).
- 12.6: Dead-letter queue для failed webhooks.
- 12.7: Promotional discounts / coupons.

## Когда пересмотреть

- Если Stripe fees превысят 5% от gross (учитывая mix EU/US/crypto).
- Если регуляторное требование («pay only in BYN», China-specific) —
  потребует local provider-а.
- Если usage-based pricing станет основной моделью (а не subscription) —
  возможно простой Stripe Billing → Stripe Metered Billing миграция.

## Ссылки

- Связанные ADR: ADR-0010 (auth — Phase 4.10, mock auth bridge),
  ADR-0024 (notification-service — pattern для микросервиса с
  X-User-Id), ADR-0028 (rate limiting — Pro-tier FS-imports/day).
- ROADMAP §16 «Фаза 12 — Платежи и тарифы».
- CLAUDE.md §4 «stack mentions Stripe…».
- Stripe Webhook docs: <https://stripe.com/docs/webhooks/best-practices>.
- Stripe Checkout docs: <https://stripe.com/docs/payments/checkout>.
