# ADR-0076: Completeness assertions / sealed sets

- **Status:** Accepted
- **Date:** 2026-05-02
- **Authors:** @autotreegen
- **Tags:** `data-model`, `evidence`, `tree-service`

## Контекст

Genealogy research постоянно упирается в вопрос «мы закончили искать X?».
Без явного primitive'а каждый downstream-консьюмер (research-log,
hypothesis sandbox, archive search planner, AI tree-context pack) повторно
предлагает «look for more siblings of person Y» вечно. Owner'ы вручную
добавляют notes вида «exhaustive — no other children» в свободной форме,
и эти notes невозможно ни запросить, ни уважать программно.

Нужен evidence-laden primitive «scope X around person Y is closed»,
требующий source-citation для каждого утверждения и поддающийся revoke
при появлении нового доказательства.

## Рассмотренные варианты

### Вариант A — Boolean флаг на Person (`is_siblings_complete: bool`)

- ✅ Минимум LOC, ноль миграций junction'ов.
- ❌ Coarse: одна персона имеет несколько scope'ов (siblings/children/
  spouses/parents). Нужно ≥4 boolean'а — расширение enum'ов сложнее, чем
  отдельная таблица.
- ❌ Без source-attribution — нельзя отделить «owner просто кликнул»
  от «owner нашёл в метрической книге».
- ❌ Revoke без audit-trail.

### Вариант B — Атрибут на каждом source-citation'е

- ✅ Source-coupled by design.
- ❌ Семантика инверт-евая: assertion'ы — это утверждения *о scope*, не
  о citation'е. Один citation может обосновать несколько scope'ов
  («ревизская сказка перечисляет всех siblings» = и siblings sealed для
  персоны A, и parents sealed для каждого её ребёнка).
- ❌ Сложный query «is scope sealed?» требует scan'а citation'ов всех
  членов scope'а.

### Вариант C — Внешний rules engine

- ✅ Гибкость.
- ❌ Overkill: assertion'ы статичны (≤десятки на дерево), не требуют
  inference. Плата за rules engine — Operationально и cognitive — несравнима
  с пользой.

### Вариант D — Отдельная таблица `completeness_assertions` + junction `completeness_assertion_sources` (выбран)

- ✅ Source-coupled через junction, ≥1 source требуется (service-layer).
- ✅ Native query «is scope sealed?» — single index hit на
  `(tree_id, subject_person_id, scope)`.
- ✅ Revoke — set `is_sealed=False`, row остаётся для audit (не destructive).
- ✅ Расширение enum'а scope'ов — дешёвая string-column миграция.

## Решение

Принять **Вариант D**. Две новые таблицы в parser-service (canonical tree
CRUD service per architectural redirect от 2026-05-02; brief'овый
``tree-service`` не существует — pivot аналогичен Phase 5.7b api-gateway →
parser-service):

- `completeness_assertions` — TreeEntityMixins (id / status / confidence /
  provenance / version_id / timestamps / soft-delete) + ``subject_person_id`` /
  ``scope`` / ``is_sealed`` / ``asserted_at`` / ``asserted_by`` / ``note``.
- `completeness_assertion_sources` — junction (assertion_id, source_id),
  composite PK, CASCADE on assertion delete, RESTRICT on source delete.

CRUD endpoints в `parser_service.api.completeness` под
`/trees/{tree_id}/persons/{person_id}/completeness`. POST — create-or-upsert
(одна active assertion на (tree, person, scope)). DELETE — revoke
(`is_sealed=False`, чистит junction, KEEPS row).

## Последствия

- Каждый downstream-консьюмер должен (в 15.11c) звать
  `is_scope_sealed(person, scope)` ДО предложения новых connection'ов
  внутри scope'а.
- Schema invariants: `completeness_assertions` → TREE_ENTITY_TABLES (полные
  mixin'ы); `completeness_assertion_sources` → SERVICE_TABLES (pure m2m,
  как `family_children`).
- Source-count invariant (≥1 на `is_sealed=True`) НЕ enforced на уровне
  БД: Postgres не выражает это без триггеров. Service-layer проверка с
  TODO для **15.11b**, который ужесточает invariant до 422 + расширяет
  permission gates.
- False-positive seal mitigated через DELETE-as-revoke (не destructive —
  audit-trail сохраняется); **15.11d** добавит UI lock-icon affordance.
- Brief изначально просил `tree_id ondelete=CASCADE`; я пошёл по
  проектной конвенции (`TreeScopedMixin` → RESTRICT) — soft-delete-first
  паттерн ADR-0003: tree-purge должен явно очищать assertion'ы, не
  тихо стирать genealogical claim'ы.

## Принятые отклонения от brief'а

| Brief'овая позиция | Реальность | Обоснование |
|---|---|---|
| `tree-service` package + service | parser-service | tree-service не существует в main; parser-service уже владеет tree CRUD (precedent: Phase 5.7b safe-merge) |
| Alembic 0036 | Alembic 0040 | 0036–0039 уже claimed в sibling worktrees (per `next-chain-number.ps1`) |
| ADR-0075 | ADR-0076 | 0075 уже claimed в sibling worktree |
| `tree_id ON DELETE CASCADE` | RESTRICT | Project convention `TreeScopedMixin`; ADR-0003 soft-delete-first |
| Scopes: descendants/ancestors не упоминались | Не добавлены в 15.11a | Brief заявил 4 scope'а; рекурсивные scope'ы требуют отдельной revoke-cascade семантики (15.11b/c) |
