# ADR-0055: archive-service и FamilySearch read-only adapter (Phase 9.0)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `archive-service`, `familysearch`, `phase-9`, `oauth2`, `rate-limit`,
  `caching`, `tokens-at-rest`

## Контекст

Phase 9 (ROADMAP §13) — интеграция с внешними генеалогическими архивами.
Архитектура (§13.3) предписывает отдельный сервис `archive-service` со
своими адаптерами под каждый источник. Phase 9.0 — **scaffold** этого
сервиса плюс первый адаптер для **FamilySearch (read-only)**: пользователь
может через наш UI искать записи в FS и просматривать tree-people, но мы
**не** пишем результаты в наше дерево (запись — Phase 9.1+, через
ImportJob в parser-service, которая уже существует с Phase 5.1).

Существующие смежные компоненты:

- `packages/familysearch-client` (Phase 5.0, ADR-0011) — типизированный
  async-клиент Tree API + OAuth 2.0 PKCE flow + tenacity retry на 429/503.
- `services/parser-service/api/familysearch.py` (Phase 5.1, ADR-0027) —
  server-side OAuth flow и async-импорт pedigree через arq. Хранит
  токен в `users.fs_token_encrypted` (Fernet at-rest).

archive-service пересекается со всем этим, но **не** заменяет: его
endpoints — read-only прокси (`/archives/familysearch/*`), а у
parser-service остаются import-endpoints (`/imports/familysearch/*`).

## Рассмотренные варианты

### Вариант A — расширить parser-service вместо нового сервиса

- ✅ Один сервис → проще деплой, меньше boilerplate.
- ❌ ROADMAP §13.3 явно предписывает отдельный `archive-service`. Причина:
  parser-service уже большой (GEDCOM parsing + tree read API + FS import +
  inference orchestration), а Phase 9.x добавляет ещё 5+ адаптеров —
  смешивать ingest и proxy в одном сервисе ухудшит cohesion и blast radius
  багов.
- ❌ DNA / inference / parser имеют разные SLO; FS rate-limits — отдельный
  surface. Лучше изолировать.

### Вариант B — archive-service переиспользует familysearch-client (Tree API + OAuth) и сам делает Records Search через httpx

- ✅ ROADMAP §13.3.1 это и предлагает: «Reuse pattern из
  packages/familysearch-client/».
- ✅ OAuth/Tree-API уже отлажены; добавляем только Records Search и
  service-уровень (rate-limit + ETag cache).
- ❌ Records Search API не покрыт client'ом — нужна минимальная
  «копия» HTTP-логики в адаптере (status mapping и т.п.). Дубликация
  ограничена `_raise_for_status` и `_cached_get` (~50 строк).

### Вариант C — расширить familysearch-client до полного set'а endpoints (включая Records Search), а archive-service сделать тонким прокси

- ✅ Нет дубликации HTTP-логики.
- ❌ Records Search и Tree API имеют разные shape ответов; в client'е
  нужна вторая «лестница» моделей. Цена scaffold'а Phase 9.0 вырастает
  до изменения публичного API общего пакета.
- ❌ Кэш ETag нативно живёт в service-слое (Redis), client этого не
  видит.

## Решение

Выбран **Вариант B**.

- **Новый сервис** `services/archive-service` (uv workspace member, FastAPI,
  тот же middleware-стек ADR-0053).
- **Reuse `familysearch-client`** для PKCE OAuth и для `FamilySearchConfig`
  (sandbox/production endpoints). Errors типизированы через
  `familysearch_client.errors.*`.
- **Новый код** в `services/archive-service/src/archive_service/adapters/familysearch.py`:
  - Records Search API — raw httpx, парсинг entries → `RecordHit`.
  - Tree person — raw httpx, парсинг → `PersonDetail` (нам не нужны
      все GEDCOM-X поля, scope read-only-просмотр).
  - Token-bucket rate-limit на ключе `fs:rate:{client_id}:{user_id}`
      (атомарный Lua в Redis). Capacity = 60 (burst), refill = 1500/час
      по дефолту. При исчерпании — `AdapterRateLimitError` → HTTP 429
      с `Retry-After`.
  - ETag-кэш на 24h в Redis hash `fs:cache:{sha256(endpoint+params)}`
      (поля `etag` + `body`). Запросы посылаются с `If-None-Match`,
      на 304 возвращаем закэшированный body.

### Read-only first

Phase 9.0 **не** пишет в наше дерево. Записи появятся в Phase 9.1+:
оттуда тянем в parser-service ImportJob (тот же arq pipeline, что
для GEDCOM-импорта). Это сознательный split: даёт нам шипить proxy
быстро (3–5 дней), а ingestion-страну — отдельным PR с тестами на
маппинг/дедупликацию (которая уже частично есть в Phase 5.1).

### Tokens at rest

Refresh-токены FS — особо чувствительные данные (длительный доступ к
аккаунту пользователя). Шифруем Fernet-ом (`cryptography`), ключ из
ENV `ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY` (urlsafe-base64, 32 bytes).
**При отсутствии ключа** `/oauth/callback` возвращает 503 — мы
сознательно отказываемся хранить токен в plaintext'е, аналогично
ADR-0027.

Хранилище — `archive_service.token_storage.TokenStorage` (Redis,
ключ `fs:token:{user_id}`, TTL = `token.expires_in`).

> **Open question / TODO.** `parser_service.fs_oauth.TokenStorage` уже
> делает похожую работу для своего use-case'а. Дубликация осознанная
> (см. ниже §«Что отложено»), но стратегически нужно вынести общий
> примитив в `shared-models[security]` и заменить оба места одним.
> Это не входит в Phase 9.0 scope: изменение публичного API
> shared-models требует синхронной координации с dna-service и
> notification-service, что увеличило бы blast radius scaffold'а.

### OAuth state хранится отдельно от токена

Phase 9.0 хранит `(state → code_verifier)` в Redis с TTL 10 минут под
ключом `fs:oauth_state:{state}`. На callback'е используем `GETDEL`
(атомарный read+delete) — защищает от replay'я state'а и от
«висячих» state-записей. Этот паттерн совпадает с
`parser_service.fs_oauth.{save_state,consume_state}` из Phase 5.1.

### TOS / юридический check

FamilySearch developer terms (актуальны на 2026-04-28, см. также
ROADMAP §13.1, research note `docs/research/archive-integrations-2026.md`):

- Public API доступен после регистрации app key.
- Read-only Tree + Records разрешены для personal apps; commercial use
  требует **Compatible Solution** approval — заявку готовим в Phase 12
  (Stripe). До этого момента archive-service используется в режиме
  «free / personal exploration» с явным consent.
- Rate quota — 1500 req/hour (personal). Соблюдаем через token-bucket.
- Атрибуция: на UI рядом с record show «Source: FamilySearch» — UI-задача,
  не scope этого ADR.

## Последствия

- **Положительные:**
  - Готов scaffold для следующих адаптеров (Wikimedia, WikiTree, …) —
      они переиспользуют тот же `apply_security_middleware`,
      `make_redis_client`, `TokenStorage` (после refactor'а в shared).
  - Read-only proxy позволяет фронту делать «search-and-preview» из
      любой страницы без мигания между сервисами.
  - Rate-limit и кэш устраняют riskk бана от FS даже при агрессивных
      UI-паттернах (typeahead и т.п.).
- **Отрицательные / стоимость:**
  - Дубликат `TokenStorage` в parser-service и archive-service до
      extraction'а в shared-models.
  - Lua rate-limit script — небольшой когнитивный overhead для тех,
      кто не работал с EVAL.
  - Ещё один Cloud Run сервис → +небольшая стоимость в проде.
- **Риски:**
  - Если FS изменит response shape `entries[]` для Records Search —
      `_parse_search_response` упадёт. Парсер консервативный (skip
      записей без title), но строгая структура контракта закреплена в
      тестах с inline-fixture'ами.
  - Token-bucket в Redis: при N-instances-Cloud-Run каждый держит
      свой счётчик через Lua, но keyspace общий (Redis), так что
      конкурентность корректна.
- **Что нужно сделать в коде** (этим PR):
  - `services/archive-service/{src,tests}` — scaffold + adapter +
      router + tests.
  - `pyproject.toml` (root) — register workspace member + sources +
      mypy_path.
  - `docs/adr/0055-archive-service-and-familysearch-adapter.md` — этот файл.

## Что отложено (Phase 9.1+)

- **Запись в наше дерево** — мы добавляем `RecordHit → ImportJob` mapping,
  переиспользуя existing FS import pipeline parser-service'а.
- **Extraction `TokenStorage` в `shared_models`** — параллельно с
  Phase 9.1 или в его рамках.
- **State binding к user_id** — сейчас CSRF state не привязан к
  Clerk-сессии, что в теории позволяет cross-user replay (если
  атакующий получил state через MITM или logs). Phase 9.x должен
  добавить `(state → user_id)` co-storage с верификацией на callback'е.
- **Bulk request endpoints** (FS Place Authority, Source descriptions) —
  не входят в Phase 9.0 read-only scope.

## Когда пересмотреть

- Если FS квота меняется — обновить дефолты `fs_rate_limit_*`.
- Если получим **Compatible Solution** approval — пересмотреть
  атрибуцию-строки на UI и SLA повторных запросов.
- Если расширим до 3+ адаптеров — пересмотреть, нужно ли вынести
  rate-limit и cache layer в общий middleware (сейчас они в
  `FamilySearchAdapter`, что для одного адаптера ОК).

## Ссылки

- Связанные ADR: ADR-0011 (FS client), ADR-0017 (FS import mapping),
  ADR-0027 (FS OAuth token storage), ADR-0033 (Clerk JWT),
  ADR-0053 (security middleware).
- ROADMAP §13 — Phase 9 интеграции.
- `docs/research/archive-integrations-2026.md` — research note по
  всем источникам (privacy, partnership cost, rate limits).
- RFC 7636 — PKCE: <https://datatracker.ietf.org/doc/html/rfc7636>
- FamilySearch dev portal: <https://developers.familysearch.org>
