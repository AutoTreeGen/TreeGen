# Agent brief — Phase 8.0: Notification service skeleton

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-notify`.
> Чистая территория — никто другой не пишет в `services/notification-service/`.
> Перед стартом: `CLAUDE.md`, `ROADMAP.md` §8, существующие сервисы для
> референса (`services/dna-service/` и `services/parser-service/` —
> та же FastAPI-структура).

---

## Зачем

После Phase 4-7 у нас есть:

- Hypothesis review queue (Phase 4.5/4.6) — пользователь должен ревьюить.
- DNA inference rules (Phase 7.3) — генерируют новые гипотезы fone night.
- FS-import dedup suggestions (Phase 5.2) — тоже требуют внимания.

Без notification service пользователь должен **активно** заходить и
проверять очереди. С ним — получает email/in-app digest:
«5 new hypotheses to review, 2 high-confidence DNA matches found».

MVP — только in-app + log channel. Email — отдельная фаза с SMTP/SES
конфигурацией. Цель этой фазы: **протокольный скелет**, чтобы любой
другой сервис мог просто `notify(user_id, type, payload)` без знания
о deliveryмеханизмах.

---

## Что НЕ делать

- ❌ Реальная отправка email — Phase 8.1.
- ❌ SMS / push — Phase 8.2+.
- ❌ Real-time WebSocket — Phase 8.3.
- ❌ User preferences UI — Phase 4.x follow-up.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — ADR-0024: notification architecture

**Файл:** `docs/adr/0024-notification-service-architecture.md`

Зафиксируй:

1. **Channel abstraction**: Protocol с методами `send(notif: Notification) -> bool`.
   Реализации: `InAppChannel`, `LogChannel`, future `EmailChannel`.
2. **Event types**: enum (`hypothesis_pending_review`, `dna_match_found`,
   `import_completed`, `import_failed`, `merge_undone`, etc.). Каждый
   тип — свой шаблон сообщения.
3. **Delivery semantics**: at-least-once. Idempotency key — `(user_id,
   event_type, ref_id)`; повторная отправка одного события за окно
   1 час пропускается.
4. **Storage**: `notifications` table — id, user_id, event_type, payload
   (jsonb), channels_attempted (jsonb), delivered_at, read_at,
   created_at. Indexes на (user_id, read_at), (event_type, created_at).
5. **API**: `POST /notifications` (внутренний, от других сервисов),
   `GET /users/me/notifications` (для frontend), `PATCH /notifications/{id}/read`.
6. **No user accounts yet**: пока auth не реализован, `user_id` —
   просто int, без FK на users table (которой ещё нет). Когда будет
   auth — добавим FK миграцией.

### Task 2 — Service scaffold

**Файлы:** `services/notification-service/`:

- `pyproject.toml`
- `src/notification_service/__init__.py`
- `src/notification_service/api/health.py` (`GET /healthz`)
- `src/notification_service/api/notifications.py` (CRUD)
- `src/notification_service/services/dispatcher.py` (channel routing)
- `src/notification_service/channels/in_app.py`
- `src/notification_service/channels/log.py`
- `src/notification_service/main.py` (FastAPI app)
- `Dockerfile`
- `tests/test_health.py`, `tests/test_dispatcher.py`,
  `tests/test_api.py`

Структуру копируй с `services/dna-service/` (там уже отлажена
паттерн) — тот же FastAPI scaffold, ASGITransport tests, etc.

### Task 3 — Notification ORM

**Файл:** `packages/shared-models/src/.../orm.py`

```python
class Notification(Base):
    __tablename__ = "notifications"
    id, user_id, event_type, payload (jsonb),
    idempotency_key (str, indexed unique partial),
    channels_attempted (jsonb),
    delivered_at, read_at, created_at
```

Alembic миграция. Index на `(user_id, read_at)` для unread counter.
Unique constraint на `idempotency_key` WHERE created_at > NOW() - 1 hour
(partial unique index).

⚠ Watch: `orm.py` сейчас спокойно (последние shipping waves слились
без коллизий) — но git pull --rebase перед commit обязательно.

### Task 4 — Channel implementations

**InAppChannel:** просто записывает в БД (Notification record уже есть,
канал помечает `channels_attempted += "in_app"` + `delivered_at`).

**LogChannel:** `logger.info(f"[notify] user={user_id} type={event_type}")`.
Полезно для дебага.

### Task 5 — Internal API для других сервисов

**Endpoint:** `POST /notify` body:

```json
{
  "user_id": 1,
  "event_type": "hypothesis_pending_review",
  "payload": {"hypothesis_id": 42, "tree_id": 1},
  "channels": ["in_app", "log"]
}
```

Response: `{"notification_id": 123, "delivered": ["in_app", "log"]}`.

Тесты:

- Idempotency: same `(user_id, event_type, payload.ref_id)` дважды
  за час → второй call возвращает существующий `notification_id`.
- Channel failure isolation: log падает → in_app всё равно delivered.
- Unknown event_type → 400 с понятным error.

### Task 6 — End-user API

`GET /users/me/notifications?unread=true&limit=20` — пагинация.
`PATCH /notifications/{id}/read` — отметить прочитанным.

(Auth пока mock — header `X-User-Id: 1`. Когда auth появится — заменим
на JWT extraction.)

### Task 7 — Финал

1. ROADMAP §8.0 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-8.0-notification-service-skeleton`.
4. CI green до merge. Никакого `--no-verify`.
5. PR description: что есть, чего нет (email — 8.1, real-time — 8.3),
   как другие сервисы должны это использовать (1-line example).

---

## Сигналы успеха

1. ✅ ADR-0024 в `docs/adr/`.
2. ✅ services/notification-service/ scaffold (FastAPI, /healthz, tests).
3. ✅ ORM + миграция в main.
4. ✅ Idempotency в окне 1 час работает.
5. ✅ Channel failure isolation работает.
6. ✅ Internal POST /notify + end-user GET endpoints рабочие.

---

## Если застрял

- Mock auth непонятен → header X-User-Id, fail-open для localhost,
  TODO в PR.
- Idempotency partial unique index не поддерживается на текущей версии
  Postgres → fallback на check-and-insert в транзакции.
- Channel Protocol vs ABC — выбирай Protocol (PEP 544), как в
  `inference-engine` plugins.

Удачи.
