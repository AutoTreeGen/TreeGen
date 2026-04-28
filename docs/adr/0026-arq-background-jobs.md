# ADR-0026: arq как очередь фоновых задач (импорты, bulk-инференс)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `backend`, `queue`, `phase-3`, `infrastructure`

## Контекст

Phase 3.4 закрыта: `POST /imports` принимает GEDCOM, `import_runner`
парсит и раскладывает по таблицам. Но импорт **синхронный**: вся работа
происходит внутри HTTP-запроса. Реальные пользовательские GED-файлы
(Ancestry с медиа, MyHeritage) — 70+ МБ. Парсинг и bulk-INSERT занимают
минуты, в течение которых:

- HTTP-запрос держит коннект, рискует упереться в proxy/load-balancer
  timeout (Cloudflare 100s, GCP HTTPS LB 30s по умолчанию).
- API-воркер занят и не отвечает на другие запросы — single-threaded
  ASGI loop.
- Клиент не видит прогресса. Кнопка «Импорт» либо «крутится бесконечно»,
  либо отваливается с timeout — и пользователь не знает, импорт прошёл
  или нет.

Phase 7.5 (bulk hypothesis compute по всему дереву) и Phase 5.x
(FamilySearch sync — пагинация по REST API) имеют ту же форму: «долгая
работа, прогресс, возможность отмены». Решать одну точку отдельно от
другой — двойная работа и двойная инфра.

CLAUDE.md §4 уже фиксирует `arq` на Redis в качестве локального стека
очередей; формальное архитектурное решение для этого не оформлено.
Этот ADR закрывает этот gap до старта Phase 3.5.

## Рассмотренные варианты

### Вариант A — arq на Redis (рекомендую)

Лёгкий async-friendly job runner поверх Redis. Воркер — отдельный
процесс, читает задачи из Redis-стрима, исполняет coroutine.

- ✅ Чистый async/await — естественно ложится на FastAPI + SQLAlchemy 2
  async, никакого `loop.run_until_complete` в воркере.
- ✅ Один зависимостный кирпич: Redis уже стоит локально (rate limiting,
  кэш sессий потенциально).
- ✅ Уже зафиксирован в CLAUDE.md §4 как часть стека — нет нового
  технологического выбора, только формализация.
- ✅ Voyage embeddings, LLM-вызовы, GEDCOM-парсинг — всё это
  IO-bound, идеально под async-воркера.
- ❌ Меньшая экосистема, чем у Celery: нет встроенного админского UI,
  beat-scheduler — отдельный пакет (`arq` cron). Для Phase 3.5
  это не нужно, но при росте может понадобиться доп.инструменты.

### Вариант B — Celery

«Стандарт» для Python-очередей.

- ✅ Зрелая экосистема, Flower UI, beat-scheduler, retry-политики,
  rate-limit на job-level — всё из коробки.
- ✅ Множество брокеров (Redis, RabbitMQ, SQS).
- ❌ Sync-first дизайн. Async-таски возможны, но через костыли
  (`asgiref.sync_to_async`, gevent-pool). Для async-проекта
  это шаг назад.
- ❌ Тяжелее по конфигурации: брокер + result backend + воркер +
  beat — четыре подвижные части, каждая со своими настройками.
- ❌ В Phase 13 (production на GCP) мы планируем Cloud Tasks как
  managed-очередь. Celery там бессмыслен — мы бы его всё равно
  выбросили.

### Вариант C — Cloud Tasks напрямую (с эмулятором локально)

Прыгнуть сразу в production-стек.

- ✅ Никакой миграции в Phase 13 — пишем код один раз.
- ❌ Локальный эмулятор Cloud Tasks существует, но беднее реального
  сервиса (нет dispatch deadlines, retries реализованы частично).
  Расхождение dev↔prod больно отлаживать.
- ❌ Cloud Tasks — push-модель (HTTP-вызов на наш же сервис).
  Для прогресса всё равно нужен Redis pub/sub или БД-polling.
  То есть Redis всё равно остаётся, и сложности не уменьшаются.
- ❌ Заставляет принять привязку к GCP до того, как мы готовы.

### Вариант D — in-process threading / asyncio.create_task

«Просто запусти в фоне».

- ✅ Ноль новой инфраструктуры.
- ❌ Перезапуск API-воркера убивает все running jobs. Никакой
  durability.
- ❌ Прогресс — только in-memory; масштабирование за пределы
  одного процесса невозможно.
- ❌ Cancellation требует ручной координации между HTTP-обработчиками
  и фоновой корутиной.
- ❌ Любой OOM на больших импортах валит весь API-сервис, не только
  одну задачу.

## Решение

Выбран **Вариант A — arq на Redis**.

**Локально:** Redis уже поднимается через `docker compose up -d`. arq
воркер — отдельный процесс, запускается командой
`uv run arq parser_service.worker.WorkerSettings`. API-сервис ставит
задачу через `arq.connections.create_pool()` и сразу возвращает
`{ "import_id": ..., "status": "pending" }`.

**В проде (Phase 13):** код задач остаётся прежним. Меняется только
адаптер enqueue: `arq` → Cloud Tasks (HTTP push на endpoint, который
делает то же, что воркер). Это изоляция позади тонкого интерфейса
`enqueue_import(import_id)` в `services/parser-service/queue.py` —
переход без переписывания бизнес-логики.

## Архитектура (Phase 3.5 baseline)

```text
┌──────────────┐  POST /imports          ┌────────────────────┐
│  Web client  │ ─────────────────────▶ │  api-gateway/api   │
└──────────────┘                         │  parser-service    │
        ▲                                │                    │
        │   GET /imports/{id}/events     │  enqueue(job_id)   │
        │   (SSE stream)                 └─────────┬──────────┘
        │                                          │
        │                                          ▼
        │                                  ┌──────────────┐
        │                                  │   Redis      │
        │                                  │  - queue:    │
        │                                  │    'imports' │
        │                                  │  - pubsub:   │
        │                                  │    'job-     │
        │                                  │     events:  │
        │                                  │     {id}'    │
        │                                  └──────┬───────┘
        │                                         │
        │       SUBSCRIBE 'job-events:{id}'       │ POP queue:imports
        └─────────────────────────────────────────┤
                                                  ▼
                                          ┌────────────────┐
                                          │  arq worker    │
                                          │  (parser-      │
                                          │   service/     │
                                          │   worker.py)   │
                                          │                │
                                          │  - parse GED   │
                                          │  - bulk INSERT │
                                          │  - PUBLISH     │
                                          │    progress    │
                                          └────────────────┘
```

**Очередь:** `imports` (Redis). Job-функции живут в
`services/parser-service/src/parser_service/worker.py` (модуль с
`WorkerSettings` и async-функциями вида `import_gedcom(ctx, import_id)`).

**Прогресс:** воркер каждые N батчей (`N=1000` персон, `N=500` событий
и т.п. — конкретные числа в worker-PR) делает
`redis.publish("job-events:{job_id}", json({"phase": "events", "done": 12340, "total": 56000}))`.

**SSE-эндпоинт:** `GET /imports/{id}/events` (Phase 3.5 api-PR)
подписывается на `job-events:{job_id}` и стримит
`text/event-stream`-фреймы клиенту. Закрывается по терминальному
событию (`status: "completed" | "failed" | "cancelled"`).

**Cancellation:** `PATCH /imports/{id}/cancel` пишет в БД
`imports.cancel_requested = true`. Воркер в `import_runner` между
батчами проверяет флаг и поднимает `ImportCancelledError`, которая
ловится job-обёрткой и публикует терминальное событие. Это soft-cancel
— текущий батч доезжает, новый не начинается. Hard-kill (SIGTERM
воркера) через arq не делаем: данные могут остаться в полу-импортированном
состоянии.

**Idempotency:** уже реализована на уровне
`(tree_id, source_sha256)` (Phase 3.4) — повторный enqueue одного
GED просто возвращает существующий `import_id`.

## Последствия

**Положительные:**

- API-эндпоинты `/imports` отвечают <200 мс независимо от размера
  GED-файла.
- Прогресс пользователю — реальный, не «поллинг каждые 5 секунд».
- Phase 7.5 (bulk-инференс по дереву) и Phase 5.x (FamilySearch
  sync) переиспользуют ту же инфраструктуру — это их «разогрев».
- Воркер можно горизонтально масштабировать (несколько процессов
  на одну очередь). API-сервис при этом не нужно дублировать.

**Отрицательные / стоимость:**

- +1 процесс в локальном стеке (arq worker). docker-compose потребует
  отдельный сервис либо запуск воркера вручную через uv. Решаем
  в worker-PR — добавляем сервис в `docker-compose.yml`.
- Redis становится критичным (а не just-cache): его падение
  останавливает импорты. Mitigation: health check + явный 503 на
  `/imports` если Redis недоступен.
- SSE-коннекты долгоживущие — на проде нужно проверить max-keepalive
  proxy/LB. Phase 13 проблема, не блокирует Phase 3.5.

**Риски:**

- Долгоживущие SSE-коннекты при бекенд-deployment могут оборваться.
  Mitigation: клиент переподписывается с `Last-Event-ID`, воркер
  пишет события в Redis stream (не pub/sub), чтобы можно было
  re-replay. Phase 3.5 ui-PR — обычный pub/sub без re-replay (KISS),
  переход на stream — TODO в worker-PR если возникнет регрессия.
- Stuck jobs (воркер упал на середине): arq имеет встроенный timeout
  `job_timeout` per-function. Импорт ставим на `job_timeout=3600`
  (час) — больше реальных GED не встречали. После истечения
  arq помечает job как failed.
- Phase 13 миграция на Cloud Tasks — отложена, но не «бесплатна».
  Компенсация: тонкий интерфейс enqueue (см. «Решение»), вся
  business-логика в pure async-функциях, которые легко вызвать
  из любого адаптера.

**Что нужно сделать в коде:**

Этот ADR — первый из 5 PR Phase 3.5. Дальнейшие PR:

1. **worker-PR:** `services/parser-service/src/parser_service/worker.py`,
   `WorkerSettings`, docker-compose-сервис `arq-worker`,
   тестовый job `ping`.
2. **runner-PR:** `import_runner` рефактор → батчевые шаги с
   проверкой `cancel_requested` между ними, публикация прогресса
   в pub/sub.
3. **api-PR:** `POST /imports` enqueue вместо синхронного вызова,
   `GET /imports/{id}/events` (SSE), `PATCH /imports/{id}/cancel`,
   ORM-поле `imports.cancel_requested`.
4. **ui-PR:** progress-bar компонент в `apps/web/`, потребитель SSE,
   кнопка cancel.

Брief — `docs/agent-briefs/phase-3.5-background-imports.md`.

## Когда пересмотреть

- > 100 импортов/мин устойчиво — Redis становится узким местом,
  пора партиционировать очереди или мигрировать на Cloud Tasks
  (Phase 13).
- Появление recurring/scheduled задач (cron-стиль) — оценить
  arq cron vs Cloud Scheduler.
- Если SSE-стрим начнёт требовать exactly-once delivery (replay,
  resume) — переход pub/sub → Redis Streams внутри той же arq.
- При переезде на Cloud Tasks (Phase 13) — этот ADR superseded
  отдельным ADR про адаптер enqueue.

## Ссылки

- ADR-0001 — tech stack (arq + Redis уже зафиксированы как локальный
  стек).
- CLAUDE.md §4 — технологический стек.
- ROADMAP §7.0 — статус Phase 3.5.
- arq docs: <https://arq-docs.helpmanual.io/>.
- GCP Cloud Tasks: <https://cloud.google.com/tasks/docs>.
