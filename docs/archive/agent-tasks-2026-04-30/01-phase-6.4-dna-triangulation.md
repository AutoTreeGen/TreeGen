# Agent 1 — Phase 6.4: DNA Triangulation engine

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (код+документация EN, docstrings/комментарии RU, Conventional Commits, Python 3.12, FastAPI 0.115+, Pydantic v2, SQLAlchemy 2 async, `uv`, pre-commit must pass, **запрещено `--no-verify`**, ветка `feat/<short-name>`, никогда не коммитить в `main`).
2. `ROADMAP.md` — секция Phase 6 (DNA), особенно §10.4 строка «**6.4** Triangulation + Bayes-prior из дерева — Planned».
3. `docs/architecture.md`, `docs/data-model.md`, `docs/adr/` (особенно ADR-0014 pairwise matching, ADR-0020 dna-service, ADR-0033 DNA UI).
4. Существующий код: `packages/dna-analysis/`, `services/dna-service/`.

## Задача

Реализовать **triangulation engine** — compute-only, **без записи в БД** в этой итерации (никаких Alembic-миграций — это в Phase 6.5).

## Scope

### `packages/dna-analysis/`

- Новый модуль `triangulation.py`:
  - `find_triangulation_groups(matches: list[Match], min_overlap_cm: float = 7.0) -> list[TriangulationGroup]`
  - Алгоритм: для каждой пары matches (A, B), которые сами matches между собой, найти общие IBD-сегменты на той же хромосоме с overlap ≥ `min_overlap_cm`. Группа — связная компонента в графе таких триплетов.
  - Эффективная реализация: интервальное дерево или просто sort-by-start + sweep на хромосому. O(n log n) на хромосому.
- Pydantic-модель `TriangulationGroup(chromosome, start_cm, end_cm, members: list[match_id], confidence_boost: float)`.
- `bayes_boost(group: TriangulationGroup, tree_relationship: str | None) -> float` — простой множитель confidence для same_person гипотезы. ≥3 triangulating matches с известной MRCA → 1.5x; без MRCA → 1.0x; одинокий триплет → 1.2x.

### `services/dna-service/`

- Эндпоинт `GET /trees/{tree_id}/triangulation?min_overlap_cm=7.0`:
  - Permission gate `require_tree_role(TreeRole.VIEWER)` (паттерн из `parser-service/api/sharing.py`).
  - Compute on demand. Кэш в Redis на 1 час по ключу `dna:triangulation:{tree_id}:{min_overlap}`.
  - Возврат: `list[TriangulationGroup]`.

### ADR-0054 в `docs/adr/`

Обоснование алгоритма (sliding-window 7 cM по умолчанию, ссылки на DNA Painter / GEDmatch методологию), trade-offs (false positives при endogamy — отметить как known limitation, fix в Phase 6.5 через IBD2).

## Тесты (покрытие новой логики > 80%)

- `packages/dna-analysis/tests/test_triangulation.py` — unit:
  - пустой вход; 2 matches без триангуляции; 3 matches с overlap; edge case overlap ровно `min_overlap_cm`; разные хромосомы (не должны триангулироваться); endogamy synthetic case (>10 matches на одном сегменте — алгоритм должен отметить как low confidence).
- `services/dna-service/tests/test_triangulation_endpoint.py` — интеграционный:
  - 403 для не-владельца; 200 для VIEWER+; cache hit (второй вызов в 10× быстрее или без БД-запросов через mock); формат ответа.

## Запреты

- ❌ Alembic-миграции
- ❌ `packages/shared-models/`
- ❌ `apps/web/messages/*.json`
- ❌ Корневой `pyproject.toml`

## Процесс

1. `git checkout -b feat/phase-6.4-dna-triangulation`
2. Маленькие осмысленные коммиты (Conventional Commits: `feat(dna-analysis): ...`, `feat(dna-service): ...`, `docs(adr): add ADR-0054`, `test(dna-analysis): ...`).
3. Перед каждым коммитом: `uv run pre-commit run --all-files` + `uv run pytest packages/dna-analysis services/dna-service`.
4. **НЕ мержить, НЕ пушить в `main`.**

## Финальный отчёт

- Имя ветки.
- Список коммитов (`git log --oneline main..HEAD`).
- Pytest summary (passed/failed/skipped).
- Путь к ADR-0054.
- Список созданных/изменённых файлов.
- Открытые вопросы / TODO для Phase 6.5.
