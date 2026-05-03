# ADR-0077: Completeness Assertions — Validation Layer

- **Status:** Proposed
- **Date:** 2026-05-02
- **Authors:** AutoTreeGen
- **Tags:** `tree`, `completeness`, `validation`, `audit`, `phase-15-11b`

## Контекст

Phase 15.11a (ADR-0076, PR #199) поставил **permissive** CRUD над
`completeness_assertions` — endpoint'ы создают/upsert'ят/revoke'ят row'ы
без enforcement бизнес-инвариантов:

1. `is_sealed=True` без source_ids — допускается (TODO 15.11b).
2. source_ids не валидируются: cross-tree / soft-deleted / unknown id
   проходят насквозь, материализуются как FK-ошибки или silent corruption.
3. Re-assertion другим user'ом просто перезаписывает `asserted_by` —
   нет provenance того, кто и когда форсировал unseal/seal-flip.
4. Revoke не оставляет audit-trail сверх auto-listener'а.

Brief Phase 15.11b просит залатать четыре дыры одним PR'ом, без новой
схемы и без UI. Validation должен быть «chokepoint»-модулем, через
который проходят все mutating-handler'ы — иначе дыры в покрытии будут
неизбежны при появлении ещё одного call-site (15.11c consumer's
batch-update, например).

## Рассмотренные варианты

### Вариант A — DB CHECK constraints + triggers

- ✅ Невозможно обойти service-layer.
- ❌ `>=1 source` invariant требует сравнения с junction-table —
  CHECK constraint на parent-table этого не выразит без trigger'а.
- ❌ Role-gate в БД отсутствует (роли — application-level concept).
- ❌ Override mechanic с audit-row требует написания row'и в другую
  таблицу — DB triggers это умеют, но debug-/test-experience плохой.
- ❌ Schema migration → coordination cost (другие worktree'ы тоже
  бьются за alembic-номера; см. `feedback_phase_parallelization_alembic.md`).

### Вариант B — Middleware (per-route validator)

- ✅ Чисто декларативно: `@validate_completeness_create` на роут.
- ❌ Middleware видит только request body, не имеет async-session →
  не может валидировать source-liveness без второго round-trip'а.
- ❌ Bypass при появлении alternate caller'ов (worker/CLI/admin),
  которые не идут через FastAPI-middleware stack.

### Вариант C — Service-layer chokepoint module *(выбран)*

- ✅ Чистый callable, `await validate_assertion_create(session, ...)`,
  принимает session и переиспользует её в одной транзакции.
- ✅ Reusable для будущих non-HTTP caller'ов (worker, CLI).
- ✅ Лёгкий тест: integration-tests через `app_client` + unit-tests
  вызывают функцию напрямую (нет тестов через middleware).
- ✅ Никаких schema/migration-изменений → конкуренции за alembic нет.
- ❌ Только bypass-protected if все mutating-handler'ы вызывают
  validator — митигация: lint-rule TODO в этом ADR (см. ниже).

## Решение

Выбран **Вариант C** — service-layer chokepoint module
`services/parser-service/src/parser_service/completeness/validation.py`.

### Контракт

```python
async def validate_assertion_create(
    session: AsyncSession,
    *,
    tree_id: UUID,
    subject_person_id: UUID,
    scope: CompletenessScope,
    is_sealed: bool,
    source_ids: list[UUID],
    actor_user_id: UUID,
    override: bool = False,
) -> AssertionUpsertContext

async def validate_assertion_revoke(
    session: AsyncSession,
    *,
    tree_id: UUID,
    subject_person_id: UUID,
    scope: CompletenessScope,
    actor_user_id: UUID,
) -> AssertionRevokeContext

def emit_completeness_audit(
    session: AsyncSession,
    *,
    tree_id: UUID,
    assertion_id: UUID,
    actor_user_id: UUID,
    action: AuditAction,
    reason: str,
    diff: dict[str, Any],
) -> None
```

Validator raises `HTTPException`-subclasses, которые FastAPI рендерит как
422 / 409 (см. ниже). Возвращает `AssertionUpsertContext` /
`AssertionRevokeContext` — лёгкие dataclass'ы, через которые caller
понимает, что именно произошло (insert vs upsert vs override) и
эмитит audit-row при необходимости.

### Status-codes

| Случай | Статус | Класс ошибки |
|---|---|---|
| sealed без source_ids | 422 | `SourceRequiredError` |
| source_id не существует | 422 | `SourceNotFoundError` |
| source принадлежит другому tree | 422 | `SourceCrossTreeError` |
| source soft-deleted | 422 | `SourceDeletedError` |
| re-assert другим user'ом без override | 409 | `OverrideRequiredError` |

### Audit-emission

Auto-listener из `shared_models.audit` фиксирует `INSERT/UPDATE/DELETE`
diff'ы для самой `CompletenessAssertion`-row'ы — это достаточно для
forensics. Дополнительно мы эмитим **manual audit-row** с
`reason="override_reassertion"` или `reason="revoke"` и event-specific
metadata в `diff`:

- `override_reassertion`: `{scope, subject_person_id, prev_actor_id,
  new_actor_id, is_sealed, source_count}`.
- `revoke`: `{scope, subject_person_id, prev_actor_id, revoking_actor_id}`.

Это даёт consumer'ам (15.11c hypothesis-recompute / 15.11d UI history)
дёшево фильтровать `audit_log WHERE entity_type='completeness_assertions'
AND reason IN ('override_reassertion', 'revoke')` — не пробираясь через
diff'ы auto-listener'а.

### Role gate

Framework-level `require_tree_role(TreeRole.EDITOR)` уже навешен на
POST/DELETE-роуты в 15.11a (см. `services/parser-service/src/parser_service/api/completeness.py`). 15.11b НЕ дублирует этот gate
внутри validator'а — иначе mismatch с `safe_merge` / `ego_anchor`
конвенцией. Тест `test_viewer_role_rejected_403` в наборе 15.11b
проверяет именно framework-level gate.

## Принятые отклонения от brief'а

- **Нет «researcher» роли.** Brief упоминает «owner / editor / researcher
  can assert; viewer cannot» — но в `shared_models.enums.TreeRole`
  существуют только `OWNER`, `EDITOR`, `VIEWER` (см. ADR-0058 на role
  hierarchy). Brief'овский тест `test_researcher_can_assert` в покрытии
  15.11b пропущен; вместо него остаются `test_owner_can_assert` и
  `test_editor_can_assert`. Если в будущем добавится `RESEARCHER`-роль —
  validator её НЕ ловит явно (полагается на framework-gate `EDITOR`,
  поэтому будет работать так же, как `OWNER`/`EDITOR`).

- **ADR-0076 → ADR-0077.** Brief явно просил «ADR-0076». 15.11a (Phase
  15.11a's PR #199) переопределил свой ADR с 0075 → 0076 (так как 0075
  взят PR #197 «voice-to-tree NLU»). 15.11b следует тому же
  reconcile-pattern: ADR-0076 занят 15.11a → 15.11b'у достаётся
  ADR-0077 (следующий свободный, проверено через `gh search prs --state
  open --jq` на 2026-05-02).

- **Validator возвращает context, а не None.** Brief описывал сигнатуру
  `validate_assertion_create(...) -> None  # raises ValidationError`.
  Реализация возвращает `AssertionUpsertContext`, чтобы caller
  (handler) знал, эмитировать ли override-audit без второго lookup'а
  existing-row'ы. Семантика «raise on failure» сохранена; добавлен
  лишь возвращаемый context-блок.

## Последствия

**Положительные:**

- Все 4 дыры из 15.11a закрыты одним chokepoint-модулем.
- Audit-trail на override/revoke даёт consumer'ам Phase 15.11c+
  дешёвый запрос «кто и когда нарушил seal».
- No-schema-change → нет coordination cost с alembic-chain (см.
  `feedback_phase_parallelization_alembic.md`).

**Отрицательные / стоимость:**

- Каждый новый mutating-caller `completeness_assertions` обязан
  явно вызывать validator. Без lint-rule (TODO ниже) это легко
  забыть. Митигация: integration-tests покрывают все routes на
  validation-rejection.

**Риски:**

- **Bypass через alternate caller** (worker, CLI). Митигация:
  validator — единственный exposed callable из
  `parser_service.completeness`, его документация явно говорит «вызывайте
  перед mutation». TODO: добавить ruff-rule, который ловит
  `session.add(CompletenessAssertion(...))` без предшествующего
  `await validate_assertion_create(...)` — отдельный chore-PR в 15.11d.

**Что нужно сделать в коде:**

- `services/parser-service/src/parser_service/completeness/__init__.py`
  - `validation.py` — chokepoint-модуль (выполнено в этом PR).
- Update `api/completeness.py` — replace inline create/upsert logic
  with `validate_assertion_create()` call (выполнено).
- Add `override: bool = False` to `CompletenessAssertionCreate`
  (выполнено).
- Update revoke handler — call `validate_assertion_revoke` + emit
  audit (выполнено).
- Update 15.11a's `test_create_sealed_without_sources_accepted_15_11a`
  → `test_create_sealed_without_sources_rejected_422` (выполнено).
- New test file `test_completeness_validation.py` — 12 cases
  (выполнено; researcher-test пропущен, см. отклонения).

## Когда пересмотреть

- При появлении non-HTTP caller'ов `completeness_assertions` (worker,
  CLI, admin tool) — нужно проверить, что они идут через validator.
- При расширении `TreeRole` enum (добавление `RESEARCHER`) — обновить
  brief-test и проверить, что validator не блокирует новые роли
  явно.
- При переходе на DB-level enforcement (например, partial-unique
  constraint на `(tree_id, subject_person_id, scope)` уже есть с
  15.11a — если в 15.11c появится `is_complete()` indexed-view,
  пересмотреть, не выгоднее ли часть проверок мигрировать в DB).

## Ссылки

- ADR-0003 — Audit log strategy (auto-listener для tree-scoped CRUD).
- ADR-0046 — Phase 4.11a user-level audit для GDPR-actions.
- ADR-0076 — Phase 15.11a completeness assertions data model + permissive CRUD.
- ADR-0058 — Tree role hierarchy.
- `services/parser-service/src/parser_service/api/completeness.py` —
  call-site, обновлённый этим PR.
- `services/parser-service/src/parser_service/completeness/validation.py` —
  модуль, добавленный этим PR.
- `feedback_phase_parallelization_alembic.md` — почему 15.11b сознательно
  не добавляет миграцию.
