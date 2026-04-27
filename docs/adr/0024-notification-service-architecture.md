# ADR-0024: Notification service architecture (Phase 8.0 skeleton)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `notifications`, `service`, `delivery`, `idempotency`, `channels`

## Контекст

После Phase 4-7 в системе три источника событий, требующих внимания
пользователя:

- **Hypothesis review queue** (Phase 4.5/4.6) — пары кандидатов на merge
  с composite_score, которые owner дерева должен подтвердить или отклонить.
- **Inference engine background runs** (Phase 7.3) — новые гипотезы,
  сгенерированные ночным rule-pipeline'ом.
- **FamilySearch import dedup suggestions** (Phase 5.2) — пары source
  records, которые могут быть одним и тем же.

Сейчас обнаружить эти события можно только активным заходом в
соответствующий UI-раздел. На неактивных пользователях очереди копятся,
review-rate падает. Нужен поверх этого тонкий слой, который проактивно
доставит «у тебя 5 новых hypotheses на ревью» в выбранный канал.

При этом в Phase 8.0 ещё нет:

- Auth слоя — `user_id` пока *просто* числовая ссылка без FK на
  отсутствующую `users` таблицу. Делать FK нельзя — таблицы нет;
  откладывать сервис до auth — тоже нельзя, потому что без нотификаций
  Phase 4-7 деградируют сразу после shipping.
- Email-инфраструктуры (SMTP / SES / sender-domain reputation) —
  Phase 8.1.
- Real-time push (WebSocket / SSE) — Phase 8.3.
- User-preferences UI («muted channels», «digest frequency») —
  Phase 4.x follow-up.

Phase 8.0 задача — **скелет**, чтобы любой другой сервис мог сделать
один HTTP-вызов `POST /notify` и не знать, как именно мы доставим
событие пользователю.

## Рассмотренные варианты

### Вариант A — Inline нотификации внутри сервисов-источников

Каждый сервис (parser-service, dna-service, …) сам пишет в `notifications`
таблицу. Нет отдельного notification-service.

- ✅ Минимум кода, нет ещё одного процесса.
- ❌ Каждый сервис обязан знать список каналов, idempotency-policy,
  template'ы. Дублирование в N сервисах. Любое изменение
  delivery-логики — N PR'ов.
- ❌ Невозможно добавить новый канал (email / push) без правок во всех
  сервисах-источниках.
- ❌ Поверх дублирования — нет единой точки для retry / digest.

### Вариант B — Брокер сообщений (Redis pub/sub / Cloud Tasks)

Сервисы-источники публикуют события; notification-worker подписан и
доставляет.

- ✅ Decoupled, scalable.
- ❌ Для 8.0 это инфраструктурный overshoot: нужно поднимать брокер,
  топологию очередей, retry/poison-queue policy, observability.
- ❌ At-least-once семантика брокера + наша idempotency = два уровня
  идемпотентности; сложнее тестировать.
- ❌ Phase 8.0 — это **skeleton**, не production-grade fan-out. Брокер
  откладываем до Phase 8.3 (real-time), когда он действительно понадобится.

### Вариант C — Отдельный HTTP-сервис с channel abstraction (выбран)

Новый `services/notification-service/` со своим FastAPI app:

- Внутренний эндпоинт `POST /notify` — вызывают сервисы-источники.
- End-user эндпоинты `GET /users/me/notifications` + `PATCH /…/read`
  — для frontend.
- Channel abstraction (Protocol per PEP 544) с реализациями
  `InAppChannel` (запись в БД) и `LogChannel` (debug). Будущие
  `EmailChannel` / `PushChannel` подключаются через тот же Protocol
  без правок api-слоя.
- Хранилище — таблица `notifications` (см. Phase 8.0 миграция).
- Idempotency через `idempotency_key = (user_id, event_type, ref_id)`
  с окном 1 час.

- ✅ Чёткий контракт для всех будущих consumer'ов: один HTTP-вызов.
- ✅ Channel-Protocol позволяет добавить email в Phase 8.1 без
  изменений вызывающих сервисов.
- ✅ Идемпотентность централизована в одном месте.
- ✅ Logical decoupling, без брокера и его operational overhead.
- ❌ Ещё один процесс. Но это «маленький» процесс — деплоится тем же
  Dockerfile-паттерном что и parser-service / dna-service.
- ❌ Sync HTTP — звонящий ждёт ответа dispatcher'а. На скейле может
  стать узким местом, тогда и поднимем брокер (Phase 8.3).

## Решение

Выбран **Вариант C**.

Делаем `services/notification-service/` рядом с другими сервисами,
с тем же FastAPI scaffold (см. `dna-service/`). Channel abstraction
— Protocol; реализации `InAppChannel` и `LogChannel`. Idempotency —
unique partial index в БД `(user_id, event_type, idempotency_key)`,
fallback — check-and-insert внутри транзакции, если PostgreSQL версия
не поддерживает partial unique index с предикатом времени.

## Последствия

**Положительные:**

- Единый proto для нотификаций по всему проекту: `POST /notify` с
  `{user_id, event_type, payload, channels}`.
- Phase 4 frontend получает `GET /users/me/notifications` для бейджа
  unread в шапке + drawer / page для списка.
- Channel-Protocol fronts a clear extension path для email / push.
- Idempotency защищает от дубликатов при retry в сервисах-источниках:
  «inference engine крашнулся в середине ночного прогона и был
  перезапущен» больше не означает 2 копии каждой нотификации.

**Отрицательные / стоимость:**

- Новый сервис → новые ops: deploy, healthz, logs, alerting. Mitigation:
  копируем известный шаблон из dna-service — никакой новой инфры не
  нужно сверх существующих docker-compose / Cloud Run.
- `user_id` без FK на отсутствующие `users` — слабая целостность.
  Mitigation: явная notes в коде + миграция на FK как только Phase 4.x
  auth добавит таблицу `users` (или mvp ускорит появление этой таблицы).
- Sync HTTP-fan-out не масштабируется до тысяч нотификаций / минуту.
  Не проблема в Phase 8.0 (по верхней оценке — десятки нотификаций /
  день / пользователь). Перейдём на брокер в Phase 8.3 если упрёмся.

**Риски:**

- Каналы — фактический интерфейс реального мира; LogChannel может
  замолчать без явной ошибки. Mitigation: `delivered_at` ставится только
  если `Channel.send()` вернул `True`; `channels_attempted` — JSON-список
  пар `(channel_name, success_bool)` для аудита.
- Idempotency-окно 1 час подобрано на вкус; если событие повторяется
  легитимно (например, новый review в той же гипотезе через 30 минут),
  оно будет проглочено. Mitigation: idempotency-ключ включает не только
  `(user_id, event_type)` но и `payload.ref_id` — повторное событие про
  «другую» сущность пройдёт.
- Channel failure isolation: если один канал падает (например LogChannel
  кидает IOError), это **не должно** ломать остальные. Реализуем
  явным per-channel try/except в dispatcher с записью результата в
  `channels_attempted`. Тест на это обязателен.

**Что нужно сделать в коде (Phase 8.0):**

1. ORM `Notification` + alembic миграция (таблица + индексы).
2. `services/notification-service/` scaffold — FastAPI app, settings,
   database init, healthz, тесты по образцу dna-service.
3. `channels/in_app.py`, `channels/log.py` + Protocol в `channels/base.py`.
4. `services/dispatcher.py` — channel routing + idempotency + failure
   isolation.
5. Internal endpoint `POST /notify`.
6. End-user endpoints `GET /users/me/notifications`,
   `PATCH /notifications/{id}/read` (auth — пока mock через
   `X-User-Id` header, TODO заменить на JWT extraction в Phase 4 auth).
7. Dockerfile (тот же паттерн что parser-service / dna-service).
8. Тесты: healthz, dispatcher (idempotency, channel failure isolation,
   unknown event_type → 400), api (POST /notify happy + idempotent
   replay; GET /users/me/notifications + PATCH /…/read).

## Когда пересмотреть

- При появлении email-канала (Phase 8.1) — пересмотреть retry-policy
  и template-storage (templates сейчас inline в коде; для email с
  HTML-шаблонами понадобится отдельная директория).
- При >100 нотификаций / минуту устойчиво — пересмотреть синхронный
  HTTP-fan-out на брокер (Phase 8.3).
- При появлении auth (`users` таблица) — добавить FK
  `notifications.user_id → users.id` миграцией; пересмотреть mock
  `X-User-Id` header в end-user endpoints.
- При user-preferences UI (digest frequency, channel mute) — расширить
  таблицу + dispatcher с per-user filtering.

## Ссылки

- Связанные ADR: ADR-0020 (dna-service architecture — тот же scaffold
  pattern), ADR-0021 (Hypothesis persistence — основной consumer
  нотификаций для review queue).
- ROADMAP §8.0.
- Phase 4.5/4.6 PRs (#75, #77, #82) — review queue, для которой
  нотификации становятся проактивным каналом доставки.
