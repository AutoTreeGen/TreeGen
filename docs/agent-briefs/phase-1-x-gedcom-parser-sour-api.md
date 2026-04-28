# Agent brief — Phase 1.x: gedcom-parser SOUR sub-tags API

> **Кому:** Агент 5 (Claude Code CLI, bypass on) — first task на TreeGen.
> **Контекст:** Windows, `D:\Projects\TreeGen`, default branch `main`.
> **Перед стартом:** обязательно прочитай `CLAUDE.md`, `ROADMAP.md`,
> `docs/adr/0008-ci-precommit-parity.md`.
> **Worktree:** рекомендую `git worktree add ../TreeGen-gedcom main` для
> изоляции от 4 параллельных агентов в main worktree.

---

## Контекст

`packages/gedcom-parser/` — основной парсер GEDCOM в проекте, функциональный
(парсит persons/families/events/places/dates). Покрывает GEDCOM 5.5.5 +
proprietary tags (Ancestry/MyHeritage/Geni).

**Найденный gap (Phase 3.3, Агент 2):**

- Citation sub-tags: PAGE, QUAY, NOTE, EVEN, ROLE — **не expose'ятся**
  через high-level API
- Агент 2 был вынужден спуститься на `parse_document_file` +
  manual `GedcomDocument.from_records` + walked raw `GedcomRecord` для
  SOUR sub-tags
- Это working workaround в parser-service, но **технический долг** —
  каждый кто хочет читать citations должен повторять этот workaround

**Phase 1.x — закрыть этот gap properly.**

После этого:

- parser-service `import_runner.py` может упростить citation impport
- futures sources (FamilySearch, MyHeritage XML) — общий API

**Параллельно работают:**

- Агент 1: `apps/web/` + `services/parser-service/api/trees.py`
- Агент 2: `packages/entity-resolution/` + `services/parser-service/services/dedup_finder.py`
- Агент 3: `packages/familysearch-client/`
- Агент 4: `packages/dna-analysis/`

**Твоя территория** (нулевое пересечение):

- `packages/gedcom-parser/` — целиком твой
- `docs/gedcom-extensions.md` — обновить если нужно
- `services/parser-service/src/parser_service/services/import_runner.py` —
  **только если** нужно убрать workaround (опционально, координируй
  с Агентом 2 через rebase)

**Что НЕ трогай:**

- Всё кроме `packages/gedcom-parser/` и `docs/gedcom-extensions.md`

---

## Цель Phase 1.x

1. Дополнить `gedcom_parser` API так чтобы PAGE / QUAY / NOTE / EVEN / ROLE
   sub-tags Citation были first-class объектами, доступными через
   high-level API (`person.events[i].sources[j].page`, etc).
2. Backwards compatibility — existing API не должен ломаться.
3. Тесты на real-world GED фикстурах + synthetic edge cases.
4. После merge — отдельный follow-up PR (опционально) который убирает
   workaround в `parser-service/services/import_runner.py`.

---

## Задачи (в этом порядке)

### Task 1 — feat(gedcom-parser): expose Citation sub-tags

**Цель:** API `event.sources[i].page` etc работает.

**Шаги:**

1. `git checkout main && git pull`
2. `git worktree add ../TreeGen-gedcom feat/phase-1.x-citation-subtags`
3. `cd ../TreeGen-gedcom`
4. Изучить текущую структуру в `packages/gedcom-parser/src/gedcom_parser/`:
   - Найти где парсится Source/SOUR
   - Найти где парсится Event
   - Понять как сейчас Event.sources хранятся
5. Расширить `Citation` (или создать если нет) Pydantic/dataclass:

   ```python
   @dataclass
   class Citation:
       source_xref: str | None      # @S1@
       page: str | None             # PAGE "p. 42"
       quality: int | None          # QUAY 0-3
       notes: list[str] = field(default_factory=list)  # NOTE
       event_role: str | None = None # EVEN / ROLE
       data_text: str | None = None  # DATA / TEXT
   ```

6. В Event-parsing логике: для каждого SOUR sub-record парсить sub-tags
   и наполнять Citation.
7. Аналогично на Person-level (если GEDCOM позволяет SOUR direct under INDI).
8. Backwards compat: существующее `event.sources` (list[str]?) должно
   продолжать работать или мигрировать на `event.citations: list[Citation]`
   с deprecation warning.
9. Тесты:
   - test_citation_with_page_and_quay
   - test_citation_with_notes
   - test_event_with_role_subtag
   - test_multiple_citations_per_event
   - test_backwards_compat_old_api_still_works (если deprecation, не remove)
10. `uv run pytest packages/gedcom-parser -m "not gedcom_real"` — green
11. `pwsh scripts/check.ps1` зелёное
12. Commit, push, PR

### Task 2 — feat(gedcom-parser): expose Source repository / publication

**Цель:** SOUR record sub-tags REPO, PUBL, AUTH, ABBR доступны
через `Source` объект.

**Шаги:**

1. `feat/phase-1.x-source-metadata`
2. Расширить `Source`:

   ```python
   @dataclass
   class Source:
       xref: str
       title: str | None
       author: str | None             # AUTH
       abbreviation: str | None       # ABBR
       publication: str | None        # PUBL
       repository_xref: str | None    # REPO @R1@
       text_excerpt: str | None       # TEXT
   ```

3. Парсинг REPO records (если ещё не парсятся) — стандартный GEDCOM
4. Тесты:
   - test_source_with_author_and_publication
   - test_source_repository_xref_resolved
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR

### Task 3 — test: real-world GED corpus regression

**Цель:** убедиться что existing GED-файлы (Ancestry / MyHeritage /
Geni dialects) парсятся без regression.

**Шаги:**

1. `feat/phase-1.x-real-corpus-tests`
2. Прогнать `GEDCOM_TEST_CORPUS=D:/Projects/GED uv run pytest
   packages/gedcom-parser -m gedcom_real`
3. Если что-то падает — fix (это и есть real-world coverage)
4. Если всё ОК — добавить assertion на новые поля Citation/Source в
   smoke-тесте на личном Ztree.ged
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR

### Task 4 — docs: update gedcom-extensions.md

**Цель:** документировать новые expose'нутые поля.

**Шаги:**

1. `docs/phase-1.x-extensions-doc`
2. В `docs/gedcom-extensions.md` добавить секцию "Citation sub-tags"
   с примерами GEDCOM → API маппингов
3. Обновить `packages/gedcom-parser/README.md` если есть
4. `pwsh scripts/check.ps1` зелёное
5. Commit, push, PR

### Task 5 (опционально) — refactor(parser-service): remove citation workaround

**Цель:** убрать workaround из `import_runner.py` который Агент 2 написал.

**Координация:** Агент 2 работает в Phase 3.4 на `dedup_finder.py`,
**не трогает** `import_runner.py`. Конфликт unlikely. Но обязательно
ребейзись на main перед стартом.

**Шаги:**

1. После мерджа Tasks 1-2 — pull main
2. `refactor/phase-1.x-import-runner-citation-cleanup`
3. В `services/parser-service/src/parser_service/services/import_runner.py`:
   - Найти место где сейчас walks raw GedcomRecord для SOUR sub-tags
   - Заменить на новый высокоуровневый API
4. Запустить parser-service тесты — должны остаться зелёными
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR

---

## Что НЕ делать

- ❌ Breaking changes API без deprecation period — всегда backwards compat
- ❌ Трогать пакеты других агентов
- ❌ ML/AI на парсинге — парсер должен быть deterministic
- ❌ Auto-correction опечаток в GEDCOM — round-trip без потерь приоритет
- ❌ `git commit --no-verify`
- ❌ Мердж с красным CI

---

## Сигналы успеха

После 4 PR (5-й опциональный):

1. ✅ `event.citations[0].page == "p. 42"` работает на real GED
2. ✅ Backwards compat: existing tests все green
3. ✅ Real-world corpus прогон не падает
4. ✅ Documentation в `gedcom-extensions.md`
5. ✅ Все CI зелёные

---

## Coordination

- Корневой `pyproject.toml` / `uv.lock` — если конфликт, rebase + uv lock
- Чужие пакеты — не трогай
- Если решишь делать Task 5 — координируй с Агентом 2 (они в Phase 3.4
  на parser-service/services/dedup_finder.py — другой файл, но рядом)

Удачи. Это техдолг closure, не sexy но **снимает workaround** который
будут впредь иметь все consumers gedcom-parser (FamilySearch importer,
будущие parsers).
