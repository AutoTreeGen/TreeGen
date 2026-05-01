# ADR-0068: Multilingual Name Engine (Phase 15.10)

- **Status:** Accepted
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `entity-resolution`, `names`, `transliteration`, `phonetic`, `i18n`, `aj`, `slavic`

## Контекст

ICP AutoTreeGen — Eastern European Jewish + Slavic genealogy. Имена в этом
домене существуют одновременно в нескольких script'ах и orthographies:

- **Levitin** (English passport / Ellis Island manifest);
- **Левитин** (Russian-empire vital records, post-1918 Soviet);
- **לויטין** (Hebrew tombstone / yizkor book / KKL records);
- **Lewitin** (German pre-WWII records, also Polish romanization);
- **Levitan / Leviton** — common spelling drift в US naturalisation papers.

Для archive search, GEDCOM safe-merge, AI Identity Resolver и Voice-to-Tree
append-mode fuzzy match все эти формы должны matching как один человек.
Без этого слоя:

- `entity_resolution.persons.person_match_score` сравнивает только Latin
  spelling + Daitch-Mokotoff phonetic; cross-script (Cyrillic ↔ Hebrew ↔
  Latin) сходство не ловится — false negatives на 30–50% AJ-имён.
- Pre-existing 10.3 AI normalization Claude-driven, дорогой для каждого
  lookup'а; нужен deterministic, instant, predictable layer ниже.
- Polish surname endings (-ski / -cki / -icz) и German umlauts (ä↔ae) тоже
  не покрыты — false negatives на ashkenazi-Polish и Yekkes branches.

Patronymic parsing (Иван **Иванович** Петров) — отдельный, но смежный
вопрос: GEDCOM-stream'ы из Eastern European archives не разделяют
given / patronymic / surname в отдельные fields, всё лежит в одном
NAME-token'е.

## Рассмотренные варианты

### Вариант A — расширить существующий `entity_resolution.phonetic` + `string_matching`

Добавить функции `to_latin_bgn(text)` / `to_cyrillic(text)` / `restore_diacritics(text)` рядом с `daitch_mokotoff` в `phonetic.py`,
а `string_matching.py` дополнить cross-script-aware ratio.

- ✅ Один-pacckage scope, нет нового sub-folder'а.
- ❌ `phonetic.py` смешивает Soundex / DM / transliteration — теряет
  cohesion (модуль становится «namesutil»).
- ❌ Backward-compat риск: callers `daitch_mokotoff` не ожидают, что
  модуль может теперь импортить `transliterate` lib.

### Вариант B — новый `names/` sub-package в entity-resolution

Шесть focused-модулей под одним namespace'ом:

- `patronymic.py`, `transliterate.py`, `daitch_mokotoff.py` (thin re-export),
  `synonyms.py`, `variants.py`, `match.py`
- `data/icp_anchor_synonyms.json` — curated reverse-index.

Backward-compat: existing `phonetic.py` / `persons.py` / `string_matching.py`
не трогаются. Будущий PR опционально мигрирует `persons.person_match_score`
на `NameMatcher`.

- ✅ Cohesive (один subpackage = одна задача = «multilingual names»).
- ✅ Backward-compat trivially: ничего не редактируется, существующие
  тесты unchanged.
- ✅ Re-export DM из `phonetic.py` (без дублирования логики) — single
  source of truth остаётся ADR-0015.
- ❌ Чуть больше файлов; добавляет 2 dep'а в pyproject (`transliterate`,
  `unidecode`).

### Вариант C — отдельный package `multilingual-names`

Полностью независимый package (как `gedcom-parser`).

- ✅ Самостоятельные релизные циклы.
- ❌ Overkill для V1 (один потребитель — entity-resolution).
- ❌ Cross-package coordination для DM re-export.

## Решение

Выбран **Вариант B** (`names/` sub-package в entity-resolution). Cohesion

- backward-compat без риска регрессий — главные критерии. Cost этого
выбора (два новых dep'а, ~700 LOC новой логики) — обоснован тем, что
без слоя AJ-/Slavic-search в archive integrations 9.0+ будет fundamentally
broken.

**Ключевые design points:**

1. **DM re-export, не реимплементация.** `names/daitch_mokotoff.py` —
   3 строки: `from entity_resolution.phonetic import daitch_mokotoff as
   dm_soundex`. Canonical impl остаётся в phonetic.py per ADR-0015;
   улучшения DM-таблицы делаются ровно в одном месте.

2. **Reason-attribution на match'е.** `MatchResult.reason ∈ {exact /
   variant_diacritic / variant_synonym / variant_transliteration /
   dm_phonetic / fuzzy}` — UI и audit-логи могут показать «почему этот
   candidate матчится». Exact > diacritic (0.92) > synonym (0.88) >
   transliteration (0.85) > DM (0.75) > fuzzy ([0.60, 0.80] clamped).

3. **Cross-script equality НЕ exact.** `Levitin` ≡ `Левитин` под
   `unidecode` fold, но reason'ом считается `variant_synonym` или
   `variant_transliteration`, не `exact`. Audit-trail про
   "почему сматчилось" точнее.

4. **ICP-anchor synonyms — curated, не auto-generated.** Phase 15.10 V1
   — 32 anchor-группы (Levitin / Cohen / Katz / Friedman / Baron + 27
   ещё), каждая с 5–10 cross-script вариантами. JSON версионирован
   (`_meta` block); расширяется по мере owner-domain research'а. AI-
   normalization (Phase 10.3) дополняет, не заменяет — для названий
   вне anchor-table.

5. **Three-flag backward-compat.** `NameMatcher(use_variants=False,
   use_phonetic=False, use_synonyms=False)` сводится к `exact + fuzzy`,
   идентичный паттерн прямого `levenshtein_ratio`-вызова. Callers,
   ещё не мигрировавшие, могут опционально использовать NameMatcher
   с этими флагами без поведенческого drift'а.

6. **Deferred: inference-service endpoints.** `POST /api/v1/names/expand`
   и `POST /api/v1/names/match` отложены в follow-up PR — стабилизируем
   `names` API сначала, потом обернём.

## Последствия

**Положительные:**

- Archive search в Phase 9 (FamilySearch / Wikimedia / JewishGen) сможет
  принимать любую форму имени и находить cross-script matches.
- 10.7 AI Tree Context Pack Identity Resolver получает deterministic
  baseline — Claude вызывается только если name-matcher не уверен.
- 10.9c Voice-to-Tree append-mode сможет fuzzy-match'ить «Анна
  Иванова» с already-imported «Anna Ivanova».
- 5.7 GEDCOM Safe Merge ловит cross-platform spellings (Ancestry /
  MyHeritage / Geni часто экспортируют разные формы той же персоны).

**Отрицательные / стоимость:**

- Два новых deps в `entity-resolution` (`transliterate>=1.10`,
  `unidecode>=1.3`) — оба маленькие, pure-Python, no native compile.
- Curated synonym-table надо поддерживать (anchor lineages добавляются
  по мере research'а; misspelled entries — single point of failure для
  конкретной фамилии).
- Yiddish lexicon — V1 не реализован (`to_hebrew(source_script='yiddish')`
  fall-back'ом на Latin-rules); добавится в Phase 10.9.x.

**Риски:**

- ICP-anchor JSON может вырасти до тысяч записей — текущий dict-loader
  загрузит всё в memory. Для V1 acceptable (≤ 10 KB файл); для V2 —
  миграция на SQLite или pgvector index.
- Transliteration accuracy — best-effort. Архивы используют разные
  romanization standards в разные эпохи; матчинг всё равно полагается
  на DM phonetic как safety-net.
- ``unidecode`` для Cyrillic производит «generic Latin», не BGN — поэтому
  ``canonical_form`` намеренно ≠ ``to_latin(standard='bgn')``. Reviewer'ам
  следить, что эти две функции не путаются.

**Что нужно сделать в коде:**

- Создать `packages/entity-resolution/src/entity_resolution/names/` с 6
  модулями + JSON.
- Добавить deps в `packages/entity-resolution/pyproject.toml`.
- Тесты: `packages/entity-resolution/tests/names/` — ≥80% coverage,
  AJ / Slavic dogfood block.
- ROADMAP §18A.10 — статус Phase 15.10.
- Existing `entity_resolution.phonetic` / `string_matching` / `persons.py`
  — **не трогать** (regression guard через unchanged тесты).

## Когда пересмотреть

- Anchor JSON > 5 000 записей → migrate to indexed store.
- Появятся другие AJ-/Slavic-domain platforms с consume'ом этого API
  (например, отдельный mobile-flow) → возможно extract в `multilingual-
  names` package.
- Yiddish corpus станет приоритетом → отдельный ADR на Yiddish lexicon
  - transliterate.
- Если `persons.person_match_score` мигрирует на NameMatcher — pop'нуть
  это deferred-item из §18A.10 «Не входит в первый PR».

## Ссылки

- ADR-0015 — Daitch-Mokotoff Soundex (canonical impl, не дублируется здесь).
- ADR-0058 — Phase 15.1 evidence panel (consumer name-engine'а в
  archive-search UI).
- JewishGen Daitch-Mokotoff spec: <https://www.jewishgen.org/InfoFiles/soundex.html>
- BGN/PCGN romanization: <https://en.wikipedia.org/wiki/BGN/PCGN_romanization_of_Russian>
- LoC romanization tables: <https://www.loc.gov/catdir/cpso/romanization/>
- ISO 9 (Cyrillic → Latin): <https://en.wikipedia.org/wiki/ISO_9>
