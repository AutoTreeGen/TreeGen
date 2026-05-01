# Agent 3 — Phase 14.2: Telegram inline-search + weekly digest

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (`F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md`.
2. `ROADMAP.md` — «Фаза 14 — Telegram-бот и автоматизация», особенно §14 «Phase 14.2+ (future)».
3. ADR-0040 (Phase 14.0 bot scaffold), последние коммиты Phase 14.0 (#127) и Phase 14.1 (#140) — изучи `services/telegram-bot/` целиком, понимай существующую архитектуру (account linking, обработчики команд, как notification-service шлёт push в бот).
4. `services/parser-service/api/persons.py` — search endpoint, который будешь дёргать из inline-search.

## Задача

Расширить `services/telegram-bot` на: (1) inline-search по дереву, (2) weekly digest worker.

## Scope

### 1. Inline-search

- Хендлер `aiogram` `InlineQueryHandler`: `@bot Иванов 1850` → top-5 person matches.
- Парсинг query: surname (обязательно), optional given (после первого слова), optional year (4-значное число).
- Вызов parser-service внутренним HTTP-клиентом по `GET /trees/{user_default_tree}/persons/search?surname=...&given=...&year=...&phonetic=true&limit=5`. Использовать service-to-service auth (X-User-Id header, см. как сделано в `services/parser-service/auth.py`).
- Если у пользователя нет залинкованного аккаунта (нет `tg_user_id` в БД) — отдать пустой результат с подсказкой «Link your account: /start».
- Если linked, но deault_tree не выбрано — кнопка «Choose a tree» (deep-link в web `/dashboard?from=tg`).
- Каждый результат — `InlineQueryResultArticle` с `title=display_name`, `description=BIRT date • place`, `input_message_content` = deep-link `https://app.example.com/persons/{id}?from=tg` + краткое summary, `thumb_url` если есть (заглушка ок).

### 2. Weekly digest worker

- Новый arq job `send_weekly_digest` в `services/telegram-bot/src/telegram_bot/jobs/digest.py`.
- Cron schedule (через `arq.cron`): каждый понедельник 09:00 UTC. **Зарегистрируй в существующем worker, не создавай новый процесс.**
- Для каждого linked Telegram user:
  - Запрос к parser-service: `GET /users/{id}/digest-summary?since=<7d_ago>` — нужно создать **только этот эндпоинт** в parser-service (минимальное изменение). Возвращает: `new_persons_count`, `new_hypotheses_pending`, `top_3_recent_persons[]`.
  - Формирование сообщения (ru/en по `user.locale`):
    - «За неделю: 12 новых персон, 3 гипотезы ждут проверки».
    - Top-3 person cards с deep-link.
  - Отправка через `bot.send_message(chat_id, ..., parse_mode="HTML")`.
- Idempotency: лог `DigestSendLog(user_id, period_start, period_end, sent_at)` в Redis на 60 дней (НЕ создавай таблицу — это Phase 14.3, в Redis достаточно).
- Кнопка отписки в каждом сообщении: callback_data `digest:unsubscribe` → выставляет `user_settings.digest_enabled=false` (см. как Phase 4.10b сделал user settings).

## Тесты (> 80%)

- `services/telegram-bot/tests/test_inline_search.py` — мок parser-service, проверить парсинг query, формат `InlineQueryResultArticle`, обработку unlinked user, year в query.
- `services/telegram-bot/tests/test_digest.py` — мок parser-service + мок bot.send_message, проверить cron-schedule, idempotency через Redis, локализацию, обработку 0 событий (skip отправки).
- `services/parser-service/tests/test_digest_summary.py` — интеграционный на новый эндпоинт.

## Запреты

- ❌ Alembic-миграции (используй Redis для DigestSendLog).
- ❌ `packages/shared-models/` (новые поля в существующих моделях — нет; только новый Pydantic schema внутри parser-service).
- ❌ `apps/web/messages/*.json`.
- ❌ Корневой `pyproject.toml`.

## Процесс

1. `git checkout -b feat/phase-14.2-telegram-digest`
2. Коммиты: `feat(telegram-bot): inline search`, `feat(parser-service): digest summary endpoint`, `feat(telegram-bot): weekly digest worker`, `test(...)`.
3. `uv run pre-commit run --all-files` + `uv run pytest services/telegram-bot services/parser-service` перед каждым коммитом.
4. **НЕ мержить, НЕ пушить в `main`.**

## Финальный отчёт

- Ветка, коммиты, pytest, файлы, как локально потестить inline-search вручную (BotFather Inline mode setup steps), env-vars если новые.
