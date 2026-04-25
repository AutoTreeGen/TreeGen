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

```
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

```
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

---

## 8. Фаза 4 — Веб-сайт MVP

**Цель:** пользователь может зарегистрироваться, загрузить GED, увидеть дерево.

### 8.1 Страницы

- Лендинг (объяснение продукта, тарифы — пока заглушки).
- `/login`, `/signup` (Clerk).
- `/dashboard` — список деревьев, импортов.
- `/trees/[id]` — обзор дерева: stats, recent imports.
- `/trees/[id]/persons` — поиск по персонам.
- `/trees/[id]/persons/[personId]` — карточка персоны.
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

### 13.1 Сначала — юридическое исследование (ОБЯЗАТЕЛЬНО)

Для каждой платформы составить таблицу:

| Платформа | Public API | Commercial use | Auth | Rate limits | TOS-ограничения |
|---|---|---|---|---|---|
| FamilySearch | Да | С разрешения | OAuth2 | Есть | Нельзя массово выгружать |
| Geni | Да | С условиями | OAuth2 | Есть | Соблюдать privacy |
| MyHeritage | Да | По договору | API key | Есть | Платный tier для бизнеса |
| Ancestry | Нет публичного | Запрещено без партнёрства | — | — | Скрейпинг = бан |
| 23andMe | Закрыт | Нет | — | — | — |
| FTDNA | Ограничен | По договору | — | — | — |
| GEDmatch | Через UI | Ограничено | — | — | TOS меняется |
| JewishGen | Search UI, нет API | Серая зона | — | — | Уважать атрибуцию |
| JRI-Poland | Search UI | Серая зона | — | — | — |
| genealogyindexer.org | Search UI | Серая зона | — | — | — |
| Szukaj w Archiwach | Открытые данные | Свободно (CC) | — | — | Атрибуция |
| BillionGraves | API есть | По tier | API key | Есть | — |
| Wikimedia Commons | API | Свободно (CC) | — | Есть | Атрибуция |

> **Принцип:** не делать ничего, что нарушает TOS. Для платформ без API — только функция «помочь пользователю поискать там вручную» (deep links, готовые запросы), без скрейпинга.

### 13.2 Подзадачи

1. `archive-service` — отдельный сервис со своими адаптерами под каждый источник.
2. Адаптер `FamilySearchAdapter` — search by name/date/place, fetch person.
3. Адаптер `GeniAdapter` — search profiles, fetch family.
4. Адаптер `MyHeritageAdapter` (по доступу).
5. Адаптер `WikimediaAdapter` — поиск изображений мест/документов.
6. Адаптер `SzukajWArchiwachAdapter` — поиск польских архивов (CC-данные).
7. Smart query builder: из персоны генерирует запросы во все источники с учётом транслитерации.
8. Deduplication results across sources.
9. UI: для каждой персоны — вкладка «External matches» со списком найденных в архивах.

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

- Совместное редактирование дерева (несколько user'ов с разными ролями: owner, editor, viewer).
- Публичные / приватные деревья.
- Комментарии, обсуждения на уровне персоны/семьи.
- Связь с Telegram-каналом, Discord, Facebook (только публикация дайджестов, не двусторонняя интеграция изначально).
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

- Telegram Bot для уведомлений: «Найдено 3 новых матча в вашем дереве».
- Команды: `/search Иванов 1850 Минск`, `/match @kit_id`, `/digest`.
- Webhook вместо polling.
- Bot — отдельный сервис, общается с api-gateway.

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

```
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
