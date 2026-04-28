# ADR-0040: Telegram bot — scaffold and account-linking architecture

- **Status:** Accepted (Phase 14.0 — scaffold; Phase 14.1 wires notifications)
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `telegram`, `bot`, `phase-14`, `privacy`, `webhook`

**Phase 14.0 (this PR) ships:**

- `services/telegram-bot/` FastAPI service with `/healthz`, webhook
  receiver `POST /telegram/webhook`, and account-link confirmation
  `POST /telegram/link/confirm`.
- `aiogram 3.x` Dispatcher + Router with handlers for `/start`,
  `/imports`, `/persons <name>`, `/tree`.
- `telegram_user_links` ORM + Alembic migration `0018`.
- Redis-backed one-time link-token mint/consume (15 min TTL).
- Schema-invariants allowlist entry.
- Mocked outbound — no real `api.telegram.org` calls in tests
  (`httpx.MockTransport`).
- `.env.example` and `docker-compose.yml` entry.

**Phase 14.1 (deferred) will add:**

- notification-service → telegram-bot fan-out (per-event-type opt-in).
- Real parser-service / api-gateway calls inside `/imports`, `/persons`,
  `/tree` — currently stubbed with «привязка к данным появится в Phase
  14.1» messages so the bot is alive and routable but does not yet read
  user data.
- Service-to-service auth model (separate ADR — Phase 14.1 needs to
  decide how telegram-bot authenticates against parser-service on behalf
  of the linked user; candidates: machine token + impersonation header,
  or short-lived per-user JWT minted by api-gateway).
- Cloud Run module + Secret Manager bindings for
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET`.

## Контекст

ROADMAP §14 (Phase 14 Telegram-бот) ставит цели:

1. Push-уведомления о новых импортах, ДНК-матчах, гипотезах
   (через notification-service, отдельный канал — Phase 14.1).
2. Поиск персон и просмотр статуса импортов из мессенджера, без
   открытия web-у (Phase 14.0 — scaffold команд).
3. Onboarding-канал для пользователей, у которых нет привычки заходить
   на web ежедневно.

Phase 14.0 — это **скелет сервиса**, не интеграция. Critical-path для
14.0:

- Bot должен принимать webhook от Telegram.
- Bot должен валидировать `X-Telegram-Bot-Api-Secret-Token` (single
  shared secret, per-instance).
- Bot должен уметь сопоставить `tg_chat_id` с `user_id` в БД через
  явный opt-in flow с одноразовым токеном (CLAUDE.md §3.5
  «Privacy by design»: **никаких неявных привязок**).
- Bot должен иметь dispatch-router для команд, чтобы 14.1 просто
  заполнила тела хендлеров реальной интеграцией с parser-service.

CLAUDE.md §3.5 (Privacy by design) — главный driver архитектуры:

- `tg_chat_id` хранится **только** после явного opt-in на стороне
  пользователя. Сценарий: пользователь нажимает /start в боте → бот
  выдаёт one-time link `https://web.../telegram/link?token=...` →
  пользователь, уже залогиненный в web (Clerk session), открывает эту
  ссылку → web вызывает `POST /telegram/link/confirm` с (token,
  Clerk JWT) → telegram-bot подтверждает, кладёт строку в
  `telegram_user_links`. **Только после этого** bot знает, какому
  user'у соответствует данный chat.
- Revocation = `revoked_at` timestamp, не tombstone-soft-delete.
  В schema-invariants allowlist'е.

CLAUDE.md §5 (нет скрейпинга) — не релевантно (Telegram Bot API
официальный); §6 (strict mypy, ruff, > 80% покрытие) — релевантно.

ADR-0008 (CI/pre-commit parity) — релевантно: тесты не должны делать
сетевых вызовов, `httpx.MockTransport` для outbound, FastAPI TestClient
для inbound. Нет маркера `telegram_real`-варианта в Phase 14.0 — для
владельца это локальный smoke через ngrok / cloudflared, не CI.

## Рассмотренные варианты — выбор библиотеки

### Вариант A — `aiogram 3.x` *(выбран)*

- ✅ **Async-first** (наш стек целиком async — FastAPI, asyncpg,
  httpx). aiogram 3.x проектировался под asyncio с нуля.
- ✅ **Pydantic v2 нативно** — Update / Message / etc. модели —
  Pydantic v2, валидация совместима с нашим
  `shared_models.schemas`.
- ✅ **Router pattern** — `Dispatcher.include_router(router)`
  идеологически идентичен `app.include_router(router)` FastAPI;
  миграция между файлами cheap.
- ✅ **Webhook-friendly** — `Dispatcher.feed_update(bot, update)`
  принимает уже-распарсенный Update, что позволяет переиспользовать
  FastAPI endpoint без поднятия второго aiohttp-сервера, который
  тащит `aiogram.webhook.aiohttp_server`.
- ❌ Меньше community-плагинов чем у python-telegram-bot
  (но для scaffold'а нам они не нужны).
- ❌ В 3.x кое-где сменились API (vs 2.x), нужно осторожнее с
  туториалами из интернета — версия pinned в `pyproject.toml`.

### Вариант B — `python-telegram-bot` 21+

- ✅ Самая большая community/документация.
- ❌ В 21+ async-нативно, но архитектурно ориентируется на
  long-polling/Application loop. Webhook-режим есть, но менее
  естественен — обычно поднимают `Application.run_webhook(...)`,
  это второй HTTP-сервер, конфликтующий с нашим FastAPI.
- ❌ Не Pydantic — собственные dataclass-based типы, требуется
  отдельный мост для нашего schema-валидаторного слоя.
- ❌ Установка тащит больше транзитивных зависимостей
  (`tornado` для webhook-mode), что увеличивает supply-chain
  surface.

### Вариант C — голый httpx + собственные Pydantic-модели для Update

- ✅ Минимум зависимостей.
- ❌ Re-invent the wheel: вся типобезопасность Update / Message /
  CallbackQuery / InlineKeyboardButton — наши, поддерживаются
  вручную при изменениях Bot API.
- ❌ В Phase 14.1 при росте функциональности (inline-keyboards,
  callback queries, file uploads) внутренняя реализация догонит
  объём aiogram'а — preliminary abstraction для экономии 1 зависимости.

## Решение

**Вариант A — `aiogram 3.x`.**

Обоснование (4 предложения):

1. **Pydantic v2 + async-first** — единственный из трёх вариант,
   который не требует мостов между внутренними типами библиотеки и
   нашим стеком (`shared_models` тоже Pydantic v2).
2. **Webhook-mode без второго HTTP-сервера** — `Dispatcher.feed_update`
   принимает уже-распарсенный Update, наш существующий FastAPI
   endpoint остаётся единственной точкой входа, что упрощает
   деплой на Cloud Run (один контейнер = один порт).
3. **Router pattern совместим с нашим стилем кода** — `email-service`
   и `parser-service` уже используют FastAPI router-композицию;
   aiogram-router'ы для команд читаются точно так же.
4. **Cost вариант B/C** — overhead python-telegram-bot и собственных
   моделей был бы оправдан только если бы стек был sync или
   non-Pydantic; ни одно из этих условий не выполняется.

### Архитектурные выборы внутри варианта A

**Webhook security:**

- `X-Telegram-Bot-Api-Secret-Token` header валидируется на каждом
  запросе. Сервис конфигурируется секретом `TELEGRAM_WEBHOOK_SECRET`
  (32+ символа, генерируется владельцем, кладётся в Secret Manager).
- При валидации — constant-time compare (`hmac.compare_digest`),
  чтобы не утекал по timing'у.
- 401 ответ при mismatch'е, без тела (не подсказываем атакующему
  валидную форму).
- HTTPS-only (Telegram требует HTTPS на webhook URL); в локальной
  разработке — ngrok / cloudflared tunnel, который владелец поднимает
  сам.

**Account linking flow:**

```text
[user]                 [Telegram]              [telegram-bot]            [web/api-gateway]
  |  /start             |                       |                          |
  |-------------------->|                       |                          |
  |                     |  webhook update        |                          |
  |                     |----------------------->|                          |
  |                     |                       |  mint link_token (Redis,  |
  |                     |                       |  TTL 15min); bind to       |
  |                     |                       |  tg_chat_id + tg_user_id   |
  |                     |                       |                          |
  |                     |  reply: "Click https://web.../telegram/link?token=..."
  |                     |<-----------------------|                          |
  |  click link         |                       |                          |
  |--------------------------------------------------------------------->|
  |                     |                       |  POST /telegram/link/confirm  |
  |                     |                       |  body={token}, headers={Clerk JWT}
  |                     |                       |<-------------------------|
  |                     |                       |  consume token → INSERT  |
  |                     |                       |  telegram_user_links     |
  |                     |                       |  (user_id, tg_chat_id)   |
  |                     |                       |  return 200              |
  |                     |                       |------------------------->|
  |                     |                       |                          |
  |                     |  optional: confirmation message via Bot API      |
```

Ключевые свойства:

- **Token — random 32 байта, base64url-encoded** (~43 символа). Хранится
  в Redis с TTL 900 секунд. Один раз consumed → удалён.
- **Token несёт `tg_chat_id` + `tg_user_id`** (не несёт Telegram
  user info — этого достаточно для бота). При confirm'е web-у
  предоставляет Clerk JWT (`user_id`); telegram-bot достаёт из
  Redis по token'у — `(tg_chat_id, tg_user_id)` — и записывает связь.
- **CSRF — token в URL-параметре**, но т.к. consume требует валидной
  Clerk-сессии (web-front проверяет на стороне `/telegram/link`
  страницы), CSRF-vector закрыт сессионной политикой Clerk'а.
  Дополнительно при confirm'е web передаёт `state`-параметр,
  привязанный к Clerk-сессии, чтобы исключить atom-link атаку
  (Phase 14.0 — TBD: отложено если scope перерастёт; web-страница
  пока требует, чтобы пользователь явно нажал «Связать», то есть
  не auto-confirm).

**Schema выбор для `telegram_user_links`:**

- `(user_id, tg_chat_id)` UNIQUE — один Telegram-аккаунт привязывается
  только к одному TreeGen-юзеру (и наоборот).
- `revoked_at` TIMESTAMP NULL — revocation = soft state, чтобы
  GDPR-аудит видел историю отзыва. Hard-delete — через
  GDPR-erasure pipeline.
- **Без `provenance`, без `version_id`, без `tree_id`** — это user
  setting, не доменный факт. Соответствует pattern'у
  `notification_preferences` (ADR-0029) и `email_send_log` (ADR-0039
  §«Schema»).
- **Schema-invariants allowlist** — таблица в `SERVICE_TABLES` set'е
  с комментарием, объясняющим mapping (per-user, opt-in, revocation
  = timestamp).

**Outbound — Bot API client:**

- Тонкий wrapper поверх aiogram'овского `Bot` (httpx-backed).
  Только `send_message(chat_id, text)` сейчас; форматирование —
  plain-text или MarkdownV2 (escape'ится через
  `aiogram.utils.markdown.escape_md`).
- В тестах — `httpx.MockTransport`, передаётся в `Bot(session=...)`
  через `aiogram.client.session.aiohttp.AiohttpSession` или
  через явный `httpx`-based session. Нет реальных вызовов в CI.

**Tests — unit-only:**

- `tests/test_webhook.py` — webhook signature validation (200/401),
  malformed payload (422), правильная diapatch'ация в router.
- `tests/test_handlers.py` — `/start` минтит token, `/imports`
  возвращает stub-сообщение Phase 14.0 (TODO 14.1).
- `tests/test_link_tokens.py` — mint / consume / TTL / replay-attack
  (consume дважды → 410).
- Coverage > 80% по новому коду — без интеграции с реальным
  Telegram API.

**Что отложено (out of scope Phase 14.0):**

- Реальный fetch user-данных в `/imports`, `/persons`, `/tree`
  (Phase 14.0b или Phase 14.1).
- notification-service → telegram-bot fan-out (Phase 14.1).
- Inline keyboards, callback queries, file uploads (Phase 14.x).
- Multi-language responses (сейчас English-only stub-сообщения,
  i18n в Phase 14.x опирается на `users.locale` уже после 14.1).
- `aiogram` middleware для rate-limiting per-chat (Phase 14.x;
  Telegram сам имеет per-bot quota, для scaffold'а его достаточно).

## Последствия

**Положительные:**

- Скелет переиспользуется: `services/telegram-bot/src/telegram_bot/`
  имеет ту же структуру (`config.py / database.py / api/ /
  services/`) что и `email-service` / `notification-service`. Phase
  14.1 добавляет хендлеры в `services/handlers.py`, не трогая
  scaffold.
- Privacy-by-design: opt-in flow жёстко вшит в схему БД (без
  `tg_chat_id` нельзя отправить сообщение, потому что нет связи с
  user_id) и в ADR.
- CI остаётся изолированным: ни один тест не делает реальных
  сетевых вызовов (ADR-0008 parity сохраняется).

**Отрицательные / стоимость:**

- aiogram 3.x — новая зависимость, добавляет ~5 транзитивных
  пакетов (`aiohttp`, `aiosignal`, `magic-filter` и т.д.).
- Phase 14.1 потребует решения cross-service-auth модели; до этого
  команды `/imports`, `/persons`, `/tree` отвечают stub'ами.

**Риски:**

- **Webhook-secret leak.** Если `TELEGRAM_WEBHOOK_SECRET` утечёт,
  атакующий сможет посылать поддельные update'ы.
  *Mitigation:* secret в Secret Manager, не в коде/логах; ротация
  при подозрении (не привязан ни к чему стейтфул);
  `hmac.compare_digest` для constant-time compare.
- **Token replay.** Если link-token утечёт во время 15-min TTL до
  consume, атакующий с валидной Clerk-сессией другого пользователя
  сможет привязать к себе чужой `tg_chat_id`.
  *Mitigation:* TTL короткий, single-use, в Redis с атомарным
  `GETDEL`. Дополнительно: возможна привязка `state`-параметра к
  user_agent / IP — отложено в Phase 14.x если нужно.
- **`tg_chat_id` numeric collision** — Telegram гарантирует
  уникальность chat_id глобально, но миграции 14.x могут
  столкнуться с migration в формате (group/private). Текущая
  схема — INTEGER → BIGINT (Telegram использует int64).
  *Mitigation:* `BIGINT` колонка с самого начала.
- **aiogram breaking changes 3.x → 4.x.** *Mitigation:* version
  pinned (`aiogram>=3.13,<4.0`), bump через ADR-update.

**Что нужно сделать в коде (Phase 14.0 PR):**

1. `services/telegram-bot/` — pyproject.toml, src-layout, py.typed.
2. Регистрация в `[tool.uv.workspace]` и `[tool.uv.sources]`
   корневого `pyproject.toml`. `uv lock`.
3. `config.py` — Settings (TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET,
   TELEGRAM_BOT_LINK_TTL_SECONDS, TELEGRAM_WEB_BASE_URL).
4. `database.py` — async engine (clone от email-service).
5. `api/health.py` — `/healthz` (без external reachability check —
   Telegram-bot не должен пинговать api.telegram.org каждый probe).
6. `api/webhook.py` — `POST /telegram/webhook`, secret validation,
   `Dispatcher.feed_webhook_update`.
7. `api/link.py` — `POST /telegram/link/confirm`, consume token,
   insert `telegram_user_links`.
8. `services/link_tokens.py` — Redis-backed mint/consume.
9. `services/command_router.py` — aiogram Router с `/start`,
   `/imports`, `/persons`, `/tree` handlers (4 stub'а + `/start`
   реальный).
10. `services/bot_client.py` — тонкий wrapper для outbound
    `send_message`.
11. ORM `TelegramUserLink` + alembic 0018 + schema_invariants
    allowlist.
12. `.env.example` дополнен; `docker-compose.yml` — service entry.
13. Tests: webhook validation, command parsing, /start link mint,
    link confirm flow, replay-attack rejection.

## Когда пересмотреть

- **aiogram 3.x deprecated / 4.0 released.** → bump pin, проверить
  breaking changes (особенно Update схема), новый ADR-update.
- **Появляется второй FastAPI-based Bot API клиент с Pydantic v2.** →
  переоценка по простоте интеграции.
- **Telegram добавляет webhook signature через HMAC** (сейчас только
  shared secret в header'е). → перейти на HMAC + payload-подпись,
  усилить валидацию, ADR-update.
- **Phase 14.1 решает cross-service-auth модель** → отдельный ADR
  на parser-service-from-telegram-bot вызовы; этот ADR не требует
  изменений.

## Ссылки

- Связанные ADR:
  - [ADR-0024](./0024-notification-service-architecture.md) —
    notification delivery (Phase 14.1 будет fan-out'ом из
    notification-service в этот сервис).
  - [ADR-0029](./0029-notification-delivery-model.md) —
    `notification_preferences` pattern; `telegram_user_links`
    повторяет его (per-user, без soft-delete).
  - [ADR-0036](./0036-sharing-permissions-model.md) — auth
    references; cross-service auth для команд решается в Phase
    14.1.
  - [ADR-0008](./0008-ci-precommit-parity.md) — CI parity; нет
    `telegram_real`-маркера в 14.0, owner smoke-тестит локально.
- External:
  - [Telegram Bot API](https://core.telegram.org/bots/api)
  - [Webhook secret token](https://core.telegram.org/bots/api#setwebhook)
    — параметр `secret_token`, передаётся обратно в
    `X-Telegram-Bot-Api-Secret-Token` header.
  - [aiogram 3.x docs](https://docs.aiogram.dev/en/latest/)
  - [aiogram Dispatcher.feed_webhook_update](https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html)
