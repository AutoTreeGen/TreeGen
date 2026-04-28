# ADR-0029: Notification delivery model — async enqueue + per-user prefs

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `backend`, `frontend`, `notifications`, `phase-8`
- **Supersedes-in-part:** ADR-0024 §«Delivery semantics»

## Контекст

ADR-0024 зафиксировал скелет notification-service (channel routing,
idempotency, in_app/log каналы, internal `POST /notify`). После Phase
4.9 (hypothesis review UI) parser-service шлёт fire-and-forget HTTP
POST в notification-service напрямую из транзакции `hypothesis_runner`
(см. `services/parser-service/src/parser_service/services/notifications.py`).

Это работает для скелета, но даёт три проблемы по мере того, как
notification-service становится частью основного flow:

1. **Liveness coupling.** Падение/таймаут notification-service делает
   импорт/инференс наблюдаемо медленнее (HTTP-таймаут на 2 секунды
   внутри транзакции). Сейчас log-warning + return — но это маскирует
   потерю нотификаций без alerting'а.
2. **Нет ретраев.** Сетевая ошибка → нотификация просто потеряна.
   Idempotency-окно на стороне notification-service защищает только от
   *двойной* отправки, не от *никакой*.
3. **Нет user-level контроля.** Пользователь не может отключить
   `hypothesis_pending_review` или включить только `dna_match_found`.
   Все события пробиваются в шапку UI без фильтра.

Phase 8.0 (skeleton) вынес это явно как «Phase 8.x follow-up». Этот
ADR оформляет «follow-up» в принятый план.

## Решение

Три изменения, идущие одним PR (Phase 8.0 wire-up):

### 1. parser-service → notification-service через arq

`hypothesis_runner` остаётся синхронным caller'ом
`notify_hypothesis_pending_review(...)`, но эта функция теперь:

```python
async def notify_hypothesis_pending_review(...) -> None:
    pool = await get_arq_pool()
    await pool.enqueue_job("dispatch_notification_job", payload)
```

Воркер исполняет HTTP POST в `notification-service`. arq даёт:

- **Backoff + retry** из коробки (`max_tries`, `retry_delay`).
- **Изоляцию latency** — основной транзакции не нужно ждать сети.
- **Observability** — failed jobs видны в arq dashboard / `arq:result`.
- **Уже задеплоенный Redis** (ADR-0026) — нулевая новая инфраструктура.

Если notification-service недоступен надолго, события копятся в Redis
до `job_timeout` (1 час) и затем падают как failed — это at-least-once
с явной точкой отсечения.

### 2. NotificationPreference ORM + per-user toggles

Новая таблица `notification_preferences`:

```text
PK (user_id, event_type)
enabled  bool   default true
channels jsonb  default '["in_app", "log"]'
```

Если строки нет → дефолты (всё включено). Это «opt-out», а не
«opt-in», что соответствует MVP-предположению Phase 8.0 («хотим, чтобы
пользователь сразу видел гипотезы»).

`Dispatcher` при `dispatch(...)`:

1. Проверяет `(user_id, event_type)` в `notification_preferences`.
2. Если `enabled=False` → ранний возврат `DispatchOutcome(notification_id=None,
   delivered_channels=[], deduplicated=False, skipped_by_pref=True)`.
   Запись в `notifications` **не создаётся** — это намеренно, чтобы
   GET /users/me/notifications не показывал «отключённые» события и не
   создавал чувство «сломалось» через unread-counter.
3. Если `enabled=True`, requested channels пересекаются с
   `prefs.channels`. Канал, отсутствующий в `prefs.channels`,
   попадает в `channels_attempted` как `{"channel": ..., "success":
   false, "skipped": "user_pref"}` — для аудита, но без вызова
   `Channel.send()`.

### 3. End-user API + frontend

- `GET /users/me/notification-preferences` — полная карта (event_type
  → enabled + channels). Дефолты материализуются на лету.
- `PATCH /users/me/notification-preferences/{event_type}` — upsert
  одной строки.
- Bell в шапке (`apps/web/src/components/notification-bell.tsx`):
  badge с unread-count, dropdown — последние 10 unread, click →
  PATCH /read + navigate.
- `apps/web/src/app/settings/notifications/page.tsx` — таблица
  «event_type → enabled toggle».

Auth остаётся mock (`X-User-Id` header) — единый toggle с
notification API; реальный JWT прилетит в Phase 4.x вместе с auth-слоем.

## Рассмотренные альтернативы

### А1. Прямой httpx POST + ретраи в parser-service

Добавить `tenacity` retry-loop вокруг существующего `httpx.AsyncClient.post`.

- ✅ Минимум кода.
- ❌ Ретраи внутри transaction commit'а — увеличивают время удержания
  блокировок на person/hypothesis rows.
- ❌ Нет durability: рестарт parser-service во время ретрая → потеря.
- ❌ Дублирует поведение arq, который у нас уже есть (ADR-0026).

### А2. Synchronous calls + outbox pattern

Записать событие в `notification_outbox` table, отдельный poller
читает и отправляет.

- ✅ Полный durability.
- ❌ Новая таблица + новый процесс-poller. arq уже даёт оба.
- ❌ Polling latency vs push. arq isr push.

### А3. Per-user prefs в JWT claim

Когда auth появится, держать prefs прямо в токене.

- ✅ Никакой DB lookup на каждый dispatch.
- ❌ Размер токена растёт с числом event_type'ов.
- ❌ Изменение prefs требует issue нового токена / refresh.
- ❌ Server-side dispatcher всё равно должен знать prefs (для
  job-инициированных нотификаций без user'ского контекста).

Выбрали ORM-таблицу — стандартный pattern, явный и тестируемый.

### А4. Запись «отключённых» нотификаций как row с marker-полем

Сохранять row с `delivered_at=None` + `disabled_by_pref=true`, но
GET-эндпоинт фильтрует.

- ✅ Полный аудит того, что событие *произошло*.
- ❌ Раздувает таблицу нотификациями, которые никто никогда не
  увидит. Audit факта произошедшего события — в доменных таблицах
  (hypothesis row, dna_match row), не в notifications.

Выбрали «не создавать row» — `notifications` это «inbox», не
«audit-log».

## Последствия

### Положительные

- API-эндпоинты parser-service не блокируются на сетевой задержке
  notification-service. P95 на `compute_hypothesis` падает
  предсказуемо.
- User получает контроль через UI — критично перед публичным
  open-beta (CLAUDE.md §5 Privacy by design: opt-out per
  notification type).
- Retry / observability — бесплатно от arq.
- Phase 8.1 (email) встраивается без изменения dispatch-контракта:
  новый Channel + расширение `prefs.channels`.

### Отрицательные / стоимость

- +1 arq job в очереди `imports` (название устаревает — в Phase 9
  переименуем в `default` либо сделаем отдельную очередь
  `notifications`). Сейчас не блокирует.
- Время от события до доставки растёт с ~50мс (синхронный POST) до
  ~200–500мс (enqueue + worker pickup). Для in-app inbox это
  приемлемо; для будущего email — допустимо.
- Тестам hypothesis_runner нужно мокать arq pool (уже мокался для
  import flow — pattern переиспользован).

### Риски

- Воркер падает на середине retry-loop'а notification job → arq
  retry поднимет другой воркер, idempotency-key защитит от дубля
  на стороне notification-service.
- Если `notification_preferences` table недоступна (DB hiccup) —
  dispatcher fail-open: считает что всё включено. Лучше доставить
  лишнее, чем пропустить из-за инфраструктурного сбоя.

## Когда пересмотреть

- Phase 8.1 (email) добавит SMTP-канал — оценить, нужно ли отдельное
  rate-limiting per-user (anti-spam).
- Phase 8.3 (real-time WebSocket) может потребовать push-канал, который
  не идёт через ту же arq-очередь (latency-критичен). Введём
  отдельный adapter, не меняя `dispatch()`-контракт.
- Если объём notifications вырастет до >10 событий/сек на одного
  user'а — переходим с `enabled` boolean на digest-окно
  («не чаще раза в N минут»).

## Ссылки

- ADR-0024 — notification-service architecture (skeleton).
- ADR-0026 — arq как очередь фоновых задач.
- ROADMAP §8 — Phase 8 notifications.
- `docs/agent-briefs/phase-8-0-notification-service.md`.
