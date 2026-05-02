# ADR-0072: DNA match-list ingest (multi-platform CSV)

- **Status:** Proposed
- **Date:** 2026-05-02
- **Authors:** @autotreegen
- **Tags:** `dna`, `data-ingest`, `phase-16`

## Контекст

Phase 16 — DNA Pro Tools — открывает poll-серьёзную работу с match-данными
(люди, у которых есть совпадение ДНК с владельцем). Match-data —
*aggregate-уровень*, а не raw genotype: total cM, longest segment cM,
shared segments count, predicted relationship, shared matches list.

Каждая из 5 целевых платформ (Ancestry, 23andMe, MyHeritage, FTDNA,
GEDmatch) даёт CSV-экспорт match-list'а. Платформы не выдают raw API
для bulk-pull — поэтому единственный legitimate способ получить
match-data — *user uploads CSV manually*. Scraping запрещён в
CLAUDE.md §5 и в брифе Phase 16.

Phase 16.1 (Agent 3) занимается *raw genotype* парсерами; Phase 16.3
(этот ADR) — match-list'ом, *аггрегатным* slice'ом данных.

Phase 16.5 (cross-platform identity resolver) — будущая фаза:
зачищает дубликаты «Alice S.» из Ancestry и «Alice Smith» из MyHeritage
в единого Person. Этот ADR подготавливает schema (resolution_confidence,
denormalized platform колонка), но resolver-логика out of scope.

## Рассмотренные варианты

### Вариант 1 — Один общий парсер с auto-detect

- ✅ Один entrypoint, не нужно явно указывать платформу.
- ❌ Auto-detect между 5 разными форматами с пересекающимися
  колонками («Total cM» одинаковое везде) — fragile heuristic.
- ❌ Каждый export evolves независимо; общий парсер усугубит drift.

REJECTED — слишком хрупко.

### Вариант 2 — Per-platform парсеры + dispatcher (выбран)

- ✅ Per-platform isolation — ровно одно место для column aliases
  каждой платформы; rename-friendly.
- ✅ Pure-function парсеры; легко тестируются с synthetic CSV (5 строк
  unit-fixture per platform).
- ✅ Dispatcher (1 функция) маршрутизирует bytes/str → парсер по
  явному ``DnaPlatform`` параметру (caller обязан знать, что
  заливает).
- ❌ User должен явно указать платформу в API. Acceptable: CSV
  uploaders уже знают, откуда экспорт.

CHOSEN — стандартный pattern в repo (gedcom-parser, dna-analysis/parsers
для raw genotype делают то же).

### Вариант 3 — Новый пакет `packages/dna-parser`

- Phase 16.3 brief вначале предлагал новый пакет — потенциально для
  совместного с 16.1 использования. Но `packages/dna-analysis` уже
  существует и содержит raw-genotype parsers; добавить ещё один
  parallel-package — ненужная фрагментация.

REJECTED — расширяем `packages/dna-analysis/match_list/`. Если 16.1
(Agent 3) решит наоборот выделить в `dna-parser`, отдельный refactor
PR проведёт миграцию обоих slice'ов.

## Решение

### 1. Module placement

`packages/dna-analysis/src/dna_analysis/match_list/`:

- `models.py`           — `MatchListEntry` Pydantic frozen-модель.
- `relationship.py`     — `normalise_relationship(raw: str)` →
  `PredictedRelationship`.
- `_csv_utils.py`       — encoding-fallback (UTF-8 BOM, Windows-1252),
  decimal-comma, optional-int/float helpers.
- `ancestry.py`,
  `twentythree_and_me.py`,
  `myheritage.py`,
  `ftdna.py`,
  `gedmatch.py`         — per-platform парсеры.
- `dispatcher.py`       — `parse_match_list(source, platform)`.

### 2. ORM extension (additive)

`shared_models.orm.DnaMatch` уже существует с Phase 6.x. Phase 16.3
добавляет колонки:

- `platform: str | None`                          — denormalized из
  `dna_kits.source_platform` для прямой фильтрации (`GET
  /dna/matches?platform=`).
- `match_username: str | None`                    — отдельный username
  (23andMe).
- `predicted_relationship_normalized: str | None` — bucket из
  `PredictedRelationship` enum рядом с raw-text.
- `resolution_confidence: float | None`           — для 16.5.
- `raw_payload: JSONB NOT NULL DEFAULT '{}'`      — полная CSV-row
  для re-parse.

Existing колонки (`predicted_relationship` text, `confidence` text,
`matched_person_id`) сохранены как есть — backward-compat. Новый
`predicted_relationship_normalized` индексирован для 16.5
cross-platform aggregation.

### 3. Endpoints

`services/dna-service`:

- `POST /dna/match-list/import` — multipart upload (`kit_id`,
  `platform`, `file`). Idempotent upsert по `(kit_id,
  external_match_id)`. Не затирает `matched_person_id` /
  `resolution_confidence` (user-judgement / 16.5-state).
- `GET /dna/matches` — list view с фильтрами `kit_id`, `platform`,
  `min_cm`, `max_cm`. Sort: `total_cm DESC NULLS LAST`.
- `DELETE /dna/matches?kit_id=&platform=` — bulk delete для
  re-import flow.

Existing Phase 6.3 endpoints (`GET /dna-kits/{id}/matches`,
`PATCH /dna-matches/{id}/link`) сохранены — два набора покрывают разные
сценарии (kit-centric vs. cross-kit query).

### 4. Schema invariants

`dna_matches` остаётся в `TREE_ENTITY_TABLES` — у неё уже есть
`tree_id`, `provenance`, `version_id`, `deleted_at`. Phase 16.3
не меняет invariant.

Brief предлагал лёгкий-вариант service-table; отклонено: переезд из
TREE_ENTITY в SERVICE сломает существующий audit-listener и
GDPR-erasure — слишком большой риск ради чисто эстетического
переименования.

### 5. raw_payload preservation

JSONB-колонка хранит полную CSV-row dict, ключи нормализованы к str.
Anti-drift: если completeness-парсера эволюционирует (платформа
переименует колонку), можно переразобрать raw_payload без
повторного скачивания экспорта пользователем.

### 6. Predicted-relationship normalisation

Платформенные строки (`«3rd Cousin»`, `«1st—2nd cousin»`,
`«Distant Cousin»`, `«Brother»`) → один из 9 bucket'ов
`PredictedRelationship`. Маппинг — regex-pattern порядка важен
(half-sibling до full-sibling). UNKNOWN — fallback, не silent
DISTANT.

GEDmatch не выдаёт relationship-string в Tier-1 экспорте; для него
маппим из `Gen` (estimated generation distance).

## Anti-drift checklist (per Phase 16.3 brief)

- ✅ No scraping. CSV-only ingestion.
- ✅ No cross-platform identity resolution (это Phase 16.5).
- ✅ No own relationship prediction. Trust platform.
- ✅ raw_payload preserved as JSONB.
- ✅ Не bundling segment ingest (это Phase 16.4).
- ✅ Backwards-compat: existing endpoints/columns не изменены.

## Последствия

### Положительные

- ✅ 5 платформ parsable одним dispatcher'ом.
- ✅ Owner ('s AJ-endogamous DNA) корпус (Ancestry +
  23andMe + MyHeritage exports) импортируется без потерь.
- ✅ Phase 16.4 (segments) и 16.5 (cross-platform resolver) могут
  building поверх стабильного match-list-baseline.
- ✅ ROI tracking: Phase 22.4 dashboard сможет агрегировать «cM по
  платформам».

### Отрицательные

- ❌ 5 per-platform парсеров — поддержка breaks при column-rename
  любой платформой. Mитigation: каждый парсер хранит alias-tuples;
  добавить новый алиас — 1 строка.
- ❌ raw_payload JSONB удваивает storage cost для match-rows
  (каждая дублирует свою CSV-row). Acceptable: match-list — это
  десятки тысяч row'ов на kit, не миллионы; cost небольшой.
- ❌ `platform` денормализован — required чтобы не делать join
  `dna_matches → dna_kits` на каждый list-request, но добавляет
  consistency-обязательство импортного pipeline'а.

## Будущие эволюции

- **Phase 16.4** — segment ingest (chromosome-level matches).
- **Phase 16.5** — cross-platform identity resolver: соединит
  «Alice S.» из Ancestry с «Alice Smith» из MyHeritage в единый
  Person. Использует `resolution_confidence`.
- **Phase 16.7** — endogamy correction (multipliers из
  `EthnicityPopulation` — owner AJ-endogamous кейс уже dogfood'ится).

## References

- Brief: `docs/briefs/phase-16-3-match-list.md`
- Memory: `feature_phase_16_dna_pro_tools.md`,
  `dna_platform_export_capabilities.md`,
  `owner_dna_cluster_map.md`
- ADR-0020 (DNA consent + storage privacy).
- ADR-0033 (Phase 6.3 match listing endpoints).
