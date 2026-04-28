# telegram-bot

AutoTreeGen Telegram bot — webhook receiver + opt-in account linking
(Phase 14.0). См. ADR-0040.

## Запуск (local dev)

```bash
uv sync --all-extras --all-packages
uv run uvicorn telegram_bot.main:app --reload --port 8006
```

Webhook URL для Telegram должен быть HTTPS — поднимай `cloudflared` или
`ngrok` локально и указывай в `setWebhook` его адрес.

## ENV (см. `.env.example`)

* `TELEGRAM_BOT_TOKEN` — bot-токен от `@BotFather`. Без него сервис
  стартует, но любые исходящие вызовы и валидация `/start` упадут.
* `TELEGRAM_WEBHOOK_SECRET` — 32+ символа, передаётся в Telegram при
  `setWebhook(secret_token=...)`. Bot валидирует
  `X-Telegram-Bot-Api-Secret-Token` header constant-time
  (`hmac.compare_digest`).
* `TELEGRAM_BOT_LINK_TTL_SECONDS` — TTL one-time link-токена в Redis
  (default 900 = 15 min).
* `TELEGRAM_BOT_WEB_BASE_URL` — база web-у для генерации
  `/telegram/link?token=...` ссылок.
* `REDIS_URL`, `DATABASE_URL` — стандартные.

## Acccount-link flow

См. ADR-0040 §«Account linking flow». В кратце:

1. Пользователь шлёт `/start` боту.
2. Bot минтит токен в Redis, кладёт `(tg_chat_id, tg_user_id)` под
   ключом, отвечает ссылкой на web.
3. Пользователь, залогиненный в web, открывает ссылку.
4. Web вызывает `POST /telegram/link/confirm` с `(token, Clerk-JWT)`.
5. Bot consumes токен, INSERT в `telegram_user_links`, отправляет
   confirmation-сообщение в чат.

Без шага 3 связь не создаётся — privacy-by-design (CLAUDE.md §3.5).

## Phase 14.0 vs 14.1 scope

* Phase 14.0 (this PR): scaffold, `/start` end-to-end, остальные команды
  (`/imports`, `/persons`, `/tree`) — stub'ы с сообщением «появится в
  Phase 14.1». ORM, миграция, ADR, тесты.
* Phase 14.1: реальный fetch user-data из parser-service +
  notification-service → telegram-bot fan-out.
