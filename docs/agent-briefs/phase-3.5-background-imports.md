# Agent brief — Phase 3.5: Background imports (arq + SSE)

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `F:\Projects\TreeGen`. Параллельная работа в
> отдельных worktree per-PR (см. «Сплит» ниже).
> Перед стартом: `CLAUDE.md` §3 и §4, `docs/adr/0026-arq-background-jobs.md`,
> `docs/architecture.md`.
>
> **Историческая заметка.** Файл `phase-3-5-multimedia-import.md` в этой
> же папке **OBSOLETE**. Multimedia (OBJE/FILE) был фактически доставлен
> в Phase 3.3 (см. ROADMAP §7.0 — `done (PR-30, PR-32, PR-33, PR-35)`),
> и название «Phase 3.5» в нём — устаревшее. Не путать. Текущая Phase 3.5
> — это **background-режим импортов через arq + SSE**, что и описано
> ниже.

---

## Зачем

Сейчас импорт большого GED (70+ МБ) блокирует HTTP-запрос: парсинг и
bulk-INSERT идут синхронно внутри handler'а `POST /imports`. Это:

- упирается в proxy/LB timeout (Cloudflare 100s, GCP HTTPS LB 30s);
- занимает API-воркер на минуты — ASGI loop не отвечает другим
  запросам;
- не даёт прогресс-бара клиенту.

CLAUDE.md §4 фиксирует `arq` на Redis как часть стека. ADR-0026 формализует
архитектурное решение. Эта Phase — реализация.

После Phase 3.5:

- `POST /imports` отвечает <200 мс (только enqueue + ORM-вставка
  shell-record).
- Клиент подписывается на `GET /imports/{id}/events` (SSE) и видит
  прогресс в реальном времени.
- Воркер обрабатывает GED в фоне; крашится один импорт — остальные
  и API живут.
- `PATCH /imports/{id}/cancel` мягко останавливает импорт между
  батчами.

---

## Что НЕ делать

- ❌ Использовать Celery / Cloud Tasks напрямую. ADR-0026 — `arq` локально,
  Cloud Tasks — отдельный ADR в Phase 13.
- ❌ Запускать background через `asyncio.create_task` внутри
  API-процесса. Никакой durability, ребут API убивает все импорты.
- ❌ Тащить retry/dead-letter инфру сейчас. Phase 3.5 — straight-line
  happy path + soft-cancel. Retry — отдельная задача (Phase 3.5.1
  если потребуется).
- ❌ Скачивать медиа-файлы или EXIF (это Phase 3.3 уже про объекты,
  а blob-скачивание — будущая Phase).
- ❌ `--no-verify`. Прямой push в main. Мерж PR с красным CI.

---

## Сплит на 5 PR (этот brief = первый, остальные следуют)

| PR | Что | Worktree |
|---|---|---|
| **#1 docs (этот)** | ADR-0026 + brief + ROADMAP в progress | `chore/phase-3.5-adr-brief-roadmap` |
| **#2 worker** | `services/parser-service/.../worker.py`, `WorkerSettings`, docker-compose-сервис `arq-worker`, smoke-job `ping`, тесты | `feat/phase-3.5-arq-worker` |
| **#3 runner** | `import_runner` рефактор → батчевые шаги, проверка `cancel_requested` между батчами, публикация прогресса в Redis pub/sub | `feat/phase-3.5-runner-progress` |
| **#4 api** | `POST /imports` enqueue вместо sync; `GET /imports/{id}/events` (SSE); `PATCH /imports/{id}/cancel`; ORM-поле `imports.cancel_requested`; Alembic-миграция | `feat/phase-3.5-api-sse-cancel` |
| **#5 ui** | `apps/web/` — progress-bar компонент, потребитель SSE, кнопка cancel, vitest-покрытие | `feat/phase-3.5-ui-progress` |

PR-зависимости: #2 → #3 → #4 → #5. PR #1 (этот) — независимый, мержится
первым, чтобы остальные ссылались на ADR-0026.

---

## Ключевые архитектурные точки (ADR-0026 коротко)

1. **Очередь:** Redis, имя `imports`. arq pool создаётся через
   `arq.connections.create_pool(RedisSettings.from_dsn(REDIS_URL))`.
2. **Job-функции:** `services/parser-service/src/parser_service/worker.py`,
   `WorkerSettings.functions = [import_gedcom]`.
3. **Прогресс:** воркер publish'ит события в Redis pub/sub
   `job-events:{job_id}`. Формат:

   ```json
   {"phase": "events", "done": 12340, "total": 56000, "ts": "2026-04-28T..."}
   ```

   Терминальные события: `{"status": "completed" | "failed" | "cancelled", ...}`.

4. **SSE-эндпоинт:** `GET /imports/{id}/events` подписан на канал и
   стримит фреймы `text/event-stream`. Закрывается на терминальном
   событии.
5. **Cancellation:** `PATCH /imports/{id}/cancel` пишет
   `imports.cancel_requested = true` в БД. Воркер между батчами:

   ```python
   if await session.scalar(
       select(Import.cancel_requested).where(Import.id == import_id)
   ):
       raise ImportCancelledError()
   ```

   Job-обёртка ловит и публикует терминал.
6. **Idempotency:** уже работает на уровне `(tree_id, source_sha256)`
   (Phase 3.4). Повторный enqueue одного GED возвращает существующий
   `import_id`.

---

## Сигналы успеха (для всей Phase 3.5)

1. ✅ `POST /imports` отвечает <200 мс на 70 МБ GED.
2. ✅ Прогресс виден в UI: phase + percent.
3. ✅ Cancel останавливает воркер между батчами; БД остаётся
   консистентной (импорт помечен как cancelled, частично-вставленные
   данные **остаются** — soft-cancel, не rollback; пользователь
   удаляет дерево руками если нужно).
4. ✅ Импорт большого GED (Ztree.ged) проходит end-to-end в фоне,
   воркер живёт.
5. ✅ Тесты: worker-PR — unit на job, runner-PR — батчинг + cancel,
   api-PR — SSE-эндпоинт через httpx ASGI, ui-PR — vitest на
   progress-bar компонент.
6. ✅ ROADMAP §7.0 row 3.5 → `done (PR-A, PR-B, PR-C, PR-D, PR-E)`
   после мерджа всей пятёрки.

---

## Если застрял

- **arq не видит job-функции** → проверь `WorkerSettings.functions`
  и что модуль воркера действительно импортируется (path в
  `uv run arq parser_service.worker.WorkerSettings`).
- **Redis pub/sub теряет события при reconnect SSE-клиента** →
  ожидаемо для pub/sub. ADR-0026 разрешает не добавлять replay в
  Phase 3.5; если регрессия болезненна — TODO в worker-PR
  «migrate to Redis Streams».
- **SSE через FastAPI/Starlette** → `StreamingResponse` с
  `media_type="text/event-stream"`, генератор `async def`, формат
  фреймов: `data: {json}\n\n`.
- **Cancel срабатывает с задержкой** → нормально, soft-cancel
  доезжает текущий батч. Если задержка > 10 секунд — уменьшить
  размер батча в runner-PR.
- **Docker-compose: arq worker не видит код** → bind-mount
  `services/parser-service` в контейнер либо собирать образ через
  тот же Dockerfile, что api-сервис, и менять только entrypoint.

Удачи.
