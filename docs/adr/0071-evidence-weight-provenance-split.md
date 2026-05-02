# ADR-0071: Separate evidence weight from provenance

- **Status:** Proposed
- **Date:** 2026-05-02
- **Authors:** @autotreegen
- **Tags:** `data-model`, `evidence`, `archive`, `phase-22`

## Контекст

Phase 22 — Off-Catalog Archive — открывает workflow получения архивных
документов через каналы за пределами обычных online-каталогов: личный
визит, FOIA-запрос, оплачиваемый посредник, оплачиваемый официальный
запрос. Триггер — личный кейс владельца: $100 платный запрос в архив
СБУ за выпиской из паспортного дела Наума Каца.

До Phase 22 evidence-семантика в системе сводила два *разных* вопроса
в одну метрику ("confidence"):

1. **Насколько силён сам документ?** Паспорт vs. вторичный online-индекс
   vs. публичное чужое дерево.
2. **Как именно мы его получили?** Личный визит, FOIA, paid intermediary,
   import from GEDCOM, scrape публичного дерева.

Эта конфляция блокирует:

- **Honest representation** платных запросов и записей через посредников
  (которые в текущей модели выглядят как "доверенный источник", хотя
  channel надо открыто указывать).
- **Audit trails** для pro-genealogist deliverables — клиенты должны
  видеть, *откуда* пришла каждая строка отчёта.
- **ROI tracking** (Phase 22.4 dashboard) — нельзя посчитать «сколько
  потратили на архивные запросы», потому что cost не отделён от других
  атрибутов источника.
- **Evidence aggregation** (Phase 15.x consumers) — confidence-агрегатор
  не может различать «у нас два слабых online-индекса» и «у нас один
  паспорт + одна устная история».

## Рассмотренные варианты

### Вариант 1 — Оставить confidence единым числом (status quo)

- ✅ Никаких миграций.
- ❌ Текущая боль: paid-request workflow не моделируется без обмана.
- ❌ Phase 22.1-22.4 endpoint работа становится невозможной без
  предварительного schema-split.

REJECTED — это и есть проблема, которую мы решаем.

### Вариант 2 — Сделать weight свободной float-колонкой

- ✅ Простая колонка, никакой lookup-таблицы.
- ❌ Drift между row'ами для одного и того же типа документа: один
  паспорт получит weight 0.9, другой 0.85, по прихоти автора. Нечестно
  и не reproducible для audit-trail.
- ❌ Не позволяет команде (не-инженерам) переоценивать tier без деплоя.

REJECTED — нарушает evidence-first принципы (CLAUDE.md §3).

### Вариант 3 — Data-driven lookup-таблица + JSONB provenance (выбран)

- ✅ ``DocumentType`` enum + ``document_type_weights`` lookup: weight
  derived, всегда детерминирован, всегда ∈ {1,2,3}, переоценка tier'а —
  одна UPDATE без деплоя.
- ✅ ``Provenance`` JSONB *strict-shape* (Pydantic) — channel, cost_usd,
  jurisdiction, archive_name, request_reference: каждое поле имеет
  чёткую семантику, агрегации возможны.
- ✅ Backwards compatibility: existing ``confidence`` остаётся, просто
  становится derived = ``weight × match_certainty``.
- ❌ Миграция касается каждой evidence-row при backfill (на момент
  ADR — таблица свежая, backfill = seed, но в будущем переоценка tier'а
  будет требовать перепост).
- ❌ Заставляет потребителей либо использовать lookup-таблицу, либо
  читать stale weight из row.

CHOSEN — единственный вариант, который удовлетворяет evidence-first +
honesty-of-provenance + ROI-tracking требования сразу.

## Решение

Разделяем evidence-row на две оси:

### 1. ``DocumentType`` + ``document_type_weights`` lookup

Новый StrEnum ``shared_models.enums.DocumentType`` фиксирует
exhaustive-список типов документа (passport, birth_certificate, …,
oral_testimony, gedcom_import, dna_match_*, other).

Tier-mapping хранится в **БД-таблице** ``document_type_weights``
(не в Python). Seed: одна row на каждое значение enum'а; weight ∈
{1,2,3} (Tier-N).

Tier-1 (weight=1, government primary): passport, birth/death/marriage
certificates, divorce records, civil register extracts, military
records, naturalization, census, metric books, revision lists.

Tier-2 (weight=2, private primary / corroborating): family bibles,
captioned photographs, headstone inscriptions, obituaries, immigration
passenger lists.

Tier-3 (weight=3, derived / secondary): GEDCOM imports, public-tree
copies, online indices, oral testimony, family letters, DNA matches
(scored separately by Phase 16.x), `other`.

### 2. ``Provenance`` strict-shape JSONB

Новый Pydantic ``shared_models.schemas.evidence.Provenance``:

- ``channel: ProvenanceChannel`` — official_request, paid_intermediary,
  paid_official_request, in_person_visit, family_archive,
  private_collection, online_catalog, dna_platform_export,
  public_tree_scrape, other, **unknown** (backfill-only).
- ``cost_usd: Decimal | None`` — стоимость получения (None ≠ 0; None
  значит «бесплатно или не релевантно», 0 значит «платный канал, но
  заплатили 0»).
- ``request_date / response_date: date | None`` — окно архивного
  запроса для latency-аналитики.
- ``jurisdiction: str | None`` — ISO 3166-1 alpha-2 (UA, IL, US).
- ``archive_name``, ``intermediary``, ``request_reference``,
  ``notes``: свободные строки (с лимитами длины), не для PII.
- ``migrated: bool`` — True для строк, созданных backfill-миграцией.

JSONB на DB-уровне получает CHECK ``provenance ? 'channel'`` —
последняя линия defence-in-depth поверх Pydantic-валидации.

### 3. ``Evidence.confidence`` — derived

Колонка остаётся, **не удаляется и не переименовывается**. Recompute
через SQLAlchemy event listener (``before_insert`` / ``before_update``):
``confidence = weight × match_certainty``.

`match_certainty` ∈ [0, 1] (existing semantics — насколько evidence
подходит к этому конкретному entity). Confidence формально может
превышать 1.0 (max=3.0); потребители нормализуют по своим нуждам.

### 4. ``unknown`` channel — только для backfill

Application-layer валидаторы (Phase 22.1-22.4 endpoint работа)
обязаны отвергать ``channel == unknown`` для свежих записей.
Provenance.is_explicit_channel() — helper для этой проверки.

DB-default `provenance` = `{"channel": "unknown", "migrated": true}`
страхует, что любая raw-INSERT не оставит row без channel'а; row
потом легко найти по `migrated = true` для последующего обогащения.

## Последствия

### Положительные

- ✅ Honest representation платных/посреднических каналов:
  PAID_INTERMEDIARY, PAID_OFFICIAL_REQUEST — explicit channel'ы.
- ✅ Pro-deliverable audit trails: каждая evidence-row несёт полный
  chain-of-custody.
- ✅ ROI tracking enabled: Phase 22.4 dashboard агрегирует
  `provenance.cost_usd` по дереву / каналу / периоду.
- ✅ Tier-классификация data-driven: команда переоценивает tier
  одной UPDATE.
- ✅ Phase 22.1-22.4 endpoint работа разблокирована.
- ✅ 15.x consumers (court-ready report, archive planner) получают
  явный channel + cost для отображения в отчётах.

### Отрицательные

- ❌ Миграция вводит две новые таблицы и одно ограничение FK
  (evidence.document_type → document_type_weights). Update каждой
  tier-классификации — теперь UPDATE, не commit, что меняет audit
  granularity (нужен ли DDL-аудит — отдельный вопрос).
- ❌ Confidence range расширяется до [0, 3]. Существующие потребители,
  которые предполагали [0, 1], должны нормализовать (документировано
  в ORM-docstring).
- ❌ Forces API-валидаторов в Phase 22.1-22.4 повторять проверку
  ``unknown``-rejection — в schema нельзя выразить «UNKNOWN допустим
  только если migrated=true».

## Будущие эволюции

- **Phase 22.1**: Archive registry, ссылается на
  ``provenance.archive_name`` как на link-key.
- **Phase 22.4**: ROI dashboard агрегирует ``provenance.cost_usd``,
  фильтрует по ``provenance.channel``, по ``jurisdiction``.
- **Phase 22.x followup**: UI для редактирования `Provenance`
  (out of scope этого PR, см. брифа § FRONTEND).
- **Phase 16.x**: DNA-evidence scoring остаётся отдельным pipeline'ом;
  ``dna_match_*`` document_type'ы хранятся ради единого entity-индекса,
  но weight для них переопределяется DNA-кодом (см. § DocumentType
  comment в коде).
- **Future ADR**: расширение ``ProvenanceChannel`` enum'а — отдельным
  ADR. Добавление каналов, тонко описывающих чувствительные методы
  получения данных, требует отдельной этической оценки. В частности
  enum **не содержит** "bribery" или подобных формулировок и не должен
  в будущем содержать.

## Anti-drift checklist (соответствует брифу)

- ✅ ``evidence.confidence`` сохранена, recompute'ится.
- ✅ ``weight`` всегда ∈ {1,2,3}, derived из lookup, никогда NULL/free-text.
- ✅ ``provenance`` — JSONB strict-shape (Pydantic + DB CHECK).
- ✅ ``cost_usd`` nullable; отрицательные отвергаются.
- ✅ Weight derivation в БД, не в Python.
- ✅ Никаких endpoint / frontend изменений в этом PR.
- ✅ Нет каналов «bribery» или подобных.
- ✅ ``unknown`` зарезервирован для backfill (валидатор API уровня —
  обязанность Phase 22.1-22.4).
- ✅ PR < 500 LOC изменений (без миграции).

## References

- Brief: `docs/briefs/phase-22-5-evidence-provenance-split.md`
- Memory: `feature_phase_22_off_catalog_archive.md`,
  `dna_match_discovery_dashboard.md`,
  `owner_dna_cluster_map.md`
- ADR-0003 (versioning, provenance baseline)
- CLAUDE.md §3 (evidence-first, hypothesis-aware, provenance everywhere)
