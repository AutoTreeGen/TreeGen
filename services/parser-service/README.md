# parser-service

FastAPI-сервис для импорта GEDCOM-файлов и чтения дерева через REST.

Phase 3 MVP — endpoints в первой итерации:

- `POST /imports` — multipart upload `.ged`, парсит через `gedcom-parser`,
  пишет через `shared-models` ORM, возвращает `import_job_id` + stats.
- `GET /imports/{job_id}` — статус job'а (queued/processing/succeeded/failed).
- `GET /trees/{tree_id}/persons?limit&offset` — список персон в дереве.
- `GET /persons/{person_id}` — детали персоны: имена, события, семьи.
- `GET /healthz` — liveness probe.

## Запуск локально

```bash
docker compose up -d                          # postgres + redis + minio
uv run alembic upgrade head                   # схема БД
uv run uvicorn parser_service.main:app --reload --port 8000
# → http://localhost:8000/docs
```

## Тесты

```bash
uv run pytest services/parser-service/ -v
```

Юнит-тесты используют in-memory SQLite-эквивалент через testcontainers
(не требует запущенного docker compose).

## Конфигурация

Через переменные окружения (см. `config.py`):

| Var | Default |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen` |
| `PARSER_SERVICE_DEBUG` | `false` |
| `PARSER_SERVICE_OWNER_EMAIL` | `owner@autotreegen.local` |

## Архитектура

`api/` — FastAPI routers (тонкие). `services/` — бизнес-логика
(import_runner вызывает gedcom-parser + ORM). `database.py` — async engine
и session factory.

В этой итерации импорт **синхронный** (HTTP request ждёт до завершения),
audit отключён в bulk-режиме (как в `scripts/import_personal_ged.py`).
Background-режим через `arq` — Phase 3.5 (см. ROADMAP §7).
