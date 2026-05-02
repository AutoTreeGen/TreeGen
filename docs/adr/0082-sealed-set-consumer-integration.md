# ADR-0082: Sealed-Set Consumer Integration

- **Status:** Accepted
- **Date:** 2026-05-03
- **Authors:** @autotreegen
- **Tags:** `data-model`, `evidence`, `consumer-integration`

## Контекст

Phase 15.11a (ADR-0076) ввёл primitive «sealed set» — owner-asserted-negation
flag на scope вокруг анкорной персоны (siblings/children/spouses/parents
exhaustive). Phase 15.11b (ADR-0077) добавил validation chokepoint в
parser-service (source-required, role-gated, override-with-audit).

Без интеграции с downstream-консьюмерами sealed-флаг — просто запись в
БД. Каждый из них (Evidence Panel, Research Log / Archive Search Planner,
Hypothesis Sandbox, AI Tree Context Pack) продолжает предлагать «найти
ещё одного брата» даже когда owner уже зафиксировал «все 4 учтены».

15.11c делает sealed-флаг живым: каждый consumer узнаёт о нём и либо
аннотирует свой output, либо вообще пропускает suggestion-логику.

## Рассмотренные варианты

### Вариант A — Helper в parser-service, библиотечный re-export

- ✅ Helper рядом с CRUD/validation, ясное single-source-of-truth.
- ❌ Cross-service code import (archive-service импортирует из parser-service)
  — services depending on services через Python-import нарушает service-isolation.

### Вариант B — Helper в `packages/shared-models`, рядом с ORM-моделью (выбран)

- ✅ Shared-models — каноническое место для cross-service ORM helpers
  (как `register_audit_listeners`, `reset_document_type_weight_cache` и т.п.).
- ✅ Read-side helper'ам симметрично write-side validation в parser-service
  (ADR-0077 §location): «validation lives with the service that owns the
  operation» — а read-only query lives with the ORM definition.
- ✅ Никаких inter-service Python imports — каждый service зависит только
  от `shared-models`.

### Вариант C — Tiny `packages/sealed-set/` library

- ❌ Overhead: один файл с двумя async-функциями не оправдывает workspace
  member, конфигурацию pyproject, отдельные tests.

## Решение

Принять **Вариант B**. Два публичных helper'а co-located с ORM-моделью в
`packages/shared-models/src/shared_models/orm/completeness_assertion.py`:

- `is_scope_sealed(session, person_id, scope) -> bool` — single-scope check.
- `sealed_scopes_for_person(session, person_id) -> frozenset[CompletenessScope]`
  — все active sealed scope'ы (одним SQL для consumer'ов, которые проверяют
  несколько scope'ов сразу: AI prompt, planner annotation).

Каждый из 4 консьюмеров использует свой паттерн интеграции в зависимости
от семантики:

| Консьюмер | Файл | Паттерн |
|---|---|---|
| **15.3 Evidence Panel** | `parser-service/api/relationships.py` | Annotate response (`subject_sealed_scopes`/`object_sealed_scopes` в `RelationshipEvidenceResponse`). UI показывает 🔒 на «add another» CTA. |
| **15.5 Research Log / Archive Planner** | `archive-service/planner/router.py` + `schemas.py` | Annotate response (`sealed_scopes` field в `PlannerResponse`). UI рендерит «🔒 siblings sealed — no further search». Suggestions не фильтруются (event может быть undocumented даже при sealed family-scope). |
| **15.6 Hypothesis Sandbox** | `parser-service/services/hypothesis_runner.py` | Hard skip: `compute_hypothesis()` early-return'ит `None` если соответствующий scope опечатан для любой стороны. PARENT_CHILD проверяет parents+children (canonical-order не сохраняет направление). |
| **10.7 AI Tree Context Pack** | `parser-service/api/chat.py` | Annotate system-prompt: добавляется явное «do NOT suggest searching for additional members of these scopes (owner has marked them exhaustive)». |

## Последствия

- Каждый consumer стал sealed-aware за один SQL-roundtrip per request
  (двусторонний для relationships endpoint — subject + object).
- Hypothesis Sandbox полностью блокирует генерацию для опечатанных scope'ов:
  это самое жёсткое поведение, мотивированное тем, что hypothesis WRITE'ит
  в БД (тогда как Evidence Panel и AI prompt — read-only views).
- Archive Planner НЕ блокирует suggestions: события могут оставаться
  undocumented даже когда family-scope закреплена. UI показывает sealed-info
  как hint.
- AI prompt получает sealed-scope info только если что-то закреплено
  (пустой `frozenset` → пустая аннотация → no prompt noise для деревьев
  без assertions).
- Phase 5.10 Fantasy Filter (#198) интеграция ОТЛОЖЕНА на следующий PR
  (15.11c-followup): #198 ещё не в main, stacking запрещён по
  `feedback_no_stacked_prs.md`.

## Принятые отклонения от brief'а

| Brief | Реальность | Обоснование |
|---|---|---|
| ADR-0081 | ADR-0082 | 0081 уже claimed в sibling worktree (per `next-chain-number.ps1`). |
| 5 consumers | 4 consumers | Phase 5.10 Fantasy Filter (#198) не в main; stacking запрещён. Defer to 15.11c-followup. |
| Helper «в parser-service» | `packages/shared-models/` | Owner после reflection одобрил shared-models location (ADR-0077 §«validation home», симметрия read/write). |

## См. также

- ADR-0076 (Phase 15.11a) — primitive schema + CRUD foundation.
- ADR-0077 (Phase 15.11b) — validation chokepoint в parser-service.
- ADR-0058 (Phase 15.1) — relationship-level evidence aggregation
  (consumer 15.3).
- ADR-0023 (Phase 7.3.1) — DNA hypothesis composition (consumer 15.6).
