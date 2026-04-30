# ADR-0062: GEDCOM unknown-tag quarantine + AST round-trip (Phase 5.5a)

- **Status:** Accepted
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `gedcom`, `parser`, `round-trip`, `provenance`, `phase-5.5`

## Контекст

Phase 1 (`packages/gedcom-parser/`) даёт двух-уровневый парсер:

- **AST** (`GedcomLine` / `GedcomRecord`) — лексер + parser. **Сохраняет
  всё** что было в файле: каждый тег, value, поддерево.
- **Семантический слой** (`Person`, `Family`, `Source`, `Event`,
  `Citation`, `Note`, `MultimediaObject`, `Repository`, `Submitter`) —
  типизированные Pydantic-модели, в которые сворачиваются `GedcomRecord`
  через `from_record(record)` factories. Каждая фабрика whitelist-driven:
  забирает только known sub-tags, всё остальное молча игнорирует.

Семантический слой — это то, что персистится в Postgres ORM
(`packages/shared-models/src/shared_models/orm/{person,family,source,...}.py`).
То есть на пути **GED → AST → entity → DB → entity → AST → GED'**
проприетарные / нестандартные теги дропаются. Они выживают только при
прямом AST-уровне round-trip'е (`parse_text → write_records`).

Cross-platform GEDCOM-обмен — international #1 pain ботом research
wave 2:

- **Ancestry** добавляет `_FSFTID`, `_PRIM`, `_TYPE`, `_APID`, `_CRE`.
- **MyHeritage** добавляет `_UID`, `_RIN`, `_PARENTRIN`.
- **Geni** добавляет `_PUBLIC`, `_LIVING`, `_FA1`.
- **Family Tree Maker** добавляет `_FREL`, `_MREL`, `_MILT`.
- Witnesses / godparents часто кладут в нестандартное место (например,
  `2 _WITN` вместо `2 ASSO`).

Ни один из этих тегов сегодня не сохраняется на пути через DB. Phase
5.5 закрывает эту дыру в два этапа:

- **5.5a (этот ADR):** quarantine на import + AST round-trip.
- **5.5b:** loss simulator + validators + endpoints.

## Рассмотренные варианты

### A. Где хранить unknown_tags

#### A1. Поле на каждой entity-модели (`Person.unknown_tags`, `Family.unknown_tags`, …) — отвергнуто

- ✅ Естественно: тег принадлежит entity, и поле живёт там же.
- ❌ Прямая модификация existing parsed entity model. CLAUDE.md §3
  «Provenance everywhere» позволяет, но user spec'ом 5.5a явно
  запрещает: «не модифицируй existing parsed entity model».
- ❌ ORM-зеркало требовало бы модификации каждого entity-table'а:
  `persons.unknown_tags`, `families.unknown_tags`, `sources.unknown_tags`,
  и т.д. — 8+ alembic-миграций минимум.

#### A2. Один список на `GedcomDocument` + один jsonb на `import_jobs` (выбрано)

- ✅ Минимальное вмешательство: одна новая Pydantic-модель
  (`RawTagBlock`), одно поле на `GedcomDocument`, одна alembic-миграция
  на одну колонку. Existing entity-модели не трогаем.
- ✅ Симметрия с Phase 10.2 `source_extractions.raw_response` — той же
  «raw blob, decoded later» идиомы.
- ✅ Single source of truth: вся информация для re-injection лежит в
  одном месте (jsonb на import-row).
- ❌ Чтобы найти unknown_tags для конкретной персоны, нужно отфильтровать
  список по `owner_xref_id`. Acceptable: list мал (типичный GED — десятки
  блоков; corpus stress-test — сотни, не миллионы).

#### A3. Новая таблица `import_unknown_tags` со ссылкой на `import_jobs.id` — отвергнуто

- ✅ Структурированный SQL-доступ, индексы по owner_kind / owner_xref.
- ❌ Овердизайн. На 5.5a / 5.5b нет запросов «найди все импорты с тегом
  X» — единственный consumer'ов unknown_tags будет export builder,
  который читает их целиком за один SELECT.
- ❌ Ещё одна таблица в `SERVICE_TABLES` allowlist + миграция + ORM
  модель + tests. ~200 LOC, не оправданы.

### B. Глубина quarantine

#### B1. Только direct-children top-level record'ов (выбрано)

Quarantine сканирует только `record.children` корневого INDI/FAM/SOUR/...,
не залезая внутрь известного child'а (например, внутрь `BIRT`).

- ✅ Простая семантика: «теги, которые `Person.from_record` не consumed
  явно».
- ✅ Покрывает доминирующий класс case'ов (Ancestry `_FSFTID` /
  MyHeritage `_UID` / Geni `_PUBLIC` всегда сидят на 1-уровне).
- ❌ Глубже-вложенные проприетарные теги (например, `2 _PRIM Y` внутри
  `1 BIRT`) на 5.5a игнорируются — они часть subtree known-event'а,
  и весь event не сканируется. Документировано как known limitation.
- ❌ Pre-existing event-level proprietary fields (Ancestry `_PRIM` под
  `BIRT`) теряются на 5.5a путь. Mitigation: 5.5b расширит quarantine
  до per-event whitelist'а.

#### B2. Полный walk по AST с диффом против семантической extraction'ы — отвергнуто

- ✅ Поймал бы 100% loss'а.
- ❌ Требует «обратный extractor» — сравнение всех children records'а
  с тем, что фактически попало в `Person`/`Event`/etc. Это как раз и
  есть entity → record reverse-конвертер, который мы откладываем на 5.5b.
- ❌ Вычислительно дороже: O(всех узлов AST) вместо O(прямых children'ов).

### C. Round-trip semantics в тестах

#### C1. Variant B — structural diff после re-parse (выбрано)

```python
records = parse_text(original)
quarantine = quarantine_document(records)
# ... mutate (strip → inject) ...
records2 = parse_text(write_records(rebuilt))
assert quarantine_document(records2) == quarantine
```

- ✅ Robust к ANSEL→UTF-8 / UTF-16→UTF-8 normalization (real corpus
  включает ANSEL Ancestry-экспорты, byte-diff неизбежно).
- ✅ Robust к нашей собственной CONC→single-line collapse (writer не
  восстанавливает оригинальный CONC split).
- ✅ Robust к whitespace/line-ending normalisation.

#### C2. Variant A — byte-for-byte diff (только whitespace + line endings) — отвергнуто

- ✅ Самый строгий контракт.
- ❌ Невозможно: ANSEL-байты после `decode_gedcom` навсегда становятся
  Unicode и обратно не конвертируются (мы пишем UTF-8). Каждый Ancestry
  фикстур моментально проваливал бы тест.
- ❌ CONC split lost by parser; восстановить не можем.

### D. Round-trip writer для `GedcomDocument`

Полный `write_document(doc)` требует **entity → record reverse-конвертер**,
которого пока нет. На 5.5a этот writer — stub, бросающий
`NotImplementedError`. Вместо него:

- `inject_unknown_tags(records, blocks)` — helper для re-injection
  (на тестах работает на «strip + inject»-симуляции DB round-trip).
- Полный converter — Phase 5.5b, как часть LossSimulator'а (он строит
  exporter под target dialect).

## Решение

Выбраны: **A2 (single jsonb на import_jobs)**, **B1 (direct-children
only)**, **C1 (structural diff)**, **D (stub + helper)**.

### Реализация

- `gedcom_parser.models.RawTagBlock` — новая Pydantic-модель
  `(owner_xref_id, owner_kind, path, record)`.
- `GedcomDocument.unknown_tags: tuple[RawTagBlock, ...]` — populated в
  `from_records` через `quarantine.quarantine_document(records)`.
- `gedcom_parser.quarantine` — whitelist-driven: `KNOWN_INDI_TAGS`,
  `KNOWN_FAM_TAGS`, `KNOWN_SOUR_TAGS`, и т.д. Зеркалят
  `Person.from_record` / `Family.from_record` / ... contracts.
- `gedcom_parser.writer.inject_unknown_tags(records, blocks)` —
  re-injects direct-children в record'ы с матчинг'ом по
  `owner_xref_id`. `path != ""` пока не поддерживается.
- `shared_models.orm.ImportJob.unknown_tags: JSONB` — defaults to
  `[]`. Alembic 0028 add-column миграция.
- `parser_service.services.import_runner.run_import` —
  после `GedcomDocument.from_records` сериализует
  `document.unknown_tags` через `model_dump(mode="json")` и пишет в
  `job.unknown_tags`.

## Последствия

### Положительные

- **Round-trip без потерь на AST уровне** — `parse → quarantine →
  inject → write` cycle покрывает 100% direct-children проприетарных
  тегов.
- **Persistence**: каждый импорт сохраняет свои unknown_tags в
  `import_jobs.unknown_tags`. Phase 5.5b export builder возьмёт их
  оттуда без модификации import-runner.
- **Zero impact на семантику**: existing entity models (`Person`,
  `Family`, `Source`, …) не изменены; ни один существующий тест не
  упал.

### Отрицательные / стоимость

- **Глубже-вложенные unknown_tags** (внутри known event'ов) на 5.5a
  не quarantine'ятся. Реальный пример: Ancestry в `BIRT` кладёт
  `_PRIM Y` как маркер «primary event». Это поле теряется на DB-цикле.
  Mitigation: 5.5b расширит до per-event whitelist'а.
- **Размер jsonb**: на типичном GED'е (5-50k персон) unknown_tags
  растёт до ~1-10 KB; на стресс-фиктурах (Ancestry export 150 МБ) —
  до 100-500 KB. Acceptable: jsonb компрессируется TOAST'ом, query
  time не страдает (читаем целиком только при export).
- **Schema drift risk**: если в `Person.from_record` появится новый
  consumed sub-tag (например, addition `BURI` rules в FUTURE), то
  забыв добавить его в `KNOWN_INDI_TAGS`, мы начнём quarantine'ить
  тег который parser теперь умеет. Mitigation:
  `test_known_indi_tags_includes_event_tags` smoke test в
  `test_quarantine.py` проверяет основной набор. Полный invariant
  (whitelist == consumed set) — TODO Phase 5.5b.

### Риски

- **Path != "" блоки** в jsonb (если 5.5b начнёт quarantine'ить
  глубже): на 5.5a `inject_unknown_tags` их silently игнорирует. Как
  только 5.5b начнёт писать такие блоки, нужно будет в эту функцию
  добавить path-walking логику. Документировано в `inject_unknown_tags`
  docstring'е.
- **Custom 0-level records** (например, `0 @P1@ _PROP` от самописной
  утилиты) сохраняем целиком как `owner_kind="custom"`. Это safety-net,
  но если их много, jsonb может разрастись.

### Что нужно сделать в коде

- Эта фаза: реализовано (см. §«Реализация»).
- **Phase 5.5b** (зависит от merge 5.5a):
  - `target_dialects.py` — `TargetDialect` enum + per-dialect support
    matrix.
  - `loss_simulator.py` — на основе `unknown_tags` + dialect matrix
    рапортит, что **будет** потеряно при export'е.
  - `validator.py` — structural (broken refs / orphans) +
    semantic (impossible dates / child-before-mother).
  - Endpoints `POST /api/v1/gedcom/simulate-export` и
    `POST /api/v1/gedcom/validate` (первый `/api/v1/` namespace в
    parser-service).
  - Entity → record reverse-конвертер для `write_document`.

## Когда пересмотреть

- **Если на real corpus стресс-тесте jsonb превышает 1 МБ** на одну
  import_job-row → пересмотреть на отдельную таблицу (вариант A3).
- **Если кто-то начнёт массово терять proprietary поля внутри
  known events** (например, Ancestry export `_PRIM` критичен для
  пользователя) → ускорить 5.5b с per-event whitelist'ом.
- **Если 5.5b LossSimulator потребует индексировать unknown_tags
  по owner_kind** (например, «найди все импорты с MyHeritage `_UID`
  тегом») → добавить generated column + GIN-index.

## Ссылки

- Связанные ADR: ADR-0003 (versioning + provenance), ADR-0007 (GEDCOM
  5.5.5 как канонический формат), ADR-0059 (AI source extraction —
  pattern для jsonb-on-extraction-row); ADR-0061 (онбординг тур —
  занят соседним номером, не по теме).
- CLAUDE.md §11 — работа с GEDCOM.
- ROADMAP §5.5 — split на 5.5a + 5.5b.
- Spec файл: `.agent-tasks/07-phase-5-5-gedcom-safe-io.md`.
- GEDCOM proprietary extensions: `docs/gedcom-extensions.md`.
