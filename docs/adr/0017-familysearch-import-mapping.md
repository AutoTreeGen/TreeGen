# ADR-0017: FamilySearch Person → ORM mapping (Phase 5.1)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `integration`, `familysearch`, `mapping`, `phase-5`

## Контекст

ADR-0011 заложил клиент для FamilySearch (httpx + Pydantic v2 + GEDCOM-X
модели). Phase 5.1 — это первая *cross-platform* интеграция: пользователь
вводит свой FamilySearch person ID, и parser-service подтягивает родословную
(до N поколений предков) в локальное дерево, без выгрузки/загрузки GEDCOM.

GEDCOM-X модели FamilySearch и наш ORM (`shared-models`) **не совпадают
1:1**. Разница лежит в трёх плоскостях:

1. **Идентификаторы.** FamilySearch person id — короткий код вида `KW7S-VQJ`
   (буквы + цифры + дефисы). У нас канонический id — UUID, а GEDCOM-xref
   ожидает префикс `@I…@`. Нужна стабильная схема mapping'а, чтобы
   повторный импорт того же FS person'а попал в существующую запись, а
   не создал дубликат.
2. **Provenance.** CLAUDE.md §3.3 требует, чтобы каждая запись несла
   `provenance.source_files` (или эквивалентное поле). У нас уже есть
   `provenance` JSONB на всех TreeEntityMixins. FamilySearch для нас —
   external source, нужна согласованная JSON-схема для traceability.
3. **Conflict resolution.** CLAUDE.md §5 запрещает auto-merge persons с
   близким родством без manual review. Re-import того же FS person'а
   технически — это **тот же** person, у нас матч по `provenance.fs_person_id`,
   а не cross-person merge. Это нужно явно отделить от entity resolution.

Силы давления:

- **CLAUDE.md §3.1 (Evidence-first), §3.3 (Provenance everywhere), §5
  (запрет auto-merge).** Маппинг должен сохранять следы импорта на каждой
  записи, ничего не «склеивать» с persons других источников.
- **ADR-0009 §«DNA как special category».** FamilySearch DNA Match
  resources — partner-only; мы их в этом маппинге **не трогаем**.
- **Rate limits.** FamilySearch для personal apps ≈ 100 req/min
  (документировано через devsupport). При depth=5 у нас ≤ 31 person =
  один-два запроса (через `/ancestry?generations=N`), так что
  client-side throttling client'а (tenacity, ADR-0011) достаточно.
- **Идемпотентность.** Повторный POST `/imports/familysearch` с тем же
  `fs_person_id` должен не дублировать persons. Этот ADR определяет ключ
  идемпотентности (`provenance.fs_person_id` на Person).

## Рассмотренные варианты

### Вариант A — Mapping в `provenance.fs_person_id` + `gedcom_xref` с префиксом `fs:`

Каждая FS person ↔ одна ORM Person. `gedcom_xref` хранит
`fs:{FS_ID}` (например, `fs:KW7S-VQJ`); `provenance` несёт структурированный
блок source/fs_person_id/url/import_job_id/imported_at. Идемпотентность —
SELECT по `provenance->>'fs_person_id'`.

- ✅ Provenance — single source of truth, CLAUDE.md §3.3 соблюдён.
- ✅ `gedcom_xref` остаётся читаемым (полезно для GEDCOM-export round-trip
  по ADR-0007 — FS-импортированные persons экспортируются с понятным xref).
- ✅ Идемпотентность через JSONB-индекс по `provenance->>'fs_person_id'`
  (миграция в Phase 5.2 — пока линейный SELECT, корпус мал).
- ✅ Ноль pollution в основной схеме — никаких новых колонок, всё через
  существующие mixins.
- ❌ Линейный SELECT по JSONB при upsert (без индекса) — не масштабируется
  на десятки тысяч записей. *Mitigation:* JSONB GIN-индекс позже.

### Вариант B — Отдельная таблица `external_id_mapping`

Отдельная таблица `(platform, external_id, person_id)`. Вариант, описанный
в ADR-0009 §«Что нужно построить» п.9 — для будущего `integration_account`,
`sync_job`, `external_id_mapping` инфраструктуры.

- ✅ Чистая схема: индекс по `(platform, external_id)` сразу даёт
  идемпотентность через primary lookup.
- ✅ Готово для multi-platform: одна Person может быть mapped в FS, Geni,
  MyHeritage одновременно (разные строки в external_id_mapping).
- ❌ Phase 5.1 — единственная интеграция; добавлять таблицу для одной
  платформы = premature abstraction. ADR-0009 явно перенёс эту инфраструктуру
  в общий слой Phase 5.2+.
- ❌ Удваивает количество писем при импорте: Person row + external_id_mapping row.
- ❌ Миграция: новая таблица, FK, индексы. Не блокирует Phase 5.1, но
  делает работу больше.

### Вариант C — Расширить `Person` колонкой `fs_person_id: str | None`

Прямой колонкой на Person.

- ✅ Самое быстрое: `WHERE fs_person_id = ?` с обычным B-tree индексом.
- ❌ Pollution схемы: завтра приходит Geni — добавляем `geni_person_id`,
  потом MyHeritage, потом WikiTree. Per-platform колонки на core-таблице
  — анти-паттерн, тот самый, который ADR-0009 явно обходит через
  `external_id_mapping`.
- ❌ Миграция на каждую новую платформу.

## Решение

Выбран **Вариант A** — mapping через `provenance.fs_person_id` + `gedcom_xref`
с префиксом `fs:`.

Обоснование (4 предложения):

1. **B — premature.** Для одной платформы Phase 5.1 отдельная таблица
   — overhead. ADR-0009 явно держит эту инфраструктуру для Phase 5.2+,
   когда подключатся Geni/MyHeritage/WikiTree.
2. **C загрязняет ядро.** Per-platform колонки противоречат ADR-0009
   (выбор B/integration-layer там же).
3. **A соответствует CLAUDE.md §3.3.** Provenance — already first-class
   на всех TreeEntityMixins; добавлять структурный блок в JSONB без
   schema-миграции.
4. **Migration path к B понятен.** Когда подключим вторую платформу
   (Geni / MyHeritage), мигрируем на `external_id_mapping` через
   backfill из provenance JSONB; Phase 5.2+ — отдельный ADR.

## Mapping

### Person

| FamilySearch GEDCOM-X | ORM `Person` | Notes |
|---|---|---|
| `Person.id` (`KW7S-VQJ`) | `gedcom_xref = "fs:KW7S-VQJ"` | Префикс `fs:` отличает от GEDCOM @I-xref'ов; round-trip GEDCOM не ломаем (наш writer оборачивает в `@fs:KW7S-VQJ@` или транслит — Phase 5.2 если потребуется). |
| `Person.id` | `provenance.fs_person_id = "KW7S-VQJ"` | Канонический ключ идемпотентности. |
| `Person.gender.type` (URI `http://gedcomx.org/Male`) | `Person.sex` (`Sex.MALE = "M"` / `Sex.FEMALE = "F"` / `Sex.UNKNOWN = "U"`) | Маппер `_mapping.py` уже снимает URI префикс → `MALE/FEMALE/UNKNOWN`. |
| `Person.living = true` | `Person.status = EntityStatus.HYPOTHESIS` (если не указано иное) | Living person'ы — из FS живые потомки; их статус по умолчанию ниже, чтобы пользователь мог явно подтвердить. Default для не-living — `EntityStatus.PROBABLE`. |
| `Person.living = false / null` | `Person.status = EntityStatus.PROBABLE` | Доверяем источнику, но ниже `CONFIRMED` (impossible to derive из чужого дерева автоматически). |

### Name

| FamilySearch | ORM `Name` | Notes |
|---|---|---|
| `Person.names[].nameForms[0].fullText` | `Name.given_name` + `Name.surname` (parsed from parts) | Маппер `_mapping.py` уже выделяет given/surname из `nameForms[0].parts`. Multi-script (`nameForms[1+]`) — Phase 5.2. |
| `Person.names[].preferred = true` | `Name.sort_order = 0`, `Name.name_type = NameType.BIRTH.value` | Preferred name становится первым (sort_order=0); остальные — sort_order=1+. |
| `Person.names[].preferred = false` | `Name.sort_order = i+1`, `Name.name_type = NameType.AKA.value` | Дополнительные формы — AKA. |

### Event

GEDCOM-X facts `Birth/Death/Marriage/...` — мы пока маппим **минимум**
(birth, death). Marriage/divorce — Phase 5.2 (требует FS Relationship API,
не в `get_person`).

| FamilySearch fact | ORM `Event.event_type` |
|---|---|
| `http://gedcomx.org/Birth` | `EventType.BIRTH = "BIRT"` |
| `http://gedcomx.org/Death` | `EventType.DEATH = "DEAT"` |
| `http://gedcomx.org/Marriage` | `EventType.MARRIAGE = "MARR"` (Phase 5.2) |
| Прочие | пропускаем в Phase 5.1 (логируем в `import_job.errors[]`) |

| FS Fact field | ORM Event field |
|---|---|
| `Fact.date.original` | `Event.date_raw` |
| `Fact.place.original` | через `_resolve_place(...)` → `Event.place_id` |
| (нет — отсутствует у FS) | `Event.date_start/date_end/date_qualifier` оставляем `None` (Phase 5.2 — парсить FS dates через gedcom-parser dates lib) |

`EventParticipant.role = "principal"` для birth/death (одна персона в евенте).

### Place

`_resolve_place(place_text, tree_id, places_cache)` — lookup-or-create по
`(tree_id, canonical_name)`:

1. Если `place_text` пустой — вернуть `None`.
2. Если `places_cache[place_text]` уже есть — вернуть UUID.
3. Иначе — построить `Place` row с `canonical_name = place_text.strip()`,
   остальные поля (`country_code_iso`, `admin1`, …) — `None` (Phase 5.2 —
   гибридный нормализатор через JewishGen gazetteer / Geonames).
4. Кешировать в `places_cache`, добавить в `place_rows` для bulk insert.

Тот же паттерн уже реализован в `services/parser-service/services/import_runner.py`
(GEDCOM importer). Переиспользуем helper'ом.

### Provenance schema

На каждой FS-импортированной Person и Event:

```json
{
  "source": "familysearch",
  "fs_person_id": "KW7S-VQJ",
  "fs_url": "https://www.familysearch.org/tree/person/details/KW7S-VQJ",
  "imported_at": "2026-04-27T12:34:56Z",
  "import_job_id": "<UUID of ImportJob>"
}
```

- `source: "familysearch"` — фиксированная константа, индикатор внешнего
  происхождения (рядом с `gedcom`, `manual`, и т.п.).
- `fs_person_id` — канонический ключ для идемпотентности.
- `fs_url` — built из `fs_person_id` (детерминированно), удобно для UI
  (deeplink на FamilySearch).
- `imported_at` — UTC timestamp (CLAUDE.md §3.5 — privacy by design;
  никаких локальных таймзон в provenance).
- `import_job_id` — FK на `ImportJob.id` (как str).

На Event записи provenance копирует те же поля, чтобы каждый event можно
было отследить до конкретного FS-импорта.

### ImportJob

`ImportJob.source_kind` расширяется новым значением `FAMILYSEARCH = "familysearch"`.

- Колонка `source_kind` — `String(32)` без DB CHECK-constraint, Python-side
  enum. Добавление значения в `ImportSourceKind` — non-breaking, миграция БД
  не требуется.
- `source_filename = None` (нет файла), `source_sha256 = None`,
  `source_size_bytes = None` (FS — API, не файл).
- `stats` JSONB по завершении: `{"persons": N, "names": M, "events": K,
  "places": P, "skipped_facts": S}`.
- `errors[]` JSONB: каждая ошибка — `{"fs_person_id": "...", "reason": "..."}`.
  Не-фатальные (например, неподдерживаемый fact type) идут сюда без
  остановки импорта.

## Conflict resolution

CLAUDE.md §5: «**Автоматический merge персон с близким родством без manual
review** запрещён». ADR-0017 это **не нарушает** — описанные ниже сценарии
работают только в пределах одного и того же FS person'а.

### Сценарий 1 — повторный импорт того же `fs_person_id`

Действие: SELECT существующую Person по `provenance->>'fs_person_id'`. Если
найдена — UPDATE: refresh `provenance.imported_at`, `provenance.import_job_id`,
обновить имена/события (см. ниже). Если не найдена — INSERT.

Это не «merge persons», это **refresh** одной и той же записи из источника.

### Сценарий 2 — конфликт с GEDCOM-импортированной Person, имеющей похожее имя

Действие: ничего автоматически. Создаём новую Person (FS-импорт). Дубликаты
будут предложены entity resolution engine (Phase 7+) для **manual review**.
ADR-0015 (entity resolution suggestions, no auto-merge) — наш якорь.

### Сценарий 3 — names/events refresh

При refresh:

- **Names**: удалить все existing `Name` rows у Person'а **где
  `provenance.import_job_id` совпадает с предыдущими FS-импортами**, вставить
  свежие. Names, добавленные пользователем вручную (без FS-provenance) —
  не трогаем.
- **Events**: тот же подход — drop/insert только FS-events этого Person'а.
  Manual events сохраняются.

Селектор для удаления: `Name.provenance->>'source' = 'familysearch' AND
Name.provenance->>'fs_person_id' = ?`. То же для Event.

## Rate limiting

- FamilySearch documented limit: ≈ 100 req/min для personal apps
  (devsupport, не публичная документация). Для bulk нужно через partner-program.
- Phase 5.1: depth ≤ 10. `/ancestry?generations=N` — **один** запрос на
  всё дерево; даже при rotation на 10 поколений = 1 request.
- `tenacity` retry в client (ADR-0011) уже учитывает 429 + `Retry-After`.
- Если FS вернёт 429 — `RateLimitError` пробрасывается до endpoint'а;
  endpoint конвертит в HTTP 429 для нашего user'а (через FastAPI exception
  handler). Это позволит фронту показать «попробуйте через X секунд».

## Что **не** делается в Phase 5.1

- **Marriage/Divorce events** — нужен `/platform/tree/persons/{id}/spouses`,
  не `get_person`. Phase 5.2.
- **Multi-script names** — Phase 5.2.
- **Sources/Citations** — FS Source resources не в скоупе. Phase 5.3.
- **Multimedia** — ditto, Phase 5.3.
- **DNA** — partner-only (ADR-0009).
- **Write back to FS** (POST/PUT) — не делаем вообще в Phase 5.x.

## Последствия

**Положительные:**

- Первая cross-platform import-функция в AutoTreeGen. Без выгрузки/загрузки
  GEDCOM.
- Полноценная provenance цепочка (CLAUDE.md §3.3) — каждая Person/Name/Event
  знает свой `fs_person_id` и `import_job_id`.
- Идемпотентность по `fs_person_id` — повторный импорт обновляет, не
  дублирует.
- Reusable шаблон для следующих интеграций (Geni / MyHeritage / WikiTree).

**Отрицательные / стоимость:**

- Без JSONB GIN-индекса на `provenance->>'fs_person_id'` — линейный SELECT
  при upsert. На корпусе Phase 5.1 (≤ 31 person за импорт) приемлемо;
  индекс — Phase 5.2 (миграция).
- Marriage/divorce пока не импортируются. Пользователь увидит persons и
  birth/death, но не семейные связи. Документируем это в README parser-service
  и в UI (Phase 4.x агента 1).

**Риски:**

- **FamilySearch rotates schema.** GEDCOM-X stable, но FS-обёртка иногда
  меняется. *Mitigation:* `extra="ignore"` на Pydantic-моделях
  (ADR-0011), маппер падает на отсутствующих полях с понятной ошибкой
  через `import_job.errors[]`, не остановит остальной импорт.
- **Скрытые duplicate persons.** Один и тот же реальный человек может
  быть в нескольких FS-trees. ADR-0017 не решает entity resolution —
  это Phase 7+ (ADR-0015).
- **Provenance leak в logs.** `fs_person_id` — публичный код, не PII.
  `access_token` — секрет, в provenance **не** попадает (Phase 5.1
  endpoint логирует только `sha256(access_token)[:8]`).

## Phase 5.2 extension — merge-mode decision tree

Phase 5.1 importer всегда вставлял FS-persons как новые row'ы;
Phase 5.2.1 после INSERT'а писал `fs_dedup_attempts` для review-UI
(suggestion-flow). Phase 5.2 добавляет **третий слой**: до INSERT'а
importer вызывает `fs_pedigree_merger.resolve_fs_person(...)`, который
смотрит на entity-resolution score против локальных не-FS Person'ов и
выбирает одну из трёх стратегий (`shared_models.enums.MergeStrategy`):

| Условие                                                        | Strategy        | Эффект                                                                 |
| -------------------------------------------------------------- | --------------- | ---------------------------------------------------------------------- |
| `fs_pid` уже сматчен в дереве                                  | `SKIP`          | Person/Names/Events НЕ вставляются. Идемпотентный no-op.               |
| `score ≥ 0.9` против local                                     | `MERGE`         | Используем existing Person как target; Names/Events с FS-provenance прилетают под него; `provenance.fs_attachments[]` обновляется. |
| `0.5 ≤ score < 0.9`                                            | `CREATE_AS_NEW` + `needs_review=True` | Создаётся новый Person с FS-provenance. Attempt-row помечается для UI Phase 4.5/4.6 review. |
| `score < 0.5` или нет кандидатов                               | `CREATE_AS_NEW` | Создаётся новый Person с FS-provenance. Без флага.                     |

Каждое решение записывается в `fs_import_merge_attempts` (миграция 0014):

- `tree_id`, `import_job_id` — scope.
- `fs_pid` — внешний ID FS-персоны.
- `strategy` — финальная стратегия (`skip` / `merge` / `create_as_new`).
- `matched_person_id` — local Person, на который приземлилось решение
  (NULL для CREATE_AS_NEW без близкого кандидата).
- `score`, `score_components` — composite score scorer'а + breakdown
  (для UI explainability).
- `needs_review` — bool-флаг для mid-confidence коридора.
- `reason` — короткий label (`fs_pid_idempotent`, `high_confidence_match`,
  `mid_confidence_review`, `low_confidence`, `no_candidates`).

Это **immutable audit-log**, не review-queue. State-машины
(rejected/merged) нет: следующее принятое решение по тому же `fs_pid`
породит новую row, что и есть нужная семантика для cross-import audit'а.

**Почему MERGE не нарушает CLAUDE.md §5 (запрет auto-merge persons).**
В Phase 4.6 Person-merge — это операция «склеить две существующих
local-Person row в одну», которая мутирует дерево необратимо без
явного user-confirm'а. Phase 5.2 MERGE — это «прицепить FS-evidence
к local-Person'у»: исходный local Person сохраняет identity и primary
name, FS-данные идут как AKA-имена и BIRT/DEAT-events с явным
`provenance.source: 'familysearch'`. Никакая local row не
уничтожается, никакой cross-person граф не сливается. Ровно эту
семантику CLAUDE.md §5 разрешает (см. Phase 5.0/5.1 — провенанс — это
свободно).

**Endpoint-семантика.** `POST /imports/familysearch` принимает
`target_tree_id: UUID | None`. Если задан — merge-mode (resolver
вызывается per-person). Если None — importer создаёт новое дерево
с именем `FamilySearch import {fs_person_id}` и работает в no-merge
режиме (CREATE_AS_NEW для всех). Async-flow
`POST /imports/familysearch/import` всегда merge-mode (там tree_id —
обязательный, и tree должен существовать).

## Когда пересмотреть

- **Подключение второй платформы** (Geni / MyHeritage / WikiTree) — мигрируем
  на Вариант B (`external_id_mapping`). Phase 5.2+ ADR.
- **Bulk-import или sync-orchestration** через `arq` (ADR-0009 §«Что
  нужно построить» п.6) — SLA меняется, нужен dead-letter queue.
- **FS Match API становится доступен** (партнёр-программа открывается
  developer-program members) — расширяем mapping на DNA-сегменты, см.
  ADR-0009 «когда пересмотреть».
- **Marriage/Divorce/Sources/Multimedia** добавляются в скоуп — расширяем
  таблицу маппинга, возможно — отдельный ADR.

## Ссылки

- Связанные ADR:
  - [ADR-0011](./0011-familysearch-client-design.md) — FS client design
  - [ADR-0009](./0009-genealogy-integration-strategy.md) — Phase 5
    integration strategy (Tier 1 + future external_id_mapping)
  - [ADR-0007](./0007-gedcom-555-as-canonical.md) — GEDCOM canonical;
    `fs:`-prefixed xref остаётся round-trip-совместимым
  - [ADR-0015](./0015-entity-resolution-suggestions.md) — no auto-merge
    of persons; FS-import тоже не нарушает
  - [ADR-0003](./0003-versioning-strategy.md) — soft delete + provenance
- External:
  - [FamilySearch Tree API](https://developers.familysearch.org/docs/api/tree)
  - [GEDCOM-X Person](https://developers.familysearch.org/docs/api/types/json/Person)
  - [GEDCOM-X Fact](https://developers.familysearch.org/docs/api/types/json/Fact)
- Architecture: CLAUDE.md §3 (Evidence-first, Hypothesis-aware, Provenance,
  Privacy by design).
