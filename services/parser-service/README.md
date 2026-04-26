# parser-service

FastAPI-сервис для импорта GEDCOM-файлов и чтения дерева через REST.

Phase 3 MVP — endpoints в первой итерации:

- `POST /imports` — multipart upload `.ged`, парсит через `gedcom-parser`,
  пишет через `shared-models` ORM, возвращает `import_job_id` + stats
  (persons / names / families / family_children / events / event_participants).
- `GET /imports/{job_id}` — статус job'а (queued/running/succeeded/failed).
- `GET /trees/{tree_id}/persons?limit&offset` — список персон в дереве.
- `GET /persons/{person_id}` — детали персоны: имена и события (BIRT/DEAT/...
  через events + event_participants).
- `GET /healthz` — liveness probe.

Phase 3.1 (events): `POST /imports` теперь раскладывает события у `INDI` и
`FAM` в таблицы `events` + `event_participants`. Place-импорт и
multi-principal participants — в Phase 3.2.

## Запуск локально

```bash
docker compose up -d                          # postgres + redis + minio
uv run alembic upgrade head                   # схема БД
uv run uvicorn parser_service.main:app --reload --port 8000
# → http://localhost:8000/docs
```

## Тесты

```bash
# Требуется запущенный Docker Desktop (testcontainers поднимает свой Postgres).
uv run pytest services/parser-service -m "not gedcom_real"

# На Windows + Python 3.13 testcontainers иногда не успевает прочитать port-mapping
# своего Reaper-контейнера; короткий обход — отключить ryuk:
TESTCONTAINERS_RYUK_DISABLED=true uv run pytest services/parser-service -m "not gedcom_real"
```

Интеграционные тесты (`-m integration`, `-m db`) поднимают
testcontainers-postgres с `pgvector`, накатывают alembic head и гоняют
FastAPI app через `httpx.AsyncClient`. Их `app_client` фикстура
переопределяет `DATABASE_URL` testcontainer'ом на время сессии.

## curl-примеры (после `uvicorn ... --port 8000`)

```bash
# Импорт GEDCOM (multipart). Ответ — ImportJobResponse со stats и tree_id.
curl -X POST http://localhost:8000/imports \
  -F file=@./Ztree.ged

# Статус job'а
curl http://localhost:8000/imports/<job-uuid>

# Список персон в дереве
curl 'http://localhost:8000/trees/<tree-uuid>/persons?limit=50&offset=0'

# Детали персоны (включает события из event_participants join)
curl http://localhost:8000/persons/<person-uuid>

# Liveness
curl http://localhost:8000/healthz
```

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
