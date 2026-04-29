# ADR-0042 — Stripe billing architecture (Phase 12.0)

- **Status:** Accepted (2026-04-29)
- **Phase:** 12.0
- **Supersedes:** —
- **Related:** ADR-0028 (FS rate-limiting), ADR-0039 (transactional email),
  ADR-0036 (sharing permissions), CLAUDE.md §3 (Privacy by design).

## Context

AutoTreeGen monetises через подписку (Phase 12). До этой фазы все
feature-флаги (DNA upload, FamilySearch import, multi-tree) были
unconditionally включены. Phase 12.0 вводит:

1. **Plan model** — FREE / PRO / PREMIUM с разными feature-наборами.
2. **Платёжный pipeline** — checkout, recurring billing, refunds,
   обновление карт.
3. **State synchronization** — Stripe asynchronously шлёт webhook'и
   о смене подписки; нам нужно зеркалировать в локальной БД, чтобы
   feature-gating не вызывал Stripe API на hot-path.
4. **Billing emails** — payment_succeeded / payment_failed уведомления
   через email-service (Phase 12.2a уже в main).

## Decision

### Provider — Stripe (vs Paddle, LemonSqueezy)

Выбор обоснован:

- **Customer Portal** — Stripe предоставляет hosted UI для self-service
  (cancel, update card, view invoices). Альтернативы либо не имеют (LS),
  либо беднее (Paddle).
- **Webhook reliability** — at-least-once + retry policy + signed
  payload — стандарт индустрии. Paddle webhook UX явно слабее.
- **Test mode** — sk_test_* + Stripe CLI для local-dev `stripe listen`
  → перенаправляет webhook'и на localhost без туннеля.
- **EU coverage** — SCA/3DS handled out-of-the-box; LemonSqueezy
  (Merchant of Record) удобен для tax compliance, но мы пока не
  достигли threshold'ов где это критично, а гибкость Stripe API
  важнее.
- **Stripe SDK для Python** — официальный, активный, type-stub'ы
  через ``stripe>=10`` (Phase 12 lockfile).

Phase 12.x может добавить адаптер, если налоговая нагрузка вынудит
переехать на MoR (LemonSqueezy / Paddle). Вся state-логика
(``subscriptions``, ``stripe_event_log``) уже изолирована за
``billing_service.services.event_handlers`` — провайдер-специфичный
SDK живёт только в ``stripe_client.py``.

### Schema

Три service-table'а (см. migration `0019_stripe_billing.py`):

| Таблица | Назначение |
|---|---|
| `stripe_customers` | user → stripe_customer_id one-to-one. Только маппинг ID, никаких PII. |
| `subscriptions` | Canonical billing state per user. Mutated **только** webhook'ами. user_id НЕ unique — исторические подписки сохраняются. Lookup-by-active = `WHERE status='active' ORDER BY updated_at DESC LIMIT 1`. |
| `stripe_event_log` | Idempotency log + audit trail. `stripe_event_id UNIQUE` обеспечивает at-most-once dispatch. |

Plan / status хранятся как `text` с CHECK-constraint'ом, не PostgreSQL
ENUM type. Причина: ENUM type требует `ALTER TYPE ... ADD VALUE` вне
транзакции (Postgres < 12 особенно болезненно), и `DROP TYPE`
блокирует drop'ы зависимых столбцов. CHECK-constraint просто пере-
накатить migration'ом.

### Webhook security

`POST /billing/webhooks/stripe` — единственный endpoint, который
доступен извне без auth-header'а. Защита:

1. **Signature verification** обязательна — `Stripe-Signature` header
   проверяется через `stripe.Webhook.construct_event` (constant-time
   HMAC, retry tolerance window 5 минут). Невалидная подпись → 400 без
   обработки. Misconfiguration (отсутствует webhook_secret) → 500
   (alert'ит Sentry, лучше fail-loud чем тихо принимать unsigned events).
2. **Raw body** парсится **до** Pydantic — Stripe требует exact-bytes
   для HMAC, любое JSON-перекодирование ломает подпись.
3. **Idempotency** — `INSERT INTO stripe_event_log (stripe_event_id, ...)`,
   IntegrityError → дубль, 200 OK без side-effects. Status поля:
   `RECEIVED` (insert OK, dispatch ещё не выполнен), `PROCESSED`
   (handler success), `FAILED` (handler raised; Stripe пере-доставит).

### Failed payment policy (grace period)

`status=PAST_DUE` сам по себе не отключает фичи. `get_user_plan`:

- `ACTIVE` / `TRIALING` → возвращает `Plan(sub.plan)`.
- `PAST_DUE` в окне `current_period_end + grace_days` (default 7) →
  возвращает `Plan(sub.plan)` (доступ сохраняется — даём шанс
  обновить карту).
- `PAST_DUE` после grace или `CANCELED` / нет записи → `FREE`.

Grace-period настраивается через `BILLING_SERVICE_PAST_DUE_GRACE_DAYS`.
Типичный Stripe Smart Retries охватывает 3 попытки в течение 7 дней —
наш 7-дневный grace перекрывает это окно.

### Feature flag (`BILLING_ENABLED=false`)

Local dev и CI работают **без** Stripe credentials. Feature flag
управляет всем feature-gating'ом:

- `false` (CI default) → `get_user_plan` возвращает `Plan.PRO` для
  всех users; `assert_feature` no-op'ит; `check_entitlement` пропускает.
  Чекаут endpoint отдаёт 503.
- `true` (production / billing-service tests) → реальная резолюция
  через `subscriptions` row.

Тесты parser-service / dna-service unconditionally устанавливают
`false` через session-scoped autouse fixture — чтобы legacy-тесты
не требовали создания фейковых подписок в БД. Тесты гейтинга
(`test_billing_gates.py`) переопределяют флаг локально.

### Email fan-out

Invoice-event handler'ы (`invoice.paid`, `invoice.payment_failed`)
POST'ят к email-service `/email/send` с `idempotency_key=stripe_event_id`.
Email-service хранит `email_send_log.idempotency_key UNIQUE` — повторная
доставка того же Stripe-event'а не приведёт к дублирующему письму.
HTTP-failure best-effort (logged warning, no raise) — следующий
Stripe retry даст ещё попытку.

DNA-related события **никогда** не идут через billing email kind'ы —
это carry-forward правило из Phase 12.2: notifications о DNA matches /
kit upload — domain notification-service, не billing.

### GDPR / Privacy by design

- **Карта, billing address, имя владельца** хранятся **только** в
  Stripe. У нас в БД — `stripe_customer_id` (cus_*) и
  `stripe_subscription_id` (sub_*) — opaque identifiers.
- **GDPR Art. 17 (right to erasure):** account deletion → hard delete
  `stripe_customers` + `subscriptions` rows + `stripe.Customer.delete()`.
  Нет soft-delete на этих таблицах (комментарии в schema_invariants).
- **DNA-данные** (special category, Art. 9) **не** проходят через
  billing-service ни в каких payload'ах — отдельный domain в
  dna-service / notification-service.

## Plan limits (Phase 12.0)

| Plan | Trees | Persons/tree | DNA | FS-import |
|---|---|---|---|---|
| FREE | 1 | 100 | ❌ | ❌ |
| PRO | ∞ | ∞ | ✅ | ✅ (rate-limited 5/день) |
| PREMIUM | ∞ | ∞ | ✅ | ✅ (rate-limited 5/день) |

PREMIUM = PRO для Phase 12.0. Phase 12.x раскроет специфику
(bulk-инструменты, повышенные quotas, priority support).

Числовые квоты (`max_trees`, `max_persons_per_tree`) — **не**
проверяются `assert_feature` (требуют count-запросов к доменным
таблицам). Каждый endpoint, который их применяет, делает отдельный
запрос с `get_plan_limits(plan)`.

## Consequences

**Positive:**

- Чистое разделение: `billing-service` владеет state machine,
  caller-сервисы только читают через `assert_feature`.
- Idempotency на webhook'ах позволяет ретраить безопасно — Stripe
  может пере-доставить event N раз, мы применим side-effect ровно один.
- Email fan-out через email-service переиспользует уже работающий
  idempotent dispatch (Phase 12.2a, ADR-0039).
- Feature flag разрешает CI / local-dev работать без Stripe credentials.

**Negative / Trade-offs:**

- Stripe SDK — sync (использует `requests`); вызовы из async-handler'а
  блокируют event loop. Phase 12.0 принимает это (низкочастотный
  checkout); масштабирование → `asyncio.to_thread` или async-обёртка.
- Lockfile растёт на ~5 пакетов (stripe SDK + httpx already был).
- billing-service становится compile-time dep parser-service /
  dna-service. Workspace-resolve через uv делает это бесплатным
  для DX, но сами сервисы теперь сильнее coupled — breaking change
  в `entitlements.py` ломает нескольких потребителей сразу. Стабильное
  public API в `billing_service.services.entitlements` — обязательство.

## Alternatives considered

- **Paddle** — Merchant of Record упрощает tax compliance, но webhook
  UX слабее, hosted Customer Portal беднее, EU SCA — медленнее.
  Reconsider, если EU-tax overhead станет блокирующим.
- **LemonSqueezy** — приятный DX и MoR, но subscription primitives
  моложе и беднее (особенно вокруг proration / mid-cycle plan changes).
- **Self-hosted (Killbill, etc.)** — out of scope для one-person team.
- **Postgres ENUM** для plan/status — отвергнуто (см. Schema выше).
- **Single subscriptions-per-user (`UNIQUE(user_id)`)** — отвергнуто:
  ломает audit trail при cancel + resubscribe. Текущий дизайн
  (`UNIQUE(stripe_subscription_id)`) сохраняет историю на нашей стороне,
  не зависит от Stripe history retention policy.

## Out of scope (Phase 12.x)

- **Webhook DLQ** для events, фейлящихся ≥3 раз (ручной re-process через
  скрипт пока).
- **Customer Portal customization** (branding, multi-currency).
- **Team / org plans** — потребует нового измерения в `subscriptions`
  (e.g., `org_id` FK).
- **Frontend billing UI** (`/pricing`, `/settings/billing`,
  upgrade-modal) — приедет в отдельной web-фазе.

## References

- Stripe API: <https://stripe.com/docs/api>
- Webhook signing: <https://stripe.com/docs/webhooks/signatures>
- Customer Portal: <https://stripe.com/docs/billing/subscriptions/customer-portal>
- ADR-0028 — FS rate-limiting (применяется поверх PRO-флага).
- ADR-0039 — transactional email (idempotency через `idempotency_key`).
