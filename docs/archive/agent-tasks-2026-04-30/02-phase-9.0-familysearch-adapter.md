# Agent 2 — Phase 9.0: FamilySearch adapter scaffold

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (`F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` (см. Agent 1 README).
2. `ROADMAP.md` — «Фаза 9 — Интеграции с архивами» (§13), особенно §13.1 «Юридическое исследование» и §13.2 «Phase 9.x ordering».
3. `docs/architecture.md`, `docs/adr/`.
4. Существующий код: `services/dna-service/`, `services/parser-service/` — как образец структуры FastAPI-сервиса. `packages/shared-models/security.py` — middleware (Phase 13.2).

## Задача

Создать `services/archive-service` как новый workspace member и **scaffold первого адаптера FamilySearch (read-only)**. Никакой записи в наше дерево — это Phase 9.1.

## Scope

### Новый сервис `services/archive-service/`

Структура по образцу `services/dna-service/`:

- `pyproject.toml` (член uv workspace).
- `src/archive_service/main.py` — FastAPI app, `apply_security_middleware(app, "archive-service")`, эндпоинт `GET /healthz`.
- `src/archive_service/adapters/familysearch.py` — основной адаптер.
- `src/archive_service/api/familysearch.py` — роутер.
- `src/archive_service/config.py` — Settings через `pydantic-settings`.
- `tests/`.
- `README.md`.

Зарегистрировать сервис в корневом `pyproject.toml` как workspace member (только эту строку, ничего другого там не трогать).

### Adapter `familysearch.py`

- **OAuth2 PKCE flow**: `start_authorize(state) -> redirect_url`, `exchange_code(code, code_verifier) -> tokens`, `refresh(refresh_token) -> tokens`. Используй `httpx` (async).
- **Methods**:
  - `async search_records(query: str | None, surname: str | None, given: str | None, year: int | None, year_range: int = 5) -> list[RecordHit]` — proxy к FS Records Search API.
  - `async get_person(fsid: str) -> PersonDetail` — proxy к FS Tree API.
- **Rate-limit** (FS quota — 1500 req/hour) — token-bucket в Redis по ключу `fs:rate:{client_id}:{user_id}`.
- **ETag-кэш** — Redis на 24h по ключу `fs:cache:{endpoint}:{sha256(params)}`. Возвращай 304-aware ответы.

### Эндпоинты

- `GET /archives/familysearch/oauth/start` → `{authorize_url, state, code_verifier}` (verifier хранить в session/Redis на 10 минут).
- `GET /archives/familysearch/oauth/callback?code=...&state=...` — обрабатывает code, сохраняет токены **зашифрованные на app-level** (если в `shared-models` есть утилита — переиспользуй; иначе — TODO в коде + явный комментарий, временно env-var для соли).
- `GET /archives/familysearch/search?q=...&surname=...&given=...&year=...` — read-only proxy.
- `GET /archives/familysearch/person/{fsid}` — read-only proxy.
- `GET /healthz`.

### Конфиг

env-vars: `FAMILYSEARCH_CLIENT_ID`, `FAMILYSEARCH_CLIENT_SECRET`, `FAMILYSEARCH_REDIRECT_URI`, `FAMILYSEARCH_BASE_URL=https://api.familysearch.org`. Если не заданы — endpoints `503` с понятным error.

### ADR-0055 в `docs/adr/`

Обоснование: read-only first, OAuth2 PKCE, rate-limit и кэш стратегия, юридический check (FS dev terms — указать ссылку на проверенный TOS), что отложено в Phase 9.1, app-level encryption открытый вопрос.

## Тесты (> 80% покрытие)

- `tests/test_familysearch_adapter.py` — unit с **inline JSON-мок ответами** (НЕ настоящие credentials). Покрыть: search успех, search 0 results, person 404, rate-limit hit (429 → exp backoff), OAuth refresh.
- `tests/test_endpoints.py` — FastAPI TestClient: 503 без env-vars, 200 с моком адаптера, healthz.
- `tests/test_security_headers.py` — smoke что middleware подключён (паттерн из остальных сервисов после Phase 13.2).
- Маркер `@pytest.mark.integration` на тестах, требующих живых credentials (skipped в CI).

## Запреты

- ❌ Alembic-миграции (адаптер вообще не пишет в нашу БД).
- ❌ `packages/shared-models/` менять — только читать. Если очень нужна новая утилита — оставь TODO в коде с явным комментарием, не блокирующим компиляцию.
- ❌ `apps/web/`.

## Процесс

1. `git checkout -b feat/phase-9.0-familysearch-adapter`
2. Маленькие коммиты: `feat(archive-service): scaffold service`, `feat(archive-service): familysearch adapter`, `feat(archive-service): oauth endpoints`, `docs(adr): add ADR-0055`, `test(archive-service): ...`.
3. Перед каждым коммитом: `uv run pre-commit run --all-files` + `uv run pytest services/archive-service`.
4. **Запусти `uv sync --all-extras --all-packages`** после регистрации в workspace, чтобы убедиться, что lock обновляется чисто.
5. **НЕ мержить, НЕ пушить в `main`.**

## Финальный отчёт

- Ветка, коммиты, pytest, ADR-0055, список файлов, env-vars для prod, открытые вопросы (особенно encryption-at-rest для refresh tokens).
