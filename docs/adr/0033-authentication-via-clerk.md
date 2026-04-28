# ADR-0033: Authentication via Clerk (Phase 4.10)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `auth`, `security`, `mvp-blocker`, `phase-4`

## Контекст

До Phase 4.10 проект работал в single-user dev-режиме: `parser-service`
резолвил владельца через `_ensure_owner(settings.owner_email)`,
`notification-service` принимал любой `X-User-Id` header без проверки,
`dna-service` вообще не имел auth-логики. Это не блокирует локальную
разработку, но **полностью блокирует public launch**: любой посетитель
сайта получил бы доступ к чужим деревьям и DNA-данным (special category
по GDPR Art. 9 — см. ADR-0012).

CLAUDE.md §4 фиксирует «Clerk или Auth0» как acceptable provider'ов.
ROADMAP §16 (security checklist) и §11 (production readiness) явно
требуют JWT-аутентификацию до выхода в публичный доступ.

Силы давления:

- **CLAUDE.md §5** (запрет auto-merge без manual review) подразумевает
  идентифицируемого пользователя, который этот merge подтверждает.
- **GDPR / DNA privacy (ADR-0012, ADR-0020).** DNA endpoint без
  верификации пользователя — нарушение по умолчанию.
- **Bus-factor.** Самописный JWT/OAuth — high-risk: rotation ключей,
  password reset, session revocation, MFA — всё нужно строить и
  поддерживать. Нет ресурсов.
- **Cost.** Free tier должен покрывать early launch (≤10k MAU),
  pricing prevent должен быть линейным и прозрачным.
- **DX.** Frontend на Next.js 15 / React 19 — ожидаем нативный SDK с
  middleware и hosted UI компонентами; ручная интеграция OAuth flow на
  тысячи строк кода в этом PR не помещается.

## Рассмотренные варианты

### Вариант A — Clerk

Хостинг auth-as-a-service с готовым Next.js SDK (`@clerk/nextjs`),
JWT-issuer, OAuth providers (Google/GitHub/Apple/Microsoft/...),
MFA/passkeys/email-magic-link, webhook'и для синка users.

- ✅ **Best Next.js DX в категории.** `clerkMiddleware` встаёт за ~10
  минут; `<SignIn>` / `<SignUp>` / `<UserButton>` — drop-in.
- ✅ **Free tier 10k MAU** покрывает early launch без оплаты.
- ✅ JWT с RS256 + JWKS endpoint — backend верификация одним PyJWT
  вызовом, без vendor SDK.
- ✅ Webhooks (`user.created` / `user.updated` / `user.deleted`) с
  Svix-HMAC подписью — бэкап для JIT-create flow.
- ❌ Vendor lock-in. Mitigation — JWT-формат стандартный (RS256/JWKS),
  миграция к Auth0/Supabase Auth = поменять issuer URL + JWKS URL,
  логика верификации не меняется.
- ❌ После 10k MAU $25/m, потом per-MAU. Acceptable для launch-этапа.

### Вариант B — Auth0

Аналог Clerk с большей корпоративной ориентацией.

- ✅ Большая популярность в enterprise; есть примеры миграций.
- ❌ Free tier 7.5k active users (меньше Clerk).
- ❌ Next.js SDK слабее; больше ручного boilerplate.
- ❌ Pricing ступенчатый и непрозрачный после free.

### Вариант C — Self-hosted (Authelia / Keycloak / Lucia / Better-Auth)

- ✅ Никакого vendor lock-in, контроль над данными.
- ❌ Operations: ротация ключей, RDS для users, password reset email,
  MFA, OAuth-провайдеры, rate-limiting login — всё на нас.
- ❌ Phase 4.10 — MVP-blocker, нет ресурсов на самописный auth.

### Вариант D — Supabase Auth

- ✅ JWT/JWKS совместимо; есть Next.js SDK.
- ❌ Ориентация на Postgres-as-platform; мы не пользуемся Supabase
  для остального — лишняя интеграционная стоимость.

## Решение

Выбран **Вариант A (Clerk)**.

Обоснование (4 предложения):

1. **DX.** Phase 4.10 — критический MVP-блокер; время до launch важнее
   long-term cost'а, и Clerk ставит auth за день, а не за две недели.
2. **Стандартный JWT.** Backend верификация — RS256 + JWKS,
   совместима с Auth0/Supabase Auth. Migration path понятен (поменять
   `clerk_issuer` ENV на новый issuer'а; код shared-models/auth не
   меняется).
3. **Free tier 10k MAU** покрывает >95% от ожидаемого первого года;
   $25/m после — copesetic для bootstrapped-проекта.
4. **Webhook canonical.** Clerk шлёт `user.created` / `user.updated` /
   `user.deleted` события с Svix-подписью — даёт нам second source of
   truth для users-таблицы помимо JIT-create.

## Архитектура

### Token flow

```text
[Browser]                          [parser-service]
   |                                       |
   |--- 1) Clerk SignIn -------------->    |
   |                                       |
   | (Clerk issues JWT in browser)         |
   |                                       |
   |--- 2) GET /trees/.../persons          |
   |       Authorization: Bearer JWT  ---> |
   |                                  |    |
   |                                  V    |
   |                       (verify_clerk_jwt)
   |                       JWKS RS256 + iss + exp
   |                                  |    |
   |                                  V    |
   |                       (get_or_create_user_from_clerk)
   |                                  |    |
   |                                  V    |
   |                       (handler runs with user_id)
```

### User sync flow (JIT vs webhook)

- **JIT (primary).** :func:`parser_service.services.user_sync.get_or_create_user_from_clerk`
  — на первый authed-API-вызов создаёт `users` row с
  `clerk_user_id = claims.sub`. Идемпотентен: повторный вызов с тем же
  sub возвращает существующий row.
- **Webhook (canonical).** `POST /webhooks/clerk` (`Svix`-signed):
  - `user.created` / `user.updated` — upsert user-row с email и
    display_name из Clerk dashboard. Бэкфил уже существующих row'ов.
  - `user.deleted` — soft-delete (`deleted_at = NOW()`).

JIT не блокируется ожиданием webhook'а (eventual consistency); если
webhook прилетит позже первого user-вызова, он просто обновит уже
существующий row.

### Decision tree per service

| Service | Auth mode | User identifier |
|---|---|---|
| parser-service | Bearer JWT обязателен на user-endpoint'ах | `users.id` UUID |
| dna-service | Bearer JWT на всех endpoint'ах (router-level) | `users.id` UUID |
| notification-service | Bearer JWT на end-user endpoint'ах; `POST /notify` — internal | int (legacy, см. §«Notification user_id type») |

Public endpoint'ы (без auth):

- `/healthz` — liveness probes.
- `/metrics` — Prometheus scrape под network ACL.
- `/webhooks/clerk` — Svix-HMAC аутентификация.
- SSE-endpoints — accept token via `?token=` query (browsers не дают
  custom headers на EventSource); follow-up.

### Notification user_id type

`notifications.user_id` и `notification_preferences.user_id` —
`BigInteger` без FK на `users` (Phase 8.0 quirk, см.
`shared_models.orm.notification.py`). Phase 4.10 не мигрирует тип:

- Auth-dependency `notification_service.auth.get_current_user_id`
  возвращает `int`, выведенный из `users.id` UUID через
  :func:`uuid_to_legacy_int` (старшие 63 bits, детерминирован).
- Phase 8.x — отдельная миграция на UUID FK. Внешний контракт
  endpoint'ов не меняется.

### Migration path (если Clerk dies)

1. Поменять ENV: `CLERK_ISSUER` → новый issuer URL (Auth0 / Supabase /
   self-hosted).
2. JWKS URL автоматически выводится из issuer'а; для override —
   `CLERK_JWKS_URL`.
3. Обновить frontend: replace `<ClerkProvider>` / `<SignIn>` на новый
   SDK. `lib/api.ts` сохраняет интерфейс `setAuthTokenProvider` — token-
   getter переписать на новый вендор.
4. Webhook receiver — переписать verification (HMAC формат меняется),
   сама business-логика upsert/delete та же.

`shared_models.auth.clerk_jwt` намеренно не зависит от Clerk-specific
claims (берёт только `sub`, `email`, `iss`, `exp`); этот код
переиспользуется без изменений.

## Что НЕ делаем в Phase 4.10

- Migration `notifications.user_id` → UUID. Отложено в Phase 8.x.
- SSE auth (current SSE endpoint'ы остаются open; cookie-/token-query
  flow — follow-up).
- Custom Clerk Organizations (multi-tenant trees per organization) —
  ROADMAP Phase 12+.
- Авто-создание Clerk webhook через Terraform — manual setup на
  dashboard'е, инструкции в README §Authentication.

## Ссылки

- Связанные ADR:
  - [ADR-0010 (TBD → 0033)](./0033-authentication-via-clerk.md) — этот документ
  - [ADR-0012](./0012-dna-privacy-architecture.md) — DNA как special category
  - [ADR-0020](./0020-dna-service-architecture.md) — consent/audit обязательно
    с identifiable user'ом
  - [ADR-0024](./0024-notification-service-architecture.md) — Phase 8.0
    quirk с int user_id
- External:
  - [Clerk Next.js docs](https://clerk.com/docs/nextjs)
  - [Clerk JWT verification](https://clerk.com/docs/backend-requests/handling/manual-jwt)
  - [Svix webhook verification](https://docs.svix.com/receiving/verifying-payloads/how)
  - [PyJWT JWKS support](https://pyjwt.readthedocs.io/en/stable/usage.html#retrieve-rsa-signing-keys-from-a-jwks-endpoint)
- Architecture: CLAUDE.md §4 (stack), §5 (no auto-merge — implies
  identifiable user), ROADMAP §16 (security checklist).
