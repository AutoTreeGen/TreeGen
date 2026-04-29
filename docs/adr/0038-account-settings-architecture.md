# ADR-0038: Account settings architecture (Phase 4.10b)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `auth`, `settings`, `gdpr`, `phase-4`
- **Supersedes:** —
- **Related:** [ADR-0033](./0033-authentication-via-clerk.md) (Clerk auth),
  [ADR-0012](./0012-dna-privacy-architecture.md) (DNA privacy).

## Контекст

Phase 4.10 (ADR-0033) shipped Clerk authentication: user'ы могут
зайти, JIT-create row в `users`, JWT verification на API. Но
user-facing settings отсутствовали:

- Нельзя сменить display_name.
- Нельзя посмотреть active sessions / выйти со всех устройств.
- Нет flow для GDPR Art. 15 (right to data portability) и Art. 17
  (right to erasure).

Это блокирует public launch — GDPR-compliance минимум требует «дать
пользователю возможность запросить удаление и export данных», даже
если processing занимает время.

Phase 4.11 (Agent 5, in flight) делает ACTUAL processing: тяжёлый
backend-flow для export (генерация tar.gz с GEDCOM + DNA + provenance,
signed-URL в storage) и erasure (hard-delete cascade всех trees,
DNA, citations, provenance с audit-trail). Это серьёзная работа —
много межсервисных coordination'ов, тесты на cascade integrity,
worker-инфраструктура.

Phase 4.10b ставит UI и DB-row contract: пользователь может INITIATE
запрос, мы пишем row в `user_action_requests` с `status='pending'`,
4.11 worker берёт его и обрабатывает. UI получает 202 Accepted +
request_id, опрашивает GET /users/me/requests для статус-апдейтов.

## Силы давления

- **MVP launch deadline.** Auth without settings — incomplete
  product. Settings without erasure/export — non-compliant. Settings
  with stub-stub — компромисс, который Phase 4.11 закрывает.
- **CLAUDE.md §3.1 (Evidence-first).** Каждое action — auditable
  row. `user_action_requests.kind` + `status` + timestamps дают
  нативный audit-trail без extra audit_log записей.
- **CLAUDE.md §5.** Erasure — destructive; user должен явно
  confirm'ить (typing email). Никакого one-click delete.
- **GDPR.** Art. 15 (export) requires structured machine-readable
  format; Art. 17 (erasure) requires actionable response within
  reasonable time. Stub-now-process-in-4.11 acceptable: row created,
  user notified, processing tracked.
- **Coordination с Phase 4.11.** Agent 5 расширит processing-side
  (worker, file generation, hard-delete cascade) поверх той же
  таблицы. ADR фиксирует table name `user_action_requests` чтобы
  не разъезжаться.

## Рассмотренные варианты

### Вариант A — Общая таблица `user_action_requests`

Одна таблица, `kind ∈ {'export', 'erasure'}`, общий lifecycle
`pending → processing → done/failed/cancelled`.

- ✅ Один schema row для двух kind'ов: lifecycle 95% одинаковый,
  worker-handler шарится в Phase 4.11.
- ✅ UI один: list endpoint возвращает оба типа сразу.
- ✅ Rаsширение на третий action (например, "downgrade to free
  tier") — добавление check-constraint, не новой таблицы.
- ❌ kind-specific поля идут в `request_metadata` jsonb, не в
  типизированных колонках. Acceptable trade-off: типизированные
  колонки нужны только для query-частых полей, метаданные exports
  и erasure'ов в production не запрашиваются по содержимому.

### Вариант B — Отдельные `export_requests` / `erasure_requests`

- ✅ Чёткая typed-schema per kind.
- ❌ 2 миграции, 2 ORM-модели, 2 worker-handler'а с почти-копией
  кода. Premature на текущем scope.
- ❌ Расширение на третий action удваивает overhead.

### Вариант C — Inline-обработка в endpoint'е

«POST /users/me/erasure-request» сразу делает hard-delete, без row.

- ❌ HTTP request не должен делать тяжёлую работу — timeout, retry
  семантика становится разрушительной (повторный POST = повторное
  удаление пустого аккаунта = noop, OK; но повторный export =
  двойная генерация, дорого).
- ❌ Нет audit-trail кто-когда-инициировал.
- ❌ Нет места для confirm-email-link flow в Phase 4.11.

## Решение

Выбран **Вариант A** — общая таблица `user_action_requests`.

Schema (alembic 0015):

```text
user_action_requests
  id                 UUID PK
  user_id            UUID FK users.id ON DELETE CASCADE
  kind               TEXT  CHECK kind IN ('export', 'erasure')
  status             TEXT  CHECK status IN ('pending', 'processing',
                                            'done', 'failed', 'cancelled')
  request_metadata   JSONB DEFAULT '{}'
  created_at         TIMESTAMPTZ DEFAULT now()
  updated_at         TIMESTAMPTZ DEFAULT now()
  processed_at       TIMESTAMPTZ NULL
  error              TEXT NULL

indexes:
  ix_user_action_requests_user_id  (FK lookup, основной UI-запрос)
  ix_user_action_requests_status   (worker-side scan для Phase 4.11)
```

Phase 4.10b создаёт rows только в `status='pending'`. Phase 4.11
переводит в `processing → done/failed`.

`request_metadata` (jsonb):

- `kind='export'`: `{"format": "gedcom_tar_gz"}` (default).
  Phase 4.11 добавит `tree_ids` filter и `download_url` (после
  generation).
- `kind='erasure'`: `{"confirm_email_hash_marker": "set"}` (Phase
  4.11 добавит `email_link_token`, `email_confirmed_at`).

## API contract

Phase 4.10b locks the UI contract — Phase 4.11 не меняет endpoint'ы,
только заполняет `status` / `processed_at` / `error` через worker.

```text
PATCH  /users/me                       — display_name / locale / timezone
POST   /users/me/erasure-request       — body {confirm_email}; 202 + request_id
POST   /users/me/export-request        — body {} (filter'ы — Phase 4.11)
GET    /users/me/requests              — list user's own; isolation by user_id
```

Изоляция между user'ами:

- Все endpoint'ы используют `Depends(get_current_user_id)` из
  ADR-0033; row-level filter `WHERE user_id = current_user`.
- 409 при попытке создать второй active request того же kind:
  один user — максимум один pending+processing на kind.

## Frontend split

`apps/web/src/app/(authenticated)/settings/page.tsx` — three tabs:

- **Profile**: display_name / locale / timezone. Locale **dual-
  writes**: backend (`users.locale` — canonical для i18n в email
  notification'ах) **и** Clerk publicMetadata (survives sign-out,
  доступен в JWT-claims без round-trip к нашему backend'у).
- **Sessions**: список через Clerk-API
  (`clerkUser.getSessions()`), не из нашего DB. Revoke individual +
  "Sign out everywhere" (revoke all active sessions). Clerk
  invalidates JWT в течение ~1s.
- **Danger zone**: "Request my data" (`POST /export-request`),
  "Delete account" (modal с email-confirm typing →
  `POST /erasure-request`). Pending requests блокируют повторный
  click — UI читает `GET /users/me/requests` и hide'ит CTA если
  pending уже есть.

## Что НЕ делаем в Phase 4.10b

- Actual data export generation. Phase 4.11.
- Actual hard-delete cascade. Phase 4.11.
- Email-link confirmation для erasure (sec. layer над typed-email-
  confirm). Phase 4.11.
- Notification на done-event'е. Phase 4.11 enqueue'ит через
  notification-service.
- SSE для real-time status updates. Phase 4.11; пока polling.

## Migration path к Phase 4.11

Agent 5 расширяет processing — без schema-breaking changes:

1. Worker process реагирует на новые pending rows.
2. Перевод status → processing → done/failed.
3. Для export: file generation → signed URL → write в
   `request_metadata.download_url`.
4. Для erasure: hard-delete cascade с audit-log → soft-delete
   `users` row с `deleted_at`.

UI Phase 4.10b уже polls `GET /users/me/requests`; новые поля в
`request_metadata` (`download_url` / `purged_count`) подхватываются
без кода UI — frontend делает type-narrow рендер.

## Ссылки

- [ADR-0033](./0033-authentication-via-clerk.md) — Clerk auth + JIT
  user creation; этот ADR строит поверх.
- [ADR-0012](./0012-dna-privacy-architecture.md) — DNA как special
  category; erasure ОБЯЗАН включать DNA hard-delete.
- [GDPR Art. 15](https://gdpr-info.eu/art-15-gdpr/) — right of access.
- [GDPR Art. 17](https://gdpr-info.eu/art-17-gdpr/) — right to erasure.
- [Clerk session API](https://clerk.com/docs/references/javascript/user/user#get-sessions)
  — `user.getSessions()` returns `SessionWithActivities[]`.
