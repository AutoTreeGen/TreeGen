# ADR-0056: Telegram bot commands + subscription model (Phase 14.1)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `telegram-bot`, `notifications`, `phase-14`

## Контекст

ADR-0040 описал scaffold telegram-bot service'а: webhook receiver,
account-linking flow через one-time token (`/start`), Phase 14.0 `_STUB_14_1`
для остальных команд. Phase 14.1 заменяет stub'ы на real implementations
и добавляет push-notification subscription:

- `/imports` — последние 5 import jobs (text + inline keyboard);
- `/persons <name>` — top-5 persons в active tree;
- `/tree` — info по active tree (name, person count, last update);
- `/subscribe` — toggle `notifications_enabled` на linked-chat'е;
- wire-up: notification-service может push'нуть в bot для подписанных.

Контекст ограничивает выбор:

- Bot уже имеет direct DB access (Phase 14.0 `link.py` пишет
  `TelegramUserLink` через `shared_models.orm`).
- `Notification.user_id` — `BigInteger` без FK на `users` (pre-existing
  tech-debt, ADR-0024 §Контекст). `TelegramUserLink.user_id` — UUID.
- Phase 14.0 trust model: каждый сервис trust'ит api-gateway-validated
  Clerk JWT; для service-to-service нет общего auth-фреймворка.

## Рассмотренные варианты

### Bot reads data: HTTP к parser-service vs direct DB

**A. HTTP (запрос ставился в спеке).** Bot вызывает `GET
/users/me/imports?limit=5`, `GET /users/me/active-tree`, ... через
parser-service.

- ✅ Чёткая граница «bot — UI слой, parser — модель».
- ❌ Service-to-service auth design не существует (Clerk JWT for end-user
  только; bot не может «прикинуться» user'ом без получения JWT'а от
  Clerk admin API). Внести pattern для service-token + retry +
  circuit-breaker — это ~300 LOC только инфраструктуры.
- ❌ Дополнительные round-trip'ы (Telegram timeout 5s; webhook должен
  отвечать <2s, иначе Telegram retry'ит → дубли).
- ❌ Скрытая зависимость: если parser-service down, бот молчит даже на
  read-only командах.

**B. Direct DB (выбран).** Bot читает `import_jobs`, `trees`, `persons`,
`names`, `telegram_user_links` напрямую через `shared_models.orm`.

- ✅ Монорепо share'ит ORM — одни и те же миграции, индексы,
  constraint'ы. Не дублируем pydantic-схемы для парсинга response'а.
- ✅ Нет cross-service auth: каждый сервис ходит в БД с тем же DSN'ом и
  своим pool'ом.
- ✅ Read-only — нет риска нарушить инварианты mutating-flow'ов
  (merge / undo / GDPR-erasure).
- ❌ Bot будет «знать» о `tree_id`, `owner_user_id`, soft-delete-conventions —
  тот же coupling, что у parser-service. В случае серьёзной
  reorganization модели надо менять оба места. Принимаем как trade-off
  — 14.1 не время вводить service-mesh.

### Active tree resolution

Простейший rule (per spec): **first-owned by `created_at ASC`**, без
учёта member-trees (Phase 11.0 sharing). Phase 14.2 опционально
расширит на `tree_memberships` join.

### Notifications opt-in default

`notifications_enabled BOOLEAN NOT NULL DEFAULT FALSE` — privacy-by-default:

- linked-chat сам по себе не получает push'и (только если user явно
  /subscribe);
- следует pattern'у GDPR-by-default (`NotificationPreference` —
  ADR-0029 — тоже opt-in для каналов кроме `in_app`).

`/subscribe` — toggle (повторный вызов отключает). Альтернатива —
`/subscribe` + `/unsubscribe` отдельно — отвергнута: меньше surface,
меньше памяти у user'а, и идемпотентность естественная (state виден в
ответе сразу: «✅ включена» / «🔕 отключена»).

### Cross-service push: notification-service → telegram-bot

**Channel-style integration** (выбран): TelegramChannel в
`notification-service/channels/` встаёт в существующий `_CHANNEL_REGISTRY`
рядом с `InAppChannel` / `LogChannel`. Каналы вызываются последовательно
из `dispatch()`; failure isolation уже есть (ADR-0024).

Auth: shared secret в headers. `notification_service` config:
`telegram_bot_url` + `telegram_internal_token`. Bot config зеркало:
`internal_service_token`. Bot сравнивает `X-Internal-Service-Token`
constant-time (`hmac.compare_digest`) — ровно тот же паттерн, что
webhook-secret (`X-Telegram-Bot-Api-Secret-Token`) из Phase 14.0.

**HMAC vs static token:** static token — простой, ротатя через secret
manager. HMAC-of-payload отвергнут — добавляет nonce/timestamp logic для
replay protection, что overkill для inter-service вызова в той же VPC.
Если позже шлюзуем bot через public LB, переход на HMAC + замена
`/notify` на signed-request endpoint — сама по себе небольшая миграция.

**user_id mapping (int Notification.user_id ↔ UUID
TelegramUserLink.user_id):** caller включает
`payload["telegram_user_id"]` (UUID-строка) при создании нотификации.
TelegramChannel читает оттуда. Без поля — silent skip. Это компенсирует
pre-existing tech-debt (`Notification.user_id BigInteger`) без миграции;
полная нормализация — отдельный Phase 4.10c.

**Sync HTTP vs queue:** sync. Notify-flow не критичен по latency
(idempotency-keyed), bot отвечает 200 даже когда не доставил → channel
успех/skip фиксируется в `channels_attempted`. Queue (Redis stream)
переусложнило бы скелет; вернёмся, если push-volume вырастет.

## Решение

1. **Bot reads via direct DB** — для всех 4 read-команд + /subscribe.
2. **Active tree** = first-owned by `created_at ASC` (member-trees вне
   scope).
3. **/subscribe** = single toggle, default off.
4. **TelegramChannel** в notification-service registry; sync HTTP POST
   с `X-Internal-Service-Token` shared-secret auth.
5. **`telegram_user_id` UUID** prober-supplied в `Notification.payload`.

## Тесты

- Unit (pure functions): `render_imports`, `render_persons`,
  `render_tree` — формат текста, deep-link генерация, inline keyboard
  shape.
- Integration (real DB testcontainer): `db_queries.py` против
  populated tree (resolve user, fetch imports/active tree, search
  persons, toggle notifications).
- Bot HTTP: `/telegram/notify` happy path + 401 (invalid token) + 503
  (no token configured) + 200/delivered=False (no link / unsubscribed).
- Notification channel: TelegramChannel via `httpx.MockTransport` —
  delivered=True, delivered=False (skip), HTTP error → False without
  raise.

## Последствия

- ✅ User получает actionable info прямо в Telegram, не уходя на web.
- ✅ Push-нотификации opt-in; privacy-by-default.
- ✅ Channel system unchanged — просто новый плагин.
- ❌ Bot имеет столько же DB-знаний, сколько parser-service. Refactor
  модели задевает оба места.
- ❌ `Notification.user_id` BigInteger остаётся pre-existing; bridge
  через payload — workaround, не решение. Phase 4.10c должен
  нормализовать FK.

## Когда пересмотреть

- Если push-volume превысит 100 msg/s: switch на queue (Redis stream
  - worker)
- Если bot и parser-service разъедутся в разные deploy-units (Phase
  13.1+ namespace separation): рассмотреть HTTP-вариант с service mesh
- Если нужен member-tree access (`/tree` показывает дерево, где user —
  editor): Phase 14.2 расширит `fetch_active_tree` через
  `tree_memberships`
