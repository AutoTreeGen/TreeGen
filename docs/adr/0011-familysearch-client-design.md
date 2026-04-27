# ADR-0011: FamilySearch client design

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `integration`, `oauth`, `phase-5`

## Контекст

ADR-0009 утвердил FamilySearch как **Tier 1** интеграцию для Phase 5
(вариант B — гибрид официальных API + GEDmatch). FamilySearch — единственная
крупная платформа, у которой одновременно:

- публичный API (`https://api.familysearch.org`) с регистрацией для
  разработчиков на [developers.familysearch.org](https://developers.familysearch.org/);
- полноценный sandbox (`https://api-integ.familysearch.org`) для разработки
  без production data;
- бесплатный тариф для non-profit/research use;
- OAuth 2.0 (Authorization Code + PKCE для desktop/web app);
- модель данных GEDCOM-X (JSON), имеющая зафиксированный round-trip с
  GEDCOM 5.5.5 (наш canonical формат — ADR-0007).

Phase 5.0 — это **скелет клиента**, не интеграция. Цель: переиспользуемый
пакет `packages/familysearch-client/`, который потом подключается к
`parser-service` в Phase 5.1, к hypothesis engine в Phase 6+, и к web-у в
Phase 4.x.

Силы давления на решение:

1. **CLAUDE.md §6:** strict mypy, ruff, > 80% покрытие тестами. Любая внешняя
   библиотека должна играть с этим стеком (Pydantic v2, httpx, async-first).
2. **CLAUDE.md §3.5 (Privacy by design):** access/refresh tokens — секреты,
   нельзя логировать в plaintext, в проде уезжают в GCP Secret Manager.
3. **CLAUDE.md §5:** запрет скрейпинга — клиент работает только через
   официальный API, никаких HTML-парсеров.
4. **ADR-0008 (CI/pre-commit parity):** в CI не должно быть сетевых тестов —
   real-API проверки помечаются маркером и пропускаются как `gedcom_real`.
5. **Phase timeline:** Phase 5.0 — это 4 PR, не 4 месяца. Решение не должно
   тянуть тяжёлую инфраструктуру.

## Рассмотренные варианты

### Вариант A — Тонкий httpx-клиент + ручной маппинг GEDCOM-X в Pydantic

Свой пакет `familysearch-client` поверх `httpx` (sync + async),
GEDCOM-X JSON парсится в Pydantic v2 модели вручную, OAuth PKCE flow
реализуется самим пакетом, retry на 429/503 — через `tenacity`.

- ✅ Полный контроль над типами: Pydantic-модели соответствуют strict mypy и
  стилю кода `shared-models`.
- ✅ Минимум транзитивных зависимостей — `httpx` уже в проекте
  (`services/parser-service`), `tenacity` точечно для retry.
- ✅ Покрытие моками через `pytest-httpx` — стандартный путь, согласуется с
  CI-парностью (ADR-0008): сетевые тесты помечаются и скипаются.
- ✅ Не блокируется на отсутствии официального Python SDK от FamilySearch.
- ❌ Поддерживать маппинг GEDCOM-X → Pydantic вручную; при появлении новых
  ресурсов FamilySearch нужно дописывать модели.
- ❌ Вся инфраструктура (rate-limit, retry, refresh) — наша
  ответственность; нет «батареек из коробки».

### Вариант B — Готовый community Python SDK

На PyPI есть `familysearch` (последний релиз 2018), `python-fsclient`
(заброшен), `gedcomx` (только модели, без HTTP). Все — pre-Pydantic,
sync-only, без типов.

- ✅ Если бы был maintained SDK — ноль кастомного кода.
- ❌ **Нет ни одного maintained SDK** (April 2026). Самый новый коммит в
  `peterbraden/python-familysearch` — 2018, mypy/Pydantic/async из коробки нет.
- ❌ Pinning к заброшенному пакету — supply chain risk + блокер на security
  patches.
- ❌ Все стилевые/типовые конвенции проекта (CLAUDE.md §6) ломаются: придётся
  оборачивать SDK в адаптер, что эквивалентно по объёму варианту A, но с
  дополнительной зависимостью.

### Вариант C — OpenAPI / Swagger-codegen из официальной спецификации

FamilySearch генерирует клиента из OpenAPI-схемы.

- ✅ Автоматическая генерация моделей и методов.
- ❌ **FamilySearch не публикует OpenAPI-спецификацию.** Документация —
  HTML-страницы с примерами JSON по каждому endpoint. Codegen неприменим
  без предварительной обратной разработки спецификации.
- ❌ Codegen-ed клиенты исторически плохо ложатся на Pydantic v2 + async
  без post-processing — выигрыш съедается ручной правкой.

## Решение

Выбран **Вариант A** — собственный httpx-клиент с Pydantic-моделями.

Обоснование (4 предложения):

1. **B нереализуем по факту** — нет maintained SDK, остальные — заброшены и
   ломают стандарты проекта.
2. **C недоступен** — FamilySearch не предоставляет OpenAPI-схему, codegen
   потребует обратной разработки спецификации, что затратнее ручного
   маппинга.
3. **A соответствует стеку** (httpx уже есть, Pydantic v2 — основной DTO-слой,
   tenacity — лёгкая зависимость) и масштабируется на остальные интеграции
   Phase 5 (Geni, MyHeritage, WikiTree) как референс-паттерн.
4. **Cost ручного маппинга умеренный**: для Phase 5.0 нужны ~5 ресурсов
   (Person, Name, Fact, Gender, Relationship). Расширение — incremental.

### Архитектурные выборы внутри варианта A

**Auth — OAuth 2.0 Authorization Code + PKCE:**

- PKCE (RFC 7636) — обязателен для desktop/web app flow без client secret.
  FamilySearch поддерживает PKCE на endpoint
  `https://identbeta.familysearch.org/cis-web/oauth2/v3/` (sandbox) и
  `https://ident.familysearch.org/cis-web/oauth2/v3/` (production).
- `code_verifier`: 43–128 символов из URL-safe alphabet (RFC 7636 §4.1).
- `code_challenge`: `BASE64URL-ENCODE(SHA256(code_verifier))`,
  `code_challenge_method=S256`.
- `state` — CSRF protection, генерируется отдельно от verifier.
- Access token: 24h TTL или 60 min inactivity. Refresh token (native app):
  90 days TTL. `refresh_token` rotation supported.
- Storage: out of scope этого ADR — пакет принимает токены извне, persistence
  делает caller (в проде — GCP Secret Manager, см. ADR-0009).

**Errors — типизированная иерархия:**

```text
FamilySearchError                    (базовый)
├── AuthError                        (401, 403)
├── NotFoundError                    (404)
├── RateLimitError                   (429, несёт retry_after)
├── ServerError                      (5xx, retryable)
└── ClientError                      (4xx прочие, non-retryable)
```

Все exception'ы — Python-классы, не HTTPStatusError, чтобы caller
не зависел от httpx.

**Retry — tenacity на 429/503:**

- exponential backoff с jitter, начальный delay 1s, max 30s.
- 3 попытки по умолчанию, конфигурируется через `RetryPolicy`.
- На 429 — уважаем `Retry-After` header, если присутствует.
- На 4xx-кроме-429 — никаких retry (это либо bug в нашем запросе, либо
  валидный отказ). Retry на network errors (ConnectError, ReadTimeout) — да.

**Testing — двухуровневое:**

- **Unit/CI:** `pytest-httpx` мокает все HTTP-запросы. Sample JSON для
  responses — фикстуры из `tests/fixtures/`, основанные на shape из
  публичной документации FamilySearch. Запускается всегда, в т.ч. в CI.
- **Real-API integration:** маркер `@pytest.mark.familysearch_real` (по
  аналогии с `gedcom_real`). Skipped по умолчанию; запускается локально
  владельцем, когда у него есть sandbox app key и `FAMILYSEARCH_SANDBOX_KEY`
  в env. CI **не** запускает эти тесты — sandbox quota ограничена и
  поведение не детерминировано без отдельного OAuth flow.
- Маркер регистрируется в корневом `pyproject.toml` под `[tool.pytest.ini_options]
  markers`.

**Async-first:**

- Основной API клиента — `async def get_person(...)` через `httpx.AsyncClient`.
  Sync-обёртка добавляется по необходимости (Phase 5.1+), не сейчас —
  parser-service уже async.

**Sandbox vs production:**

- Конфигурация через `FamilySearchConfig` (Pydantic Settings или dataclass):
  `base_url`, `authorize_url`, `token_url`. Дефолты — sandbox; production —
  явное переопределение, чтобы случайно не уйти в prod в dev-окружении.

**Provenance:**

- Каждый успешный response несёт минимум: `(endpoint, requested_at_utc,
  response_etag)` для последующего provenance tagging
  (CLAUDE.md §3.3 — provenance everywhere). Сейчас — поле в return type;
  использование появляется в Phase 5.1 при подключении к parser-service.

## Последствия

**Положительные:**

- Skeleton-пакет переиспользуется как референс для остальных Tier-1
  интеграций Phase 5 (Geni, MyHeritage, WikiTree). Структура `auth.py /
  client.py / models.py / errors.py / config.py` тиражируется.
- Pydantic-модели FamilySearch напрямую конвертятся в `shared-models` ORM
  через явный маппер в Phase 5.1 — без посредника в виде codegen-ed DTO.
- CI остаётся изолированным: ни один тест не делает реальных сетевых
  вызовов, ADR-0008 (CI parity) сохраняется.
- Token storage остаётся ответственностью каллера → пакет не несёт в себе
  привязки к GCP / dev-storage; легко переиспользуется в любом контексте.

**Отрицательные / стоимость:**

- Ручной маппинг GEDCOM-X → Pydantic для каждого нового ресурса. Phase 5.0
  покрывает только Person + связанные базовые типы (Name, Fact, Gender);
  расширение в Phase 5.1+ — несколько часов на ресурс.
- Не получаем «батарейки» community-SDK, но этих батареек и нет в природе.
- При изменении формы GEDCOM-X JSON со стороны FamilySearch (редко, но
  возможно) — нужно ловить через `model_validate` ошибки и обновлять модели.

**Риски:**

- **Sandbox app registration ограничен.** FamilySearch требует ручной
  аппрув для sandbox key. *Mitigation:* runbook для владельца
  (`docs/runbooks/familysearch-sandbox-setup.md`, опционально), TODO в
  README пакета.
- **Token refresh edge cases.** Истёкший refresh token vs revoked vs
  network error — разные пути. *Mitigation:* типизированные exceptions
  - покрытие unit-тестами всех ветвей. Реальное поведение проверяется в
  `familysearch_real`-тестах локально.
- **Rate limits не опубликованы FamilySearch.** *Mitigation:* conservative
  defaults в `RetryPolicy` + `Retry-After` уважается; если CI начнёт
  ловить 429, контактируем `devsupport@familysearch.org` (см.
  `docs/research/genealogy-apis.md`).
- **PKCE state/verifier утекают.** *Mitigation:* `state` и `code_verifier`
  никогда не логируются (ни в `__repr__`, ни через `logger.debug`); хранение
  верифаера — на стороне caller, не в самом auth-объекте после
  `complete_flow`.

**Что нужно сделать в коде (Phase 5.0 PRs):**

1. `packages/familysearch-client/` — pyproject.toml, src-layout, py.typed,
   README quickstart.
2. Регистрация в `[tool.uv.workspace]` + `[tool.uv.sources]` корневого
   `pyproject.toml`. `uv lock`.
3. `auth.py` — PKCE flow (start/complete/refresh), CSRF state, типизированные
   исключения для invalid_grant / unauthorized_client.
4. `client.py` — async `FamilySearchClient.get_person(...)`, retry middleware
   через tenacity, маппинг HTTP status → exception.
5. `models.py` — `FsPerson`, `FsName`, `FsFact`, `FsGender`, `FsRelationship`
   (минимум для Phase 5.0).
6. `errors.py` — иерархия выше.
7. Маркер `familysearch_real` в `[tool.pytest.ini_options].markers` корневого
   pyproject.toml.
8. Mock-тесты на pytest-httpx для всего: PKCE handshake, get_person 200/404/429,
   refresh flow.

Что **отложить** в этом ADR (явно out of scope Phase 5.0):

- Write endpoints (POST/PUT/DELETE) — Phase 5.2.
- Memory/photos upload — Phase 5.3.
- DNA Match resources — partner-only access (см. ADR-0009), не входит в
  hybrid B стратегию вообще.
- Sync orchestration через arq — Phase 5.1.
- Подключение к `parser-service` (`from familysearch_client import ...`)
  — Phase 5.1 (отдельный ADR при необходимости).

## Когда пересмотреть

- **FamilySearch публикует maintained Python SDK.** → Оценить миграцию;
  если он Pydantic-based и async — переиспользовать.
- **FamilySearch публикует OpenAPI-спецификацию.** → Рассмотреть codegen
  для маппинга моделей (паттерн `client.py` сохраняем).
- **GEDCOM-X JSON shape меняется breaking-way.** → Bump major version
  пакета `familysearch-client`, миграционная нота в ADR.
- **Появляется второй интеграционный пакет** (Geni, MyHeritage). →
  Проверить, не пора ли вынести общий OAuth + retry + http-middleware в
  `packages/integrations-oauth/` (см. ADR-0009 «Что нужно построить» п.1).
  Сейчас этого делать **не нужно** — preliminary abstraction до второй
  реализации = guesswork.
- **Rate limits FamilySearch оказываются tight** (< 100 req/min). →
  Пересмотреть RetryPolicy и добавить client-side throttling (token bucket
  в Redis), как описано в ADR-0009 §«Что нужно построить» п.4.

## Ссылки

- Связанные ADR:
  - [ADR-0007](./0007-gedcom-555-as-canonical.md) — GEDCOM 5.5.5 как
    canonical; FamilySearch GEDCOM-X маппится в наш canonical через
    `gedcom-parser` round-trip конвертер.
  - [ADR-0008](./0008-ci-precommit-parity.md) — CI parity; маркер
    `familysearch_real` — стандартный паттерн для skip-в-CI тестов.
  - [ADR-0009](./0009-genealogy-integration-strategy.md) — Phase 5
    integration strategy; FamilySearch — Tier 1 в hybrid B.
  - [ADR-0001](./0001-tech-stack.md) — стек (httpx, Pydantic v2, async).
- Research: [`docs/research/genealogy-apis.md`](../research/genealogy-apis.md)
  §FamilySearch — landscape April 2026.
- External:
  - [FamilySearch Developers](https://developers.familysearch.org/)
  - [GEDCOM-X Specification](https://developers.familysearch.org/main/docs/gedcom-x)
  - [RFC 7636 — OAuth 2.0 PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
  - [RFC 6749 — OAuth 2.0 Authorization Code](https://datatracker.ietf.org/doc/html/rfc6749#section-4.1)
  - [tenacity docs](https://tenacity.readthedocs.io/)
  - [pytest-httpx docs](https://github.com/Colin-b/pytest_httpx)
