# Agent brief — Phase 3.4: entity resolution (dedup suggestions)

> **Кому:** Агент 2 (Claude Code CLI, bypass on) — продолжение после Phase 3.3.
> **Контекст:** Windows, `D:\Projects\TreeGen`, default branch `main`.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

После Phase 3.3 у тебя в БД полный набор сущностей: persons, names,
families, events, event_participants, places, sources, citations, multimedia.

При import одного GEDCOM в один tree всё OK. Но реальный workflow:

- User импортирует **GED от Ancestry** + **GED от MyHeritage** + **свой GED**
  → одни и те же люди / места / источники появляются дважды-трижды
- Транслитерация фамилий: `Zhitnitzky` / `Zhytnicki` / `Жytницкий` → один человек
- Опечатки в местах: `Slonim` vs `Slonim, Grodno` vs `Slonim, Belarus`
- Разные xref для одного source: `@S1@ TITL Lubelskie parish` и
  `@S15@ TITL Lubelskie Parish 1838` → возможно одно

Phase 3.4 — **entity resolution: предложить дедуп, НЕ выполнять автоматически**.

**КРИТИЧНО — CLAUDE.md §5 запрет:**
> ❌ Автоматический merge персон с близким родством без manual review.

Поэтому Phase 3.4 — **только suggestions** (с confidence score + evidence).
Сам merge — Phase 4.5 через UI с manual approval.

**Параллельно работают:**

- Агент 1: `apps/web/` (Phase 4.3 tree viz) + `services/parser-service/api/trees.py`
  (новый ancestors endpoint)
- Агент 3: `packages/familysearch-client/`
- Агент 4: `packages/dna-analysis/` (Phase 6.1 DNA matching)

**Твоя территория:**

- `packages/entity-resolution/` — **новый пакет** (или существующий, проверь)
- `services/parser-service/src/parser_service/services/dedup_finder.py` — новый
- `services/parser-service/src/parser_service/api/dedup.py` — новый router
- `services/parser-service/src/parser_service/schemas.py` — добавить
  `DuplicateSuggestion` (НЕ конфликтует с Агентом 1, они разные классы)
- `services/parser-service/src/parser_service/main.py` — register new router
  (потенциальный merge conflict с Агентом 1, минимальный)
- `services/parser-service/tests/test_dedup_*.py` — новые
- `docs/adr/0015-entity-resolution.md` — новый ADR
- `ROADMAP.md` §7.0 (если есть)

**Что НЕ трогай:**

- `apps/web/` (Агент 1)
- `packages/familysearch-client/` (Агент 3)
- `packages/dna-analysis/` (Агент 4)
- `services/parser-service/services/import_runner.py` (твой past code, но
  стабильный, не рефактори без необходимости)
- `services/parser-service/api/trees.py` (Агент 1 правит — добавляет
  ancestors endpoint). Если нужно править — координируй через rebase.

---

## Цель Phase 3.4

1. **Source dedup** — fuzzy match по (normalized_title + author + abbreviation)
2. **Place dedup** — fuzzy match по normalized_name
3. **Person match candidates** — Soundex + Daitch-Mokotoff (для евр.
   фамилий!) + birth/death year ±2 + birth place fuzzy
4. **API:** `GET /trees/{id}/duplicate-suggestions?entity_type=...`
   возвращает paginated list пар с confidence score
5. **Алгоритмы — pure functions** в `packages/entity-resolution/`,
   тестируемы синтетически без БД

---

## Задачи (в этом порядке)

### Task 1 — docs(adr): ADR-0015 entity resolution + safety

**Цель:** зафиксировать алгоритмы + constraint "только suggestions" до кода.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b docs/adr-0015-entity-resolution`
3. Создать `docs/adr/0015-entity-resolution.md`:
   - Status: Accepted, Date: today, Authors: @autotreegen
   - Tags: entity-resolution, dedup, fuzzy-matching, phase-3
   - Контекст: проблема многократного импорта + транслитераций
   - **Главный constraint** (CLAUDE.md §5): only suggestions, no auto-merge
   - Алгоритмы:
     - **Source:** Levenshtein (или RapidFuzz) на normalized title + Jaccard
       на authors → weighted score
     - **Place:** normalized name + token-set ratio (handles "Slonim" vs
       "Slonim, Grodno") → score
     - **Person:**
       - Step 1 — name candidates: Soundex (English/Latin) +
         Daitch-Mokotoff (Jewish-specific, handles Slavic transliteration)
         - Levenshtein
       - Step 2 — date filter: birth_year ±2, death_year ±2 (если есть)
       - Step 3 — place filter: birth_place fuzzy match (используя
         place dedup из выше)
       - Step 4 — composite score: weighted sum
   - Confidence levels:
     - 0.95+ — almost certainly same → highlight strongly
     - 0.80-0.95 — likely same → user reviews
     - 0.60-0.80 — possibly same → user inspects carefully
     - <0.60 — discard
   - **Daitch-Mokotoff library:** `pyphonetics` (MIT) или own implementation
     (algorithm public). Решение: pyphonetics MVP, own — Phase 3.4.x если
     нужен control.
   - Performance: O(n²) сравнение в БД с 100k персон = 10M пар. Решение:
     Step 1 (Soundex/DM) генерирует blocking key, only сравниваем внутри
     bucket → O(n × bucket_size).
   - Когда пересмотреть: появится ML-модель (Phase 4.x), нужен GPU →
     отдельная инфра
4. `pwsh scripts/check.ps1` зелёное (только pre-commit на docs)
5. Commit, push, PR
6. Merge after green CI

### Task 2 — feat(entity-resolution): scaffold package

**Цель:** Pure-function library в `packages/entity-resolution/`.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b feat/phase-3.4-entity-resolution-scaffold`
3. Проверить если `packages/entity-resolution/` существует (per CLAUDE.md
   секция 4a). Если да — добавить в pyproject + наполнить. Если нет — создать.
4. Структура:

   ```text
   packages/entity-resolution/
     pyproject.toml          (требует rapidfuzz>=3.0, pyphonetics>=0.5.3)
     README.md
     src/entity_resolution/
       __init__.py
       phonetic.py           # Soundex + Daitch-Mokotoff wrappers
       string_matching.py    # Levenshtein, token-set, weighted_score
       sources.py            # source_match_score(a, b) -> 0..1
       places.py             # place_match_score(a, b) -> 0..1
       persons.py            # person_match_score(a, b) -> 0..1
       blocking.py           # generate_blocking_keys(records, key_fn)
       py.typed
     tests/
       conftest.py
       test_phonetic.py
       test_string_matching.py
       test_sources.py
       test_places.py
       test_persons.py
       test_blocking.py
   ```

5. Зарегистрировать в корневом `pyproject.toml`:
   - `[tool.uv.workspace] members += "packages/entity-resolution"`
   - `[tool.uv.sources] entity-resolution = { workspace = true }`
6. `uv lock`
7. Базовые stubs с tests, всё green
8. `pwsh scripts/check.ps1` зелёное
9. Commit, push, PR

### Task 3 — feat(entity-resolution): implement matching algorithms

**Цель:** Реализовать все scoring functions с тестами на синтетике.

**Шаги:**

1. `feat/phase-3.4-matching-algorithms`
2. **`phonetic.py`:**

   ```python
   def soundex(name: str) -> str: ...
   def daitch_mokotoff(name: str) -> list[str]:
       """Returns 1+ DM codes (some names produce multiple variants)."""
   ```

3. **`string_matching.py`:**

   ```python
   def levenshtein_ratio(a: str, b: str) -> float:
       """0..1, 1 = identical."""
   def token_set_ratio(a: str, b: str) -> float:
       """RapidFuzz token_set_ratio normalized to 0..1."""
   def weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
       """Weighted average, weights sum doesn't have to be 1.0."""
   ```

4. **`sources.py`:**

   ```python
   def source_match_score(
       a_title: str, a_author: str | None, a_abbrev: str | None,
       b_title: str, b_author: str | None, b_abbrev: str | None,
   ) -> float:
       """Returns 0..1. >0.85 → likely duplicate."""
   ```

   Алгоритм: token_set на title (weight 0.7) + Jaccard на authors split
   (weight 0.2) + abbreviation exact match boost (0.1).
5. **`places.py`:**

   ```python
   def place_match_score(a: str, b: str) -> float:
       """Handles 'Slonim' vs 'Slonim, Grodno' vs 'Slonim, Belarus'."""
   ```

   Token set ratio + substring containment boost.
6. **`persons.py`:**

   ```python
   def person_match_score(
       a: PersonForMatching,
       b: PersonForMatching,
   ) -> tuple[float, dict[str, float]]:
       """Returns (composite, components) — components used for explainability."""
   ```

   PersonForMatching = small dataclass с (given, surname, birth_year,
   death_year, birth_place, sex). Composite = weighted Soundex + DM bucket
   match (0.3) + name Levenshtein (0.3) + birth_year ±2 (0.2) + birth_place
   match (0.2). Sex mismatch → discard (return 0.0).
7. **`blocking.py`:**

   ```python
   def block_by_dm(persons: Iterable[PersonForMatching]) -> dict[str, list[PersonForMatching]]:
       """Bucket persons by Daitch-Mokotoff(surname). Скеж compare only within bucket."""
   ```

8. Тесты:
   - test_soundex_zhitnitzky_variants_match (`Zhitnitzky`, `Zhitnitsky`,
     `Zhytnicki` → same Soundex code OR overlapping DM codes)
   - test_dm_handles_slavic_to_english_transliteration (Жytницкий variants)
   - test_source_dedup_lubelskie_parish_records
     (`Lubelskie parish records 1838` vs `Lubelskie Parish 1838` → ≥0.85)
   - test_place_dedup_slonim_with_region (`Slonim` vs `Slonim, Grodno` → ≥0.80)
   - test_person_dedup_full_match
   - test_sex_mismatch_returns_zero
   - test_blocking_buckets_correctly
9. `pwsh scripts/check.ps1` зелёное
10. Commit, push, PR

### Task 4 — feat(parser-service): dedup_finder service + tests

**Цель:** Сервис который применяет algorithms к содержимому БД tree
и возвращает suggestions.

**Шаги:**

1. `feat/phase-3.4-dedup-service`
2. `services/parser-service/src/parser_service/services/dedup_finder.py`:

   ```python
   async def find_source_duplicates(
       session: AsyncSession,
       tree_id: UUID,
       threshold: float = 0.80,
   ) -> list[DuplicateSuggestion]:
       """Returns pairs of (source_a_id, source_b_id, score, evidence)."""

   async def find_place_duplicates(...): ...
   async def find_person_duplicates(
       session: AsyncSession,
       tree_id: UUID,
       threshold: float = 0.80,
       use_blocking: bool = True,
   ) -> list[DuplicateSuggestion]: ...
   ```

3. `DuplicateSuggestion` Pydantic в `parser_service/schemas.py`:

   ```python
   class DuplicateSuggestion(BaseModel):
       entity_type: Literal["source", "place", "person"]
       entity_a_id: UUID
       entity_b_id: UUID
       confidence: float  # 0..1
       components: dict[str, float] = {}  # explainability
       evidence: dict[str, Any] = {}  # human-readable diff
   ```

4. Тесты `tests/test_dedup_finder.py`:
   - test_find_source_duplicates_after_double_import:
     - Импортируем `_MINIMAL_GED` дважды → should find dups
   - test_find_person_duplicates_with_transliterated_surname:
     - Создать 2 persons с (Zhitnitzky / Zhytnicki) + same birth_year + same place
     - find_person_duplicates → ≥0.80 confidence
   - test_threshold_filters_low_confidence
5. **Performance test (опциональный):** на 1000+ persons, blocking должен
   быть быстрее full O(n²) at least 5x.
6. `pwsh scripts/check.ps1` зелёное
7. Commit, push, PR

### Task 5 — feat(api): /trees/{id}/duplicate-suggestions endpoint

**Цель:** REST endpoint для UI (Phase 4.5).

**Шаги:**

1. `feat/phase-3.4-dedup-api`
2. Создать `services/parser-service/src/parser_service/api/dedup.py`:
   - `GET /trees/{tree_id}/duplicate-suggestions`
   - Query params: `entity_type` (sources/places/persons), `min_confidence`
     (default 0.80), `limit` (default 100), `offset`
   - Возвращает `DuplicateSuggestionListResponse`
3. Register router в `main.py` (минимальная правка — `app.include_router(dedup.router)`)
4. Тесты:
   - test_get_dedup_suggestions_returns_pairs
   - test_filter_by_entity_type
   - test_min_confidence_threshold
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR

### Task 6 (опционально) — docs(roadmap): mark Phase 3.4 done

После всех 5 PR:

- ROADMAP.md §7.0 update
- README parser-service: добавить раздел Entity Resolution

---

## Что НЕ делать

- ❌ **Auto-merge entities** — даже при confidence 1.0 (CLAUDE.md §5)
- ❌ **Modify existing entities** в этом скоупе — только READ + return suggestions
- ❌ **DELETE/UPDATE из dedup_finder** — никаких side effects на БД
- ❌ **ML-модели** (sentence-transformers, embeddings) — Phase 4.x когда
  будет GPU инфра
- ❌ **Cross-tree dedup** — только within-tree пока
- ❌ Трогать пакеты других агентов
- ❌ `git commit --no-verify`
- ❌ Мердж с красным CI

---

## Сигналы успеха

После 5 PR:

1. ✅ ADR-0015 в `docs/adr/`
2. ✅ `packages/entity-resolution/` функциональный, ≥80% test coverage
3. ✅ `Zhitnitzky / Zhytnicki / Жitnицкий` → DM-buckets overlap → matched
4. ✅ `GET /trees/{id}/duplicate-suggestions?entity_type=person` работает
5. ✅ Никаких auto-mutations БД из dedup_finder (verified тестами)
6. ✅ Все CI зелёные

---

## Coordination

- Если корневой `pyproject.toml` / `uv.lock` конфликтует с Агентом 4 — rebase + uv lock
- `services/parser-service/main.py` — Агент 1 может править (если регистрирует
  свой router для tree viz). Если конфликт — взять обе строки `app.include_router`.
- `schemas.py` — Агент 1 добавляет `AncestorTreeNode`, ты добавляешь
  `DuplicateSuggestion`. Разные классы, конфликт минимальный.
- **Worktree isolation рекомендую:** `git worktree add ../TreeGen-phase34 main`
  чтобы избежать race conditions с Агентами 1/3/4 в main worktree.

Удачи. Это где AutoTreeGen начинает делать ML-style работу с
твоей фамильной spezificity (Daitch-Mokotoff именно для еврейской
генеалогии). Жду PR-ссылок и demo на Zhitnitzky variants.
