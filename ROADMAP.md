# AutoTreeGen / SmarTreeDNA — Master Plan для Claude Code

Пошаговый план реализации платформы. Код и идентификаторы — на английском, комментарии и документация — на русском. Этот документ — рабочая дорожная карта. Каждая фаза = серия задач для Claude Code.

---

## 0. Краткое резюме проекта

**Цель:** построить AI-платформу для научной генеалогии, объединяющую GEDCOM, ДНК-данные и архивы, с движком гипотез, оценкой достоверности и provenance.

**Принципы:**

- **Evidence-based:** каждое утверждение в дереве имеет источник, степень уверенности и историю изменений.
- **Hypothesis-first:** система хранит не только факты, но и гипотезы со всеми «за» и «против».
- **Provenance everywhere:** для каждого узла известно, откуда он пришёл (GEDCOM file X, DNA match Y, архив Z).
- **Versioning everywhere:** ничего не удаляется молча, всё версионируется.
- **Domain-specific:** еврейская генеалогия, Восточная Европа, транслитерация — first-class citizens.
- **MVP-driven:** сначала рабочий парсер на личном GED-файле, потом всё остальное.

---

## 1. Технологический стек (зафиксировать сразу)

| Слой | Технология | Обоснование |
|---|---|---|
| Backend services | Python 3.12+ (FastAPI, Pydantic v2) | Богатая экосистема для генеалогии/ML/NLP, ваша скорость разработки |
| Frontend | Next.js 15 + TypeScript + React 19 | SSR для SEO, экосистема, типобезопасность |
| Стилизация | Tailwind CSS 4 + shadcn/ui | Быстрая, профессиональная, не «AI-generic» |
| Визуализация древа | D3.js + react-d3-tree + кастомный layout | Контроль над большими деревьями (10k+ персон) |
| База данных | PostgreSQL 16 (AlloyDB-compatible) | Расчёт на GCP AlloyDB в проде, локально — обычный Postgres |
| Векторный поиск | pgvector в той же БД | Не плодим инфраструктуру, эмбеддинги для имён/мест/документов |
| ORM / миграции | SQLAlchemy 2 + Alembic | Стандарт |
| Очереди | Cloud Tasks / локально arq на Redis | Async импорты, тяжёлые расчёты |
| Object storage | Google Cloud Storage / локально MinIO | GEDCOM-файлы, DNA CSV, сканы документов |
| Аутентификация | Clerk или Auth0 | Не писать своё, OAuth готов |
| LLM | Anthropic Claude API (Sonnet 4.5 / Opus 4.7) | Reasoning, длинный контекст |
| Embeddings | Voyage AI (`voyage-3-large`) | Качество > OpenAI для нашего домена |
| OCR | Google Cloud Vision API | Сканы кириллицы/иврита/польского |
| Перевод | Google Cloud Translation API + промпты в Claude | Архивы на 5–10 языках |
| Платежи | Stripe (карты), PayPal, Coinbase Commerce (crypto) | Стандарт |
| Контейнеризация | Docker + docker-compose локально | Воспроизводимость |
| Деплой | GCP: Cloud Run для сервисов, GKE для тяжёлых workloads, AlloyDB для БД | Managed, но контролируемо |
| Secrets | Google Secret Manager + локально `.env` (в `.gitignore`) | |
| Шифрование | Cloud KMS / CMEK | Особенно для DNA-данных |
| Безопасность периметра | Cloud Armor + private VPC + IAM least privilege | Соответствует вашим заметкам |
| Мониторинг | Cloud Logging + Sentry | Ошибки + аудит |
| CI/CD | GitHub Actions | Бесплатно, гибко |
| Линтинг/форматинг | ruff + black (Python), biome (TS) | Быстро |
| Тесты | pytest (Python), Vitest + Playwright (TS) | Стандарт |

> **Важно:** не тащить всё сразу. На фазе 1–3 нужны только Python, Postgres локально, Docker. GCP — с фазы 13.

---

## 2. Структура монорепозитория

```text
autotreegen/
├── README.md
├── ROADMAP.md                          ← этот документ
├── CLAUDE.md                           ← инструкции для Claude Code
├── docker-compose.yml
├── .github/workflows/
├── packages/
│   ├── gedcom-parser/                  ← фаза 1, Python пакет
│   │   ├── pyproject.toml
│   │   ├── src/gedcom_parser/
│   │   ├── tests/
│   │   └── samples/                    ← тестовые GED-файлы (ваш + публичные)
│   ├── dna-analysis/                   ← фаза 6
│   ├── entity-resolution/              ← фаза 7
│   ├── inference-engine/               ← фаза 8
│   └── shared-models/                  ← Pydantic-модели, общие для всех сервисов
├── services/
│   ├── api-gateway/                    ← FastAPI, единая точка входа
│   ├── parser-service/                 ← фаза 3
│   ├── dna-service/
│   ├── archive-service/
│   ├── inference-service/
│   └── notification-service/
├── apps/
│   └── web/                            ← Next.js, фаза 4
├── infrastructure/
│   ├── terraform/                      ← GCP, фаза 13
│   ├── k8s/
│   └── alembic/                        ← миграции БД
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── gedcom-extensions.md            ← наши расширения GEDCOM
│   └── adr/                            ← Architecture Decision Records
└── scripts/
    ├── seed_db.py
    └── import_personal_ged.py          ← для тестов на вашем GED
```

---

## 3. Подготовка окружения (День 1)

**Задачи для Claude Code (по порядку):**

1. Инициализировать монорепозиторий: `git init`, `.gitignore` (Python + Node + IDE), README.
2. Настроить `pyproject.toml` с **uv** как менеджером зависимостей (быстрее poetry/pip).
3. Настроить `pnpm` workspaces для frontend (быстрее npm/yarn).
4. Создать `docker-compose.yml` с сервисами: `postgres:16` (с `pgvector`), `redis:7`, `minio`.
5. Настроить `pre-commit` hooks: ruff, black, biome, секреты-чек.
6. Создать `CLAUDE.md` с правилами: язык кода/комментариев, стиль, запрет коммитов в `main`, conventional commits.
7. Настроить GitHub Actions: lint + test на PR.

**Готово, когда:** `docker-compose up` поднимает БД с pgvector, локально работают Python и Node окружения.

---

## 4. Анализ требований к локальному компьютеру

Для комфортной разработки этого проекта на вашей машине нужно:

| Ресурс | Минимум | Рекомендуется | Почему |
|---|---|---|---|
| RAM | 16 GB | 32 GB | Postgres + Redis + парсинг + LLM-вызовы + браузер + IDE одновременно |
| CPU | 4 ядра | 8+ ядер | Параллельная обработка GEDCOM, DNA-кластеризация |
| Диск | 100 GB SSD свободно | 500 GB NVMe | GED-файлы, DNA CSV (большие), Docker-образы, индексы |
| GPU | не обязателен | NVIDIA с 8GB+ VRAM | Локальные эмбеддинги/OCR; иначе всё через API |
| Сеть | стабильная | оптоволокно | API-вызовы Claude/Voyage, Cloud Storage |
| ОС | macOS / Linux / WSL2 | macOS Apple Silicon или Linux | Docker нативно, меньше проблем |

**Софт, который нужно установить:** Docker Desktop, Git, Python 3.12 (через `uv`), Node.js 22+ (через `fnm` или `nvm`), VS Code или Cursor, Claude Code CLI, PostgreSQL client (`psql`, или `tableplus`/`dbeaver`).

> Если хотите — отдельной фазой могу составить точный список команд для установки под вашу ОС.

---

## 5. Фаза 1 — GEDCOM Parser (приоритет №1, MVP)

**Цель:** Robust парсер GEDCOM 5.5.5 (с обратной совместимостью с 5.5.1 и проприетарными расширениями Ancestry/MyHeritage/Geni), который читает ваш личный GED, нормализует, валидирует, выдаёт ошибки и пишет в БД.

### Почему важно сделать это первым

- Это ваш единственный универсальный формат входа/выхода.
- Реальные GEDCOM-файлы очень грязные: разные кодировки (UTF-8, ANSEL, CP1251), отсутствующие даты, проприетарные теги, циклы, дубликаты, неконсистентные ссылки.
- Любая последующая логика (DNA-анализ, поиск гипотез, AI) — мусор без чистых данных.

### 5.1 Подзадачи

1. Lexer/tokenizer для GEDCOM (line-based: `LEVEL TAG VALUE`, поддержка CONT/CONC).
2. Поддержка кодировок: автоопределение UTF-8 / ANSEL / CP1251 / ASCII.
3. Парсер в AST: дерево записей INDI, FAM, SOUR, REPO, OBJE, NOTE, SUBM, HEAD, TRLR.
4. Pydantic-модели для всех записей: `Person`, `Family`, `Event`, `Place`, `Name`, `Source`, `Citation`, `Note`, `MultimediaObject`.
5. Нормализация дат: GEDCOM date phrases (`ABT 1850`, `BET 1840 AND 1845`, `JUL 1812`, юлианский/григорианский, иврит-даты, FROM/TO). Сохранять оригинал + parsed range + uncertainty.
6. Нормализация имён: разделение на given/surname/prefix/suffix, поддержка `/Surname/`-нотации, патронимы, девичьи фамилии, иврит/идиш-имена.
7. Нормализация мест: иерархия (село → волость → уезд → губерния → империя), исторические vs современные названия (Wilno/Vilna/Vilnius/Вильно), гео-кодинг опционально.
8. Транслитерация: YIVO для идиша, ISO 9 / GOST для кириллицы, ALA-LC для иврита. Сохранять все варианты.
9. Резолвер кросс-ссылок: `@I123@`, `@F45@` → реальные объекты, обнаружение битых ссылок.
10. Валидатор: соответствие спецификации 5.5.5, отчёт об отклонениях с указанием строки.
11. Round-trip writer: обратно в GEDCOM 5.5.5 без потерь (или с явным логом потерь).
12. CLI-инструмент `gedcom-tool`:
    - `parse <file>` → JSON
    - `validate <file>` → отчёт
    - `stats <file>` → персон, семей, событий, источников, охват дат, гео-разнообразие
    - `diff <file1> <file2>` → что добавлено/изменено/удалено
    - `merge <file1> <file2> --strategy <auto|manual>` → объединение
13. Хранение в БД: маппинг Pydantic-моделей → SQLAlchemy → Postgres.
14. Тесты: на вашем личном GED + на корпусе публичных GED-файлов разного происхождения (Ancestry export, MyHeritage export, Geni export, FamilySearch export, Gramps, RootsMagic).

### 5.2 Что должно получиться (acceptance)

- `gedcom-tool parse my_tree.ged` за < 30 сек на дереве в 10 000 персон выдаёт чистый JSON.
- Полный отчёт о структуре файла, всех ошибках и предупреждениях.
- Round-trip: ваш GED → БД → GED байт-в-байт совпадает (или явный список интерпретированных полей).
- Покрытие тестами > 85%.

### 5.3 Промпт для Claude Code (пример)

```text
Реализуй пакет packages/gedcom-parser. Используй спецификацию GEDCOM 5.5.5
(https://gedcom.io). Начни с lexer для line-based формата с поддержкой
CONT/CONC и автоопределением кодировок UTF-8/ANSEL/CP1251. Все публичные API
типизируй через Pydantic v2. Все комментарии и docstrings — на русском,
имена идентификаторов и сообщения об ошибках в коде — на английском. Тесты
на pytest, фикстуры — минимальный валидный GED и corner-cases (битые
ссылки, проприетарные теги Ancestry, иврит-даты).
```

---

## 6. Фаза 2 — Модель данных и БД

**Цель:** схема, на которой можно строить всё остальное, с поддержкой гипотез, provenance, версионирования.

### 6.1 Основные таблицы (минимум)

**Сущности:** `persons`, `names` (multi), `families`, `events`, `places`, `place_aliases`, `sources`, `source_artifacts`, `citations`, `notes`, `multimedia_objects`.

**ДНК:** `dna_kits`, `dna_matches`, `shared_matches`, `clusters`, `cluster_members`, `chromosome_segments`, `person_kit_links`.

**Гипотезы и доказательства:** `hypotheses`, `hypothesis_evidence`, `evidence_artifacts`, `confidence_scores`.

**Управление:** `users`, `trees`, `tree_collaborators`, `import_jobs`, `review_tasks`, `audit_log`, `versions`.

**Векторы:** `person_embeddings`, `place_embeddings`, `document_embeddings` (через pgvector).

### 6.2 Обязательные поля у «живых» сущностей

- `id` (UUID)
- `tree_id`
- `confidence_score` (0–1, derived от evidence)
- `status` (`confirmed | probable | hypothesis | rejected | merged`)
- `provenance` (jsonb: source files, import_job_id, manual_edits)
- `version_id` (последняя версия)
- `created_at`, `updated_at`, `deleted_at` (soft delete)

### 6.3 Версионирование

Один из подходов на выбор (зафиксировать в ADR):

- **Bi-temporal таблицы** (`valid_from`, `valid_to`, `recorded_from`, `recorded_to`) — точно, сложно.
- **Event sourcing** для изменений с проекциями — мощно, требует дисциплины.
- **Audit-log + snapshot-восстановление** — прагматично, рекомендую на старте.

### 6.4 Подзадачи

1. ER-диаграмма (Mermaid) в `docs/data-model.md`.
2. SQLAlchemy-модели.
3. Alembic-миграции.
4. Seed-скрипт с тестовыми данными.
5. ADR по выбору стратегии версионирования.
6. Bench: вставка 100 000 персон < 60 сек, запрос предков 10 поколений < 200 мс.

---

## 7. Фаза 3 — Backend API (parser-service)

**Цель:** обернуть парсер в HTTP-сервис, чтобы веб мог им пользоваться.

### 7.0 Status

| Подфаза | Что внутри | Статус |
|---|---|---|
| **3-A** | FastAPI-скелет, `POST /imports`, `GET /imports/{id}`, `GET /trees/{id}/persons`, `GET /persons/{id}`, `GET /healthz` | done (PR-3) |
| **3.1** | Импорт `events` + `event_participants` для `INDI`/`FAM` | done (PR-13) |
| **3.2** | Импорт `places` (без alias-канонизации), multi-principal participants (husband/wife на MARR), `place` в `GET /persons/{id}` | done (PR-19, PR-20, PR-21) |
| **3.3** | Импорт `sources` + `citations` с PAGE/QUAY, OBJE → `multimedia_objects` + полиморфный `entity_multimedia`; `events[].citations` и `media[]` в `GET /persons/{id}` | done (PR-30, PR-32, PR-33, PR-35) |
| **3.4** | Entity resolution: `packages/entity-resolution/` (Soundex + Daitch-Mokotoff + Levenshtein + token-set + DM-blocking), `dedup_finder` (READ-ONLY), `GET /trees/{id}/duplicate-suggestions`. См. ADR-0015. Идемпотентность импорта по `(tree_id, source_sha256)` перенесена в Phase 3.5. | done (PR-39, PR-44, PR-48, PR-50) |
| **3.5** | Background-режим через `arq` + SSE для прогресса (async imports queue + Server-Sent Events live progress) | done (PR #102 ADR + #105 runner + #101 worker + #103 ui + #104 api) |

### 7.1 Эндпоинты

- `POST /trees` — создать новое дерево.
- `POST /trees/{id}/imports` — загрузить GEDCOM (multipart), стартовать import job.
- `GET /trees/{id}/imports/{job_id}` — статус и отчёт.
- `GET /trees/{id}/persons` — пагинация, фильтры по имени/датам/местам.
- `GET /trees/{id}/persons/{person_id}` — карточка с событиями, родителями, детьми, супругами, источниками.
- `GET /trees/{id}/persons/{person_id}/ancestors?generations=10`
- `GET /trees/{id}/persons/{person_id}/descendants?generations=5`
- `GET /trees/{id}/export?format=gedcom-5.5.5` — обратная выгрузка.

### 7.2 Подзадачи

1. FastAPI-проект, OpenAPI 3.1 авто-схема.
2. Аутентификация через Clerk JWT.
3. Rate limiting, request size limits (GEDCOM до 500 MB).
4. Background-обработка через `arq` (импорт большого GED не должен блокировать ответ).
5. Идемпотентность: загрузка одного и того же GED дважды не дублирует данные (детект по хешу + entity resolution).
6. WebSocket / SSE для прогресса импорта.
7. Health checks, structured logging (JSON), Prometheus-метрики.
   - Phase 9.0 (done 2026-04-27): `prometheus-client` + `GET /metrics` exposition,
     5 collectors (`treegen_hypothesis_created_total`, `_review_action_total`,
     `_compute_duration_seconds`, `_import_completed_total`,
     `_dedup_finder_duration_seconds`) wired в `hypothesis_runner` /
     `import_runner` / `familysearch_importer` / `dedup_finder` /
     `api/hypotheses` review. Grafana / Alertmanager / OpenTelemetry tracing —
     follow-up Phase 9.x.

---

## 8. Фаза 4 — Веб-сайт MVP

**Цель:** пользователь может зарегистрироваться, загрузить GED, увидеть дерево.

### 8.0 Status

| Подфаза | Содержание | Статус |
|---|---|---|
| **4.3** | Pedigree-дерево предков в браузере: ADR-0013 (react-d3-tree выбран в качестве view layer), backend `GET /persons/{person_id}/ancestors?generations=N` (BFS по семейным связям, до 10 поколений = 1024 узлов), `AncestorTreeNode` Pydantic schema, 4 интеграционных теста на `test_ancestors_*`, фронтенд-страница `/persons/[id]/tree` с компонентом `<PedigreeTree>` (154 строк, SVG foreignObject + Tailwind style), `react-d3-tree@^3.6.6` в `apps/web/package.json`, ссылка "View family tree" из карточки персоны. See ADR-0013, PR-Phase-4.3. | done (2026-04-27) |
| **4.4** | `/trees/[id]/persons`: поиск по имени (ILIKE) + фильтр по году рождения (BIRT.date_start range), debounce 300 мс, URL-state, 18 интеграционных тестов на бэкенде. См. PR-Phase-4.4. | done (2026-04-27) |
| **4.4.1** | Daitch-Mokotoff phonetic search: `persons.surname_dm` / `persons.given_name_dm` TEXT[] columns + GIN-индексы (миграция 0007), import_runner и backfill-скрипт заполняют DM с auto-транслитерацией кириллицы, `?phonetic=true` на search-эндпоинте использует Postgres ARRAY overlap (`&&`), UI checkbox + «via phonetic match» badge. Zhitnitzky / Жытницкий / Zhytnicki / Schitnitzky → один bucket-set. См. PR-Phase-4.4.1. | done (2026-04-27) |
| **4.6** | Manual person merge UI: ADR-0022, `PersonMergeLog` ORM + миграция 0006, `services/person_merger.py` (compute_diff/apply_merge/undo_merge/check_hypothesis_conflicts), 4 эндпоинта в `api/persons.py` (preview/commit/undo/merge-history) с обязательным `confirm:true` (Pydantic `Literal[True]` → 422 без него), `/persons/[id]/merge/[targetId]` page (side-by-side, choose survivor, confirm dialog, success/undo state), Phase 4.5 «Mark as same» включён. CLAUDE.md §5 enforce'ится в коде. См. ADR-0022, PR-78/81/Phase-4.6-merge-ui. | done (2026-04-27) |
| **4.9** | Hypothesis review UI закрывает критическую дыру workflow (Phase 7.x ORM + API уже есть, но юзер их не видел): `/trees/[id]/hypotheses` (filter status / type / min_confidence + URL state + pagination) → `/hypotheses/[id]` (subjects, score meter, evidence breakdown по rule_id с DNA segments / source citations агрегатами, sticky action row Approve/Reject/Defer). Approve same_person → редирект в Phase 4.6 merge UI с `?from_hypothesis=N` баннером. Добавлен `DEFERRED` в `HypothesisReviewStatus` enum (4-й валидный статус, не блокирует merge как REJECTED). Light notification: `parser_service.services.notifications` POST'ит в notification-service на каждую новую `pending_review` гипотезу, fire-and-forget с graceful skip если сервис недоступен или env-var не задан. Pending-count badge на persons page header. См. PR-Phase-4.9. | done (2026-04-27) |
| **4.7** | Source viewer UI поверх Phase 3.6 evidence-graph эндпоинтов: `/trees/[id]/sources` (paginated list + ILIKE search по `title`/`abbreviation`/`author`, debounced URL state, citation_count badge), `/sources/[id]` (метаданные SOUR, linked persons/events/families с denormalized `display_label` — имена людей, `BIRT 1850`, `Husband × Wife` — без N round-trip'ов из UI), Sources-секция на карточке персоны (отдельный useQuery, QUAY badge, EVEN/ROLE, quoted_text). Backend: `SourceSummary.citation_count` (LEFT JOIN GROUP BY), `SourceLinkedEntity.display_label` (один SELECT на каждую связанную таблицу), `q` query param на list-эндпоинте. vitest: `quay-badge`, `fetchSources` URL building. pytest: 8 интеграционных кейсов, в т.ч. display_label для всех трёх entity типов и q-search wildcard escape. См. PR-84 (WIP draft) + Phase-4.7-finalize. | done (2026-04-28) |
| **4.12** | Landing + onboarding flow + i18n foundation. Public `/` (hero, 4 value-props, screenshots-placeholders, pricing teaser, waitlist), `/demo` (read-only sample tree, синтетика), `/pricing` (Free / Pro + FAQ), `/onboarding` (3-step wizard: source → import → done на pure-function reducer). next-intl + cookie-based locale (`NEXT_LOCALE`) + middleware `Accept-Language` detection + `messages/{en,ru}.json`. Empty-state: `/dashboard` с 0 trees → redirect `/onboarding`. Lead capture: `WaitlistEntry` ORM + миграция 0014 + `POST /waitlist` в parser-service (idempotent, lower-case email, email НЕ логируется) + Next.js `/api/waitlist` proxy. SEO: per-route metadata, `app/sitemap.ts`, `app/robots.ts` (Disallow auth-protected). vitest: 4 теста landing/waitlist + 8 reducer-тестов onboarding-machine + 8 i18n-helpers. pytest: 6 кейсов /waitlist (idempotency, email lowercase, EmailStr 422, extra-fields 422, no-email-in-logs). См. ADR-0035, PR-Phase-4.12. | done (2026-04-28) |

### 8.1 Страницы

- Лендинг — Phase 4.12 done: hero + 4 value-props + pricing teaser + waitlist (см. ADR-0035).
- `/login`, `/signup` (Clerk — Phase 4.10).
- `/demo` — Phase 4.12 done: read-only sample tree (синтетика, без auth).
- `/pricing` — Phase 4.12 done: Free / Pro карточки + FAQ.
- `/onboarding` — Phase 4.12 done: 3-step wizard (source → import → done) для нового user'а.
- `/dashboard` — Phase 4.12 skeleton: список деревьев, импортов; empty-state (0 trees) → редирект на `/onboarding`.
- `/trees/[id]` — обзор дерева: stats, recent imports.
- `/trees/[id]/persons` — поиск по персонам (Phase 4.4: ILIKE + год; Phase 4.4.1: phonetic Daitch-Mokotoff toggle; Phase 4.9: pending-hypotheses badge в шапке).
- `/trees/[id]/persons/[personId]` — карточка персоны (Phase 4.7: Sources-секция с citations).
- `/trees/[id]/sources` — Phase 4.7 paginated source list + ILIKE search.
- `/sources/[id]` — Phase 4.7 source detail с linked persons/events/families.
- `/trees/[id]/hypotheses` — Phase 4.9 review queue (filter status/type/confidence).
- `/hypotheses/[id]` — Phase 4.9 detail + approve/reject/defer (approve same_person → Phase 4.6 merge UI).
- `/trees/[id]/import` — загрузка GED с drag & drop.

### 8.2 Подзадачи

1. Next.js App Router, серверные компоненты по умолчанию.
2. Дизайн-система на shadcn/ui, тёмная/светлая темы, многоязычность (i18n: en + ru минимум).
3. Strict TypeScript, Zod для валидации форм.
4. React Query для server state.
5. Drag & drop загрузка с прогрессом.
6. Локализация и форматирование дат (генеалогические даты — особый случай).

---

## 9. Фаза 5 — Визуализация древа

**Цель:** дерево не хуже, чем у Geni/MyHeritage, для деревьев до 100 000 персон.

### 9.1 Виды визуализации

1. **Pedigree chart** — предки, веером или вертикально.
2. **Descendant chart** — потомки.
3. **Hourglass chart** — предки + потомки одновременно.
4. **Family group sheet** — таблица одной семьи.
5. **Timeline view** — события по годам.
6. **Geo map** — события на карте мира.
7. **DNA cluster graph** — отдельная фаза (см. фазу 6).

### 9.2 Технические вызовы

- **Производительность:** виртуализация, рендер только видимой части, WebGL для гигантских графов (`pixi.js` или `regl`).
- **Layout:** для больших деревьев D3 hierarchy не работает, нужен инкрементальный layout.
- **Coupling visual ↔ data:** при редактировании персоны в карточке — обновлять граф без перерисовки всего.

---

## 10. Фаза 6 — DNA Analysis Service

**Цель:** реплицировать (и превзойти) `cM Explainer`, `Chromosome Browser`, `AutoCluster` от Geni и DNAGedcom.

### 10.1 Импорт ДНК-данных

Парсеры под каждый формат (CSV/ZIP):

- **Ancestry** — match list, shared matches, ThruLines.
- **MyHeritage** — match list, shared matches, theory of family relativity, segments.
- **23andMe** — relatives list, segments.
- **FamilyTreeDNA** — family finder, big Y, mtDNA.
- **GEDmatch** — one-to-many, one-to-one, autosomal, X-DNA, triangulation.
- **LivingDNA** — match list.

### 10.2 Алгоритмы

1. **AutoCluster** — кластеризация матчей по shared matches (алгоритм Leeds Method + community detection, например Louvain).
2. **Triangulation** — поиск 3+ человек с пересекающимся сегментом → общий предок.
3. **cM-to-relationship probability** — Байес на основе AncestryDNA / Shared cM Project статистик.
4. **Endogamy detection** — критично для еврейских/изолятных популяций.
5. **Phasing** — определение, от какого родителя сегмент.
6. **Haplogroup analysis** — Y-DNA / mtDNA, сопоставление с гаплогрупповыми деревьями (ISOGG, FTDNA).
7. **Cohen / Levite modal haplotype detection** — domain-specific.

### 10.3 Подзадачи

1. Парсеры всех 6 форматов с тестами на анонимизированных примерах.
2. Унифицированная модель `dna_match` с `kit_id`, `match_kit_id`, `total_cm`, `largest_segment`, `segment_count`, `chromosome_segments[]`, `predicted_relationship`, `source_platform`.
3. Реализация Leeds Method + Louvain в `packages/dna-analysis`.
4. Chromosome Browser SVG-компонент во frontend.
5. Расчёт probabilities через таблицу Shared cM Project (зашить в код, она публичная).
6. Endogamy adjustment factor для еврейских деревьев.

> **Юридический момент:** хранение ДНК-данных требует явного согласия (informed consent), чёткой политики удаления, шифрования at-rest. Заложите это в схему БД с самого начала (`dna_kits.consent_status`, `dna_kits.consent_signed_at`, `dna_kits.delete_after`).

### 10.4 Статус подфаз

| Подфаза | Описание | Статус |
|---|---|---|
| 6.0 | Парсеры платформ + privacy ADR-0012 | ✅ Done |
| 6.1 | Pairwise matching (half-IBD, Shared cM Project) — ADR-0014 | ✅ Done |
| 6.2 | dna-service: consents + storage + matching API — ADR-0020 | ✅ Done |
| 6.2.x | Consent audit-log + per-user list endpoint | ✅ Done |
| **6.3** | **Match list/detail UI + chromosome painting + link-to-person — ADR-0033** | ✅ **Done (2026-04-28)** |
| 6.4 | Triangulation + Bayes-prior из дерева | 🔜 Planned |
| 6.5 | Imputation + IBD2 + dedicated `dna_match_segments` table | 🔜 Planned |

---

## 11. Фаза 7 — Entity Resolution / Дедупликация

**Цель:** когда импортируется второй GED или DNA-данные, корректно сливать с существующими персонами.

### 11.1 Сигналы для матчинга

- **Имена** (с учётом транслитерации, орфографии, девичьих фамилий, патронимов).
- **Даты рождения/смерти** (с учётом неопределённости).
- **Места** (нормализованные исторически).
- **Родители/супруги/дети** (граф-контекст).
- **Источники** (если из одного и того же source — высокая уверенность).
- **ДНК** (если оба связаны с DNA kit — gold standard).

### 11.2 Подзадачи

1. Имена: Jaro-Winkler / Levenshtein + soundex / Daitch-Mokotoff Soundex (для еврейских имён).
2. Эмбеддинги имён через Voyage — лучше для редких/иностранных вариантов.
3. Даты: перекрытие интервалов с учётом uncertainty.
4. Места: нормализация через свой gazetteer (исторические границы).
5. Графовый матчинг: «если у двух кандидатов совпадают 2+ родственника по другим сигналам — это сильный signal».
6. Pairwise scorer + threshold + manual review queue для пограничных случаев.
7. UI для merge: показать кандидатов, разрешить пользователю принять/отклонить/отредактировать.

> Никаких автоматических merge без manual review для высоких ставок (близкое родство). Для далёких — можно auto-merge с возможностью отката.

---

## 12. Фаза 8 — Inference Engine (движок гипотез)

**Цель:** не просто хранить дерево, а активно искать новые связи и противоречия.

### 12.1 Типы гипотез

- «Person A — отец Person B» с confidence 0.73, supporting evidence: 2 источника + DNA cluster.
- «Family X и Family Y происходят от общего предка в 1820–1840» — на основе DNA + общая фамилия + общий регион.
- «В дереве конфликт: дата рождения Person Z в источнике A не совпадает с источником B».
- «Person Q вероятно соответствует кому-то в внешнем дереве на FamilySearch».

### 12.2 Архитектура

- **Rules engine** для детерминированных правил (например, «ребёнок не может родиться до родителей»).
- **Probabilistic engine** для гипотез с весами (Bayesian network или просто scoring).
- **LLM-агент** для гипотез, требующих рассуждений на естественном языке (анализ записей в архивах, перекрёстные ссылки).
- **Очередь на обработку:** при каждом импорте → запускается job, который пересматривает гипотезы для затронутых персон.

### 12.3 Подзадачи

1. Каталог типов гипотез с YAML-определениями.
2. Реализация 10–15 базовых rules (consistency checks).
3. Scoring-функция с весами доказательств.
4. Persistance гипотез с rationale (текстовое объяснение, почему).
5. UI: вкладка «Hypotheses» в карточке персоны, со списком «Supporting / Contradicting / Neutral».
6. Кнопка «Promote to fact» / «Reject» с записью решения в audit log.

---

## 13. Фаза 9 — Интеграции с архивами

**Цель:** автоматический поиск по внешним базам.

### 13.1 Юридическое исследование

> **Status (2026-04-28):** Research done — see
> [`docs/research/archive-integrations-2026.md`](./docs/research/archive-integrations-2026.md).
> Phase 5.1 (FamilySearch) is integrated; the research note ranks remaining
> sources by readiness and partnership cost.

Для каждой платформы составлена таблица в research note (auth, rate limits,
licensing, EE-Jewish coverage, partnership effort). Краткая сводка:

| Платформа | Public API | Commercial use | Auth | TOS-ограничения |
|---|---|---|---|---|
| FamilySearch | ✅ (integrated, Phase 5.1) | С разрешения | OAuth2+PKCE | Compatible Solution для prod |
| MyHeritage | ✅ (gated) | По договору | App key (manual) | Bound to MH privacy |
| Geni | ✅ | С условиями | OAuth2 | Bound to Geni privacy |
| Ancestry | ❌ | B2B-only | — | Скрейпинг = бан |
| WikiTree | ✅ | **Запрещено без consent** | None / cookie | CC-BY-SA, AUP |
| JewishGen / JRI-Poland | ❌ | Партнёрство | — | Атрибуция, per-collection |
| GenTeam.eu | ❌ | Партнёрство | — | Volunteer-indexed |
| YIVO | ❌ (ArchivesSpace API возможно) | Per-permission | CJH account | YIVO copyright |
| BillionGraves | ✅ | По tier | API key | Overlap с FamilySearch |
| Wikimedia Commons | ✅ | Свободно | None / OAuth | CC, атрибуция обязательна |
| Szukaj w Archiwach (PL) | ❌ (OAI-PMH revival?) | Свободно | — | Атрибуция |
| Lithuania (EAIS) | ⚠️ partial OAI-PMH | — | — | Атрибуция |
| Belarus (NIAB / NARB) | ❌ | — | Correspondence | Заблокировано политически |
| Ukraine (State Archival Service) | ❌ (через FS) | — | Correspondence | Заблокировано военной обстановкой |

> **Принцип:** не делать ничего, что нарушает TOS. Для платформ без API —
> только функция «помочь пользователю поискать там вручную» (deep links,
> готовые запросы), без скрейпинга.

### 13.2 Phase 9.x ordering

Полное обоснование — в research note. Кратко (Tier A — engineering можно
начинать сейчас; Tier B — заблокировано на approval-аппрувал; Tier C —
многомесячный partnership):

**Tier A — public API, ship first:**

1. **Phase 9.1 — Wikimedia Commons** (place imagery, ~3 дня, тривиально).
2. **Phase 9.2 — WikiTree adapter** (read-only public profiles, ~1 неделя;
   commercial-consent paperwork требуется для Phase 12).
3. **Phase 9.3 — BillionGraves** (cemetery records, ~1 неделя; пересмотреть
   после 9.1, частично перекрывается с FamilySearch).

**Tier B — engineering после approval (outreach в параллель с 9.1):**

1. **Phase 9.4 — MyHeritage Family Graph** (app-key approval 4–8 нед,
   затем ~1.5 нед engineering).
2. **Phase 9.5 — Geni** (сначала подтвердить strategic support от
   MyHeritage/Geni, затем ~1 неделя; пропустить если поддержка не
   подтверждена).

**Tier C — partnership-only, multi-month timelines:**

1. **Phase 9.6 — JewishGen + JRI-Poland data partnership**
   (8–16+ нед outreach; engineering ~2–3 нед per data feed). **Highest
   strategic value** для нашей EE Jewish ниши. Промежуточный deliverable —
   deep-link smart-search helper (weekend-hackable).
2. **Phase 9.7 — GenTeam.eu** (Vienna / former AT-HU, 4–12 нед outreach).
3. **Phase 9.8 — YIVO ArchivesSpace API** (4–8 нед outreach; ~1 неделя
   engineering если включат REST API).
4. **Phase 9.9 — Polish State Archives OAI-PMH revival** (8–16+ нед).

**Deferred:** Ancestry (B2B disproportionate to scale), Belarus (political
блок), Ukraine direct (через FamilySearch).

### 13.3 Подзадачи (engineering scaffold)

1. `archive-service` — отдельный сервис со своими адаптерами под каждый
   источник. Reuse pattern из `packages/familysearch-client/` (ADR-0011).
2. Адаптер `WikimediaAdapter` (Phase 9.1).
3. Адаптер `WikiTreeAdapter` (Phase 9.2).
4. Адаптер `BillionGravesAdapter` (Phase 9.3).
5. Smart query builder: из персоны генерирует запросы во все источники
   с учётом транслитерации (используется и для адаптеров, и для
   deep-link smart-search).
6. Deep-link smart-search helper для no-API источников (JewishGen,
   JRI-Poland, Szukaj w Archiwach, GenTeam, YIVO, Ancestry, Geni как
   fallback). Per-person UI panel «External searches».
7. Deduplication results across sources (расширение Phase 7 entity
   resolution).
8. UI: для каждой персоны — вкладка «External matches» со списком
   найденных в архивах + «External searches» для no-API источников.
9. Адаптеры Tier B (MyHeritage, Geni) — после approval.
10. Адаптеры Tier C (JewishGen и т.д.) — после partnership signed.

---

## 14. Фаза 10 — AI слой

**Цель:** применить LLM там, где он реально полезен, не везде.

### 14.1 Use cases

1. **Document analyzer:** загрузить скан метрической записи / переписи → OCR → распарсить запись → предложить персон/события для добавления.
2. **Translator:** автоматический перевод записей с польского/идиша/иврита/русского XIX века.
3. **Research assistant:** «Что я ещё могу узнать о моём прапрадеде?» — агент составляет план поиска.
4. **Hypothesis explainer:** «Почему система считает, что A и B — братья?» — генерация rationale на естественном языке.
5. **Document summarizer:** длинное письмо XIX века → структурированные факты.
6. **Vector search:** «найди в моих документах упоминания всех персон по фамилии Х» — semantic search.

### 14.2 Архитектура

- **RAG** над всеми загруженными документами и фактами дерева.
- **Tool use / function calling:** агент может вызывать внутренние API (поиск персон, добавление гипотезы, OCR-документа).
- **Subagents в Claude Code** для разработки (см. секцию 19).

### 14.3 Подзадачи

1. OCR-pipeline через Cloud Vision с post-processing для исторических шрифтов.
2. Эмбеддинги всех текстовых артефактов через Voyage.
3. RAG-сервис: ранжирование, reranking, контекст-инъекция.
4. Tool-definitions для агента: `search_persons`, `add_hypothesis`, `fetch_external_archive`, `translate_document`, etc.
5. Chat UI на сайте: agent вшит в страницу персоны/дерева.
6. Логирование всех LLM-вызовов с токенами и стоимостью (для контроля cost).

---

## 15. Фаза 11 — Сообщество и совместная работа

### Phase 11.0 — DB + permission API ✅ (см. ADR-0036)

- ORM `TreeMembership` + `TreeInvitation` + миграция 0015 (partial unique
  index гарантирует ровно один OWNER на дерево).
- 7 sharing endpoints в `services/parser-service/src/parser_service/api/sharing.py`:
  POST/GET `/trees/{id}/invitations`, DELETE `/invitations/{id}`,
  POST `/invitations/{token}/accept`, GET `/trees/{id}/members`,
  PATCH/DELETE `/memberships/{id}`.
- Permission gate `require_tree_role(TreeRole.X)` применён к
  `/trees/{id}/persons`, `/trees/{id}/persons/search`, `/trees/{id}/sources`
  (VIEWER+), `/persons/{id}/merge*` (EDITOR+ через `require_person_tree_role`).
- Auth-stub `parser_service.auth.get_current_user` (X-User-Id header → fallback
  на settings.owner_email). Контракт стабилен для замены на Clerk JWT в Phase 4.10.

### Phase 11.1 — UI + email + audit (next PR)

- `apps/web/src/app/trees/[id]/sharing/page.tsx` — owner UI: list members,
  invite-by-email form, pending invitations, revoke buttons.
- `apps/web/src/app/invitations/[token]/page.tsx` — accept-flow.
- Site-header tree-picker dropdown.
- Email delivery через notification-service (event_type=tree_invitation),
  SendGrid free-tier для MVP.
- Audit-log integration: GET /trees/{id}/audit?type=membership.
- Owner-transfer flow: POST /trees/{id}/transfer-ownership.

### Phase 11.2+ (future)

- Публичные / приватные деревья — TreeVisibility=PUBLIC + read-only без auth.
- Комментарии, обсуждения на уровне персоны/семьи.
- Связь с Telegram-каналом, Discord, Facebook (только публикация дайджестов).
- Forum/Q&A — отложено, можно использовать готовое (Discourse, Discord).

---

## 16. Фаза 12 — Платежи и тарифы

### Тарифы

| Тариф | Цена | Что включено |
|---|---|---|
| **Beginner** | $X/мес | 1 дерево, до 5000 персон, GEDCOM импорт/экспорт, базовая визуализация |
| **Advanced** | $Y/мес | + DNA-анализ, кластеризация, chromosome browser, до 50 000 персон, 3 дерева |
| **Super** | $Z/мес | + автопоиск по архивам, AI-ассистент, безлимит персон |
| **Private Investigation** | от $W (custom) | + ваше личное участие, индивидуальные исследования |

### Подзадачи

1. Stripe Checkout + Customer Portal (подписки, отмена, апгрейд).
2. PayPal Subscriptions API.
3. Coinbase Commerce для crypto.
4. Feature flags на основе tier.
5. Usage metering: импорты, AI-запросы (квоты по tier).
6. Билинговые письма, инвойсы.

---

## 17. Фаза 13 — Безопасность и деплой на GCP

### Архитектура GCP

- **Private VPC** с разделением subnets (public, private, db).
- **Private GKE cluster** для тяжёлых workloads (DNA analysis, AI inference).
- **Cloud Run** для API-сервисов (auto-scale, дешевле).
- **AlloyDB for PostgreSQL** в private subnet, с CMEK.
- **Cloud Storage** для GEDCOM/DNA/документов с CMEK + lifecycle rules.
- **Secret Manager** для всех секретов, ротация.
- **Cloud KMS** ключи, hardware-protected.
- **Cloud Armor + WAF** на load balancer.
- **Identity-Aware Proxy** для админки.
- **Security Command Center** для posture monitoring.
- **VPC Service Controls** вокруг чувствительных API.
- **Cloud Audit Logs** → BigQuery для долгосрочного аудита.

### Шифрование

- **At rest:** CMEK на storage и AlloyDB.
- **In transit:** TLS 1.3 везде.
- **Application-level:** дополнительный envelope encryption для DNA-сегментов (даже от своих DBA).

### GDPR / privacy

- Right to access (экспорт всех данных пользователя).
- Right to deletion (полное удаление с подтверждением).
- Data portability (GEDCOM-экспорт).
- DPA, privacy policy, cookie consent.
- Data residency (опция: EU-only хранение).

### «Завещание данных»

Фича из ваших заметок: пользователь указывает наследника аккаунта. После подтверждения смерти (через trusted contact + срок ожидания) — наследник получает доступ. Реализуется через `users.beneficiary_user_id` + workflow.

---

## 18. Фаза 14 — Telegram-бот и автоматизация

### Phase 14.0 — Bot scaffold + opt-in account linking ✅ (см. ADR-0040)

- `services/telegram-bot/` — FastAPI webhook receiver, aiogram 3.x
  Dispatcher, handlers `/start /imports /persons /tree`. `/start` —
  end-to-end (mint one-time link-token + reply with web URL); остальные
  команды — Phase 14.1 stub'ы.
- `telegram_user_links` ORM + alembic 0018: per-user opt-in mapping
  `(user_id, tg_chat_id)` без soft-delete (revocation = `revoked_at`).
- `POST /telegram/link/confirm` — consume link-token, INSERT row.
  Race-safe (UNIQUE constraint + IntegrityError → 409). Идемпотентно
  для повторного confirm'а того же user'а.
- Webhook security: `X-Telegram-Bot-Api-Secret-Token` validated
  constant-time через `hmac.compare_digest`. Без секрета (`webhook_secret=""`)
  → 503.
- Tests: signature 401/200, replay-attack 410, conflict 409,
  idempotent 200, malformed 422 — без реальных вызовов
  `api.telegram.org`.

### Phase 14.1 — Notification fan-out + real command bodies (next PR)

- notification-service → telegram-bot выпускает фан-аут на per-user
  per-event-type opt-in (расширение ADR-0024 / 0029).
- `/imports`, `/persons <name>`, `/tree` — реальный fetch из
  parser-service. Требуется решение по cross-service-auth (отдельный
  ADR — кандидаты: machine token + impersonation header / per-user JWT
  через api-gateway).
- Inline-keyboards для подтверждения dedup-suggestion'ов и hypothesis-review'а.

### Phase 14.2+ (future)

- Multi-language (`users.locale`) — ru/en/he.
- Команды `/digest` (еженедельный summary), `/search Иванов 1850 Минск`.
- Bot announcements в Telegram-канал (только owned by owner — не
  user fan-out).

---

## 19. Claude Code: настройка субагентов и MCP

### 19.1 Структура `CLAUDE.md`

Содержит:

- Описание проекта (краткое).
- Конвенции: язык кода (en) / комментариев (ru), стили, naming.
- Архитектурные принципы (evidence-first, provenance, versioning).
- Запрет: коммиты в `main`, секреты в коде, breaking changes без ADR.
- Команды: как запустить тесты, миграции, dev-окружение.

### 19.2 Subagents (`.claude/agents/`)

Создать специализированных субагентов:

1. `gedcom-expert` — знает спецификацию 5.5.5, проприетарные расширения, особенности экспортов разных платформ. Используется при работе с парсером.
2. `dna-analyst` — знает алгоритмы кластеризации, статистики Shared cM, endogamy. Используется в DNA-фазах.
3. `db-architect` — следит за миграциями, индексами, перформансом запросов.
4. `security-reviewer` — проверяет PR на security issues, особенно на DNA/PII-обработку.
5. `code-reviewer` — общий ревью PR.
6. `test-writer` — генерирует pytest-тесты для новых функций.
7. `historian` — знает историю Восточной Европы XIX–XX вв., помогает с нормализацией мест.

### 19.3 MCP servers

Подключить к Claude Code:

- Filesystem MCP (стандартный).
- GitHub MCP — для PR, issues, code review.
- PostgreSQL MCP — Claude может проверять схему/делать read-only запросы к dev-БД.
- Кастомные MCP (написать свои):
  - `gedcom-mcp` — Claude может валидировать/парсить GED-файлы.
  - `archive-mcp` — Claude может делать поиски в FamilySearch/Wikimedia из чата.

### 19.4 Slash-команды

Создать в `.claude/commands/`:

- `/import-personal-ged` — запустить импорт вашего тестового GED.
- `/db-reset` — сбросить локальную БД и накатить миграции.
- `/test-coverage` — pytest с coverage и отчёт.
- `/new-service <name>` — скаффолд нового сервиса.

---

## 20. Юридические аспекты (читать перед фазой 6 и 9)

> Я не юрист, проконсультируйтесь с профильным юристом перед коммерческим запуском. Ниже — рабочий список.

1. **DNA = special category data** в GDPR (Art. 9). Требует явного согласия, DPIA, особых мер защиты.
2. **HIPAA** в США — если работаете с американцами в medical context, может быть применимо. Скорее всего вы не в этом scope, но проверить.
3. **TOS каждой платформы:** запрос API-доступа на коммерческое использование. Письменное разрешение перед запуском.
4. **Договор с пользователем** (Terms of Service) + Privacy Policy + DPA.
5. **Возрастные ограничения:** ≥ 18 для DNA-данных в большинстве юрисдикций.
6. **Подростки и privacy:** для несовершеннолетних в дереве — ограничения видимости.
7. **Living people in trees:** возможность скрыть всех живых от публичного просмотра (стандарт индустрии).
8. **Архивные данные:** уважать лицензии (CC, public domain, restricted) и атрибуцию.

---

## 21. Roadmap по времени (для соло-разработчика)

> Цифры реалистичны, но сильно зависят от вашей плотности работы.

| Фаза | Срок (соло, full-time) | Срок (соло, part-time 20ч/нед) |
|---|---|---|
| 0. Фундамент | 1 нед | 2 нед |
| 1. GEDCOM Parser | 4–6 нед | 10–14 нед |
| 2. Модель данных | 1–2 нед | 3–4 нед |
| 3. Backend API | 2–3 нед | 5–7 нед |
| 4. Web MVP | 3–4 нед | 7–9 нед |
| 5. Визуализация | 2–3 нед | 5–7 нед |
| 6. DNA Analysis | 6–8 нед | 14–18 нед |
| 7. Entity Resolution | 3–4 нед | 7–9 нед |
| 8. Inference Engine | 4–6 нед | 10–14 нед |
| 9. Архивы (1 источник) | 2–3 нед | 5–7 нед |
| 10. AI слой | 3–5 нед | 7–12 нед |
| 11. Сообщество | 2–3 нед | 5–7 нед |
| 12. Платежи | 1–2 нед | 3–4 нед |
| 13. GCP деплой | 3–4 нед | 7–9 нед |
| 14. Telegram | 1 нед | 2 нед |

**Итого до публичного beta:** ~9–12 мес full-time / ~18–24 мес part-time.

> **Рекомендация:** не идти линейно. После фаз 1–4 (есть рабочий импорт + просмотр) — публиковать closed alpha для 5–10 знакомых генеалогов и собирать обратную связь параллельно с фазами 5–8.

---

## 22. Метрики успеха (что измерять с фазы 1)

**Технические:**

- Парсинг 100 МБ GEDCOM < 2 мин.
- Поиск персоны по имени в дереве 100 000 персон < 100 мс.
- Точность entity resolution на синтетическом наборе: precision > 0.95 при recall > 0.85.
- Покрытие тестами > 80%.
- p95 API latency < 300 мс.

**Продуктовые:**

- Время от регистрации до первого загруженного GED < 5 мин.
- % импортов без ошибок > 95%.
- DAU / MAU.
- NPS среди платных пользователей.

**Бизнес:**

- Conversion free → paid.
- Churn < 5% в месяц.
- LTV / CAC > 3.

---

## 23. Что делать прямо сейчас (next 7 days)

1. **День 1–2:** инициализация репозитория, Docker-окружение, CLAUDE.md, базовый CI.
2. **День 3:** ER-диаграмма данных (только основные сущности), ADR по версионированию.
3. **День 4–7:** скелет `packages/gedcom-parser`: lexer + tokenizer + первые парсеры HEAD/TRLR/INDI/FAM. Первый зелёный тест на минимальном GED.
4. **К концу первой недели:** ваш личный GED парсится в Python-объекты (даже без БД), можно `print(stats)`.

После этого — переход к фазе 2 параллельно с расширением парсера.

---

## 24. Открытые вопросы (зафиксировать как ADR при ответе)

1. **Версионирование:** bi-temporal vs event sourcing vs audit-log? — рекомендую audit-log на старте, переход к bi-temporal в фазе 8.
2. **Хранение DNA-сегментов:** в БД или в Cloud Storage с метаданными в БД? — сегменты могут быть огромными у крупных kits, рекомендую Storage + кэш.
3. **Графовая БД для inference?** — на старте Postgres + recursive CTE достаточно. Neo4j рассмотреть, если recursive query станет медленной.
4. **Self-hosted LLM vs API?** — на старте только API. Self-hosted (например, для OCR) — после фазы 10, если стоимость API станет проблемой.
5. **Mobile app?** — отложено до публичного beta. PWA достаточно сначала.

---

## Приложение А — шаблон промпта для Claude Code в начале каждой фазы

```text
Контекст: я работаю над фазой N проекта AutoTreeGen. См. ROADMAP.md, секцию N.
Прочитай: CLAUDE.md, docs/data-model.md, docs/architecture.md, ROADMAP.md.

Задача: <конкретная подзадача из фазы>.

Требования:
- Код на английском, комментарии и docstrings — на русском.
- Pydantic v2, FastAPI 0.115+, SQLAlchemy 2 (async), Python 3.12.
- Тесты (pytest) с покрытием > 80% новой логики.
- ADR в docs/adr/, если решение архитектурное.
- Conventional commits.
- Не коммитить в main, создать ветку feat/<short-name>.

Прежде чем писать код, сформулируй план из 5-10 шагов и дождись моего подтверждения.
```

---

## Приложение Б — список первых ADR, которые нужно создать

1. **ADR-0001:** Выбор Python + FastAPI для backend.
2. **ADR-0002:** Монорепозиторий и его структура.
3. **ADR-0003:** Стратегия версионирования данных (audit-log).
4. **ADR-0004:** PostgreSQL + pgvector vs отдельный векторный store.
5. **ADR-0005:** Стратегия entity resolution (rules + scoring + manual review).
6. **ADR-0006:** Хранение DNA-сегментов (Storage + кэш в БД).
7. **ADR-0007:** GEDCOM 5.5.5 как канонический формат вход/выход.
8. **ADR-0008:** Стратегия мультиязычности и транслитерации.
9. **ADR-0009:** Подход к гипотезам и evidence-graph.
10. **ADR-0010:** Аутентификация (Clerk vs Auth0 vs self-hosted).

---

## Контекст вашей машины (зафиксировано на старте)

- **ОС:** Windows + WSL2.
- **Режим работы:** full-time (целевые сроки до beta — 9–12 мес).
- **Тестовый GED-файл:** `D:\Projects\TreeGen\Ztree.ged` — основной test fixture для Фазы 1.

---

> Документ — живой. После каждой фазы обновлять колонку `Status` в roadmap, добавлять lessons learned.
