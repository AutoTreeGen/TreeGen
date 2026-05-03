# ADR-0074: Hidden archive registry — data model

- **Status:** Accepted
- **Date:** 2026-05-03
- **Authors:** @autotreegen
- **Tags:** `archive-service`, `data-model`, `phase-22`

## Контекст

Off-catalog архивы (SBU/MVD/ZAGS/Standesamt/AGAD/military) — главный pain
point для serious genealogy: запись существует, но не выложена в online-
каталог. Owner лично заплатил $100 SBU oblast Lviv за паспортный запрос
Naum Katz (Konyukhi/Hrubieszów) — ровно тот случай, когда искать сначала
"где" дороже, чем сам запрос.

Phase 22.1 — _registry_: каталог "куда обращаться + сколько ждать +
сколько платить". Foundation для:

- 22.2 — paid intermediary directory (legal review obligated)
- 22.3 — request letter generator (на родном для архива языке)
- 22.4 — cost dashboard

Решения этого ADR — структура таблицы и где она физически живёт.
Узкие вопросы:

1. **Где живёт таблица?** archive-service vs parser-service vs новый
   "catalog-service".
2. **`record_types` storage** — JSONB-массив vs join-таблица.
3. **Nullable почти всех полей** — допускается ли запись без contact info?
4. **`last_verified` обязательное** — что если данные старые?
5. **Re-org trigger** — когда вынести в отдельный сервис?

## Рассмотренные варианты

### Вариант A — таблица в parser-service, доступ через api-gateway

- ✅ Все доменные таблицы дерева уже там; единая БД проще для деплоя.
- ❌ archive_listings — _не_ доменная сущность дерева (нет tree_id);
  размывает purpose parser-service ("парсинг + tree CRUD").
- ❌ archive-service уже сегодня имеет DB-подключение (Phase 15.5
  Archive Search Planner) — дублирование engine'а излишне.

### Вариант B — таблица в archive-service (SELECTED)

- ✅ Logical fit: archive-service занимается архивами по определению.
- ✅ Memory `feedback_archive_service_curated_catalogs.md` (от
  предыдущего sprint planning) явно фиксирует это правило.
- ✅ Engine + Alembic уже подключены (Phase 15.5).
- ❌ Cross-service shared-models import (`ArchiveListing` в `shared_models.orm`)
  — нужно поддерживать backward compat между сервисами при изменении
  схемы. Принимаем: схема меняется только через alembic + ADR.

### Вариант C — отдельный catalog-service

- ✅ Чистое разделение: catalog-service хранит curated reference data
  (archives, notable persons, cemetery indices, intermediaries…).
- ❌ Premature: пока единственная таблица — overhead Docker+CI'я не
  оправдан.
- ⏳ Re-org trigger ниже: когда join'ы между archive_listings +
  notable_persons + cemetery_index начнут болеть, выносим всё семейство.

### record_types storage — варианты

- **A. JSONB-массив строк** (выбран): `record_types: list[str]`,
  filter через `?` / `@>` JSONB-операторы, GIN-индекс.
  ✅ Один query без JOIN; insert одной строкой; enum мал (~13 значений).
  ❌ Нельзя enforce FK на enum-таблицу — но мы и не хотим (RecordType —
     Python enum, не reference data).
- **B. Join-таблица `archive_listing_record_types`** (rejected):
  ✅ Нормальная форма; FK enforce.
  ❌ JOIN на каждый list endpoint; больше кода CRUD; overkill для
     ~50 строк seed + occasional admin add.

### last_verified — варианты

- **A. NOT NULL обязательное** (выбран):
  ✅ Honest defaults: claim "this archive holds X records" без даты
     подтверждения недопустим. Если нет даты — не добавляем строку.
  ❌ Sometimes user знает только название архива и тип записей; не
     может verify контакты — и не должен. Pre-checking guard в seed
     loader: запись без `last_verified` skip'ается.
- **B. Nullable `last_verified`** (rejected):
  ❌ "Verified" claim без даты — анти-паттерн (см. anti-drift брифа).

## Решение

1. **Таблица `archive_listings` в `archive-service`** через
   `shared_models.orm.archive_listing.ArchiveListing`. Add to
   `SERVICE_TABLES` allowlist (per ADR-0003 schema invariants).
2. **`record_types` и `languages` — JSONB-массивы строк**, GIN-индекс
   по `record_types`.
3. **Большинство полей nullable** (contact info, address, year range,
   fee range, notes), потому что real-world public archive info часто
   неполна. Нелучше иметь запись "country + name + record_types" без
   адреса, чем выдумывать.
4. **`last_verified: date NOT NULL`** — обязательное; seed loader
   пропускает entries без него.
5. **Admin-only mutating endpoints** (POST/PATCH/DELETE) под
   `require_admin` гвардом (`claims.email == settings.admin_email`).

## Последствия

### Положительные

- Pro-users получают answer на вопрос "куда обращаться за SBU paspport"
  в API-форме, а не разыскивая по форумам и group chat'ам.
- Phase 22.2-22.4 строятся на этой таблице без новых migrations: они
  только добавляют колонки или вспомогательные таблицы.
- Inference-engine (Phase 7.x+) сможет в будущем cross-link
  `Provenance.archive_name` (Phase 22.5 / ADR-0071) с конкретным
  `archive_listings.id` — тогда appears `archive_listing_id` FK на evidence.

### Отрицательные / стоимость

- `archive-service` теперь ходит в БД для двух разных read-paths
  (planner + registry). До 22.1 БД использовалась только планировщиком.
  Нет shared cache между ними — оптимизация позже, если поднимется
  load.
- Cross-service `shared_models.orm.ArchiveListing` import создаёт
  слабую связь между archive-service и shared-models. Изменения в
  ORM требуют синхронной alembic migration.

### Риски

- **Stale data.** `last_verified` не enforced как "max age" — UI должен
  показывать дату и/или цвет (Phase 22.4 будет рендерить в cost
  dashboard).
- **Privacy window mismatch.** `privacy_window_years` nullable: если
  не указан, scorer считает что блокировки нет. Может вводить в
  заблуждение для recent civil registry (PL USC, RU ZAGS) — seed
  обязательно проставляет field для всех known cases.
- **Manual admin-CRUD scaling.** При >500 entries owner не успеет
  поддерживать вручную. Тогда — community contribution flow или
  переход на curated YAML in repo (PR review). Но <100 entries —
  CRUD достаточно.

### Что нужно сделать в коде

- ✅ ORM `ArchiveListing` + enums `RecordType` / `AccessMode`.
- ✅ Alembic 0038 + inline seed loader из
  `infrastructure/seed/archive_listings.json`.
- ✅ `SERVICE_TABLES` allowlist add-on.
- ✅ Registry router (`/archives/registry`) + ranking + privacy_blocked.
- ✅ Admin-guard через `claims.email == settings.admin_email`.
- ✅ Tests (unit scorer + endpoint round-trip).

## Когда пересмотреть

- **Re-org trigger** — выделить в `catalog-service`, когда любое из:
  - >1 curated reference table (notable_persons, cemetery_index,
    intermediaries directory) — Phase 22.2+ point.
  - Cross-table join'ы становятся нужны (e.g. "архивы, в которых
    лежат файлы конкретного NotablePerson").
  - Read-load на registry > 100 RPS — нужна dedicated service для
    cache + autoscale.
- **Schema rev** — когда:
  - Inference engine (Phase 7.x+) хочет FK на `archive_listings.id`
    из evidence rows.
  - 22.2 paid_intermediary integration требует FK
    `archive_listings.id ← intermediary_listings.archive_id` (legal
    review prerequisite).

## Ссылки

- Связанные ADR: ADR-0003 (versioning + schema invariants),
  ADR-0033 (Clerk auth), ADR-0055 (archive-service scaffolding),
  ADR-0071 (Phase 22.5 evidence/provenance — archive_name field
  references this registry).
- Brief: `docs/briefs/phase-22-1-archive-registry.md`
- Origin case: owner's $100 SBU passport extract for Naum Katz
  (Konyukhi/Hrubieszów).
