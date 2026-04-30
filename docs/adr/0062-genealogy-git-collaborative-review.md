# ADR-0062: Genealogy Git — PR-style collaborative review + Protected Tree Mode

- **Status:** Accepted
- **Date:** 2026-05-01
- **Authors:** @vladimir
- **Tags:** `phase-15.4`, `collaboration`, `data-model`, `permissions`

## Контекст

Сейчас любой collaborator с EDITOR-ролью на дереве может мутировать
доменные сущности напрямую (Phase 11.0). Это работает в solo-mode и
small-trust сценариях, но ломается в двух ICP-кейсах:

1. **Pro genealogist** — клиенту нужен audit-trail каждого изменения
   («кто и почему добавил этого предка»), и возможность отклонить
   изменения, не подкреплённые источниками.
2. **Family research где несколько кузенов editing one tree** (типичный
   AJ-сценарий, см. cross-language research) — без protection дерево
   быстро становится contaminated: один кузен пишет «прабабушка из
   Минска», другой — «из Молодечно», третий — без источника, а
   объединить уже не получится.

FamilySearch wiki-style chaos подтверждает: free-for-all editing
несовместим с serious genealogy. Нужен PR-style flow с явным
review-gate'ом.

## Рассмотренные варианты

### Вариант A — Approval-required edits (no diff layer)

Каждый mutation помечается ``pending_approval`` на самой строке
доменной сущности; reviewer нажимает «approve» → строка фиксируется.

- ✅ Минимум новой схемы.
- ❌ Конфликты при concurrent edits становятся unresolvable: две
  pending версии одной строки — какую отдавать read-консьюмерам?
- ❌ Bulk-changes (один импорт = 500 mutations) трудно review'ить
  по одной строке.
- ❌ Невозможно «revert одно изменение, не трогая остальные» —
  все changes flat, нет grouping'а.

### Вариант B — PR-style proposal с diff'ом (Git-like)

Author создаёт ``tree_change_proposal`` с structured ``diff_jsonb``
({creates, updates, deletes} per entity type). Reviewer видит
group changes как unit, approve/reject — атомарно. Owner merge'ит →
diff применяется к дереву одной транзакцией + audit_log entry.

- ✅ Mental model знаком всем (GitHub PRs).
- ✅ Bulk-changes group'ятся естественно.
- ✅ Atomic merge → atomic rollback (15.4c).
- ✅ Evidence требуется к proposal'у, не к каждой строке отдельно.
- ❌ Дополнительный layer схемы (2 новые таблицы + ALTER trees).
- ❌ Diff-engine (15.4c) — нетривиальный код для validation +
  conflict detection.

### Вариант C — Real-time collab (CRDT)

Google-Docs-style: все правки видны мгновенно, conflict resolution —
автоматический через CRDT (Yjs / Automerge).

- ✅ Best UX для co-located кузенов.
- ❌ Не решает «протекта от contamination» — ровно противоположное:
  ускоряет распространение неправильных правок.
- ❌ CRDT для реляционной модели с FK — research-grade проблема,
  не shippable за разумное время.
- ❌ Нет audit-trail в форме «вот этот PR с этим источником одобрил
  reviewer X» — это центральная ценность для pro-genealogist ICP.

## Решение

Выбран **Вариант B** (PR-style proposal с diff'ом).

Структура — два новых table'а в ``SERVICE_TABLES`` (audit/workflow,
не tree-entities):

- ``tree_change_proposals`` — заголовок (title/summary) + diff_jsonb +
  state machine ``open → approved/rejected → merged → rolled_back`` +
  audit-pointer ``merge_commit_id → audit_log(id)``.
- ``tree_change_proposal_evidence`` — many-to-many с ``sources``,
  с opaque ``relationship_ref`` jsonb (caller указывает, какой change
  support'ит этот источник).

Плюс ``trees.protected boolean default false`` + ``protection_policy
jsonb default '{}'`` — opt-in флаг и политика, которая enforced'ится
endpoint'ами Phase 15.4b/c.

## Зафиксированные подрешения (из обсуждения PR'а)

### `author_user_id` — UUID FK на `users.id`, не Clerk-id text

Бриф изначально предлагал ``author_user_id text`` (Clerk-id напрямую),
но это:

- теряет CASCADE on user delete (GDPR-erasure cleanup ломается);
- теряет FK-целостность (можно вписать произвольную строку);
- расходится с паттерном по всему codebase (где user-references —
  всегда UUID FK через ``parser_service.services.user_sync.get_user_id_from_clerk``).

Берём ``UUID FK users(id)`` для author/reviewer/rolled_back_by;
``ondelete=CASCADE`` для author (его proposals удаляются с ним),
``SET NULL`` для reviewer/rolled_back_by (исторический proposal
переживает удаление reviewer'а).

### Allowlist — `SERVICE_TABLES`, не `TREE_ENTITY_TABLES`

Эти таблицы — audit/workflow log: state machine в собственной
``status``-колонке, нет ``provenance``/``version_id``/``confidence_score``/
``deleted_at``. ``test_schema_invariants.py`` ожидает у tree-entities
полный mixin-набор; положить туда proposals — упасть на missing fields.

### Protection mode default = `false`

Solo users не получают friction. Power users явно включают через UI
toggle (15.4d) и настраивают policy.

### Evidence-required gate semantics

Auto-population происходит на ``POST /proposals``: если дерево
protected И ``policy.require_evidence_for`` не пуст — проходим по
``diff.creates`` и ``diff.updates``, и для каждого change с
``kind/relation_kind ∈ require_evidence_for`` кладём
``EvidenceRequirement(relationship_id, kind)`` в ``evidence_required``.

Validation на approve (Phase 15.4b): для каждого item из
``evidence_required`` должен существовать хотя бы один
``tree_change_proposal_evidence`` row с совпадающим
``relationship_ref``. Иначе 422 «Evidence missing for relationship X».

Reasoning: caller (UI) сразу видит при создании proposal'а, какие
sources надо приаттачить; gate'ится на approve, не на create — author
может сохранить draft и потом донабрать evidence.

### Rollback strategy — ОТКРЫТО до 15.4c

Три варианта (см. бриф):

- **A:** в ``audit_log`` row класть и forward, и reverse diff
  (self-contained replay).
- **B:** ``tree_change_proposals.pre_merge_snapshot jsonb`` — full
  snapshot затронутых entities перед merge.
- **C:** Compute inverse on-demand (хрупко при последующих
  изменениях).

Не решаем здесь — нужна week-of-design в 15.4c с прототипом
diff-engine'а. Текущая схема уже имеет ``rolled_back_at`` /
``rolled_back_by_user_id`` колонки; конкретный механизм добавит
либо новую колонку (B), либо разрастёт audit_log payload (A) —
ни то, ни то не breaking change для 15.4a contract'а.

## Последствия

**Положительные:**

- Pro-genealogist ICP получает audit-trail и evidence-gate.
- Multi-cousin family research получает protection от contamination.
- Pattern переносится на другие domain-objects (DNA-merge proposals,
  source-edit proposals) без новой инфраструктуры.

**Отрицательные / стоимость:**

- ~600 LOC на 15.4a (data model + bare CRUD + scaffold api-gateway).
- 15.4c diff-engine — отдельная неделя дизайна и реализации.
- Frontend (15.4d) — split-pane diff viewer, evidence attach UI,
  permission tooltips — ещё ~800 LOC.

**Риски:**

- Adoption: solo users могут счесть PR-flow over-engineered. Mitigation:
  default off, opt-in toggle, empty-state CTA «Tree is open — direct
  edits enabled».
- Diff-engine ambiguity при concurrent edits: 15.4c может потребовать
  вернуться к этой ADR с решением по conflict-strategy.

**Что нужно сделать в коде (Phase 15.4 split):**

- **15.4a (this commit):** alembic 0028, 2 ORM, новый services/api-gateway,
  POST/GET endpoint'ы CRUD-уровня. Тесты + ADR + ROADMAP.
- **15.4b:** approve / reject / evidence attach + permission boundaries.
- **15.4c:** atomic merge engine (diff → tree mutations + audit_log) plus
  rollback (с зафиксированной по варианту A/B/C strategy).
- **15.4d:** frontend (proposals list, diff viewer, evidence panel,
  protected-tree badge).

## Когда пересмотреть

- Если pro-genealogist feedback показывает, что approval-without-merge
  состояние нерелевантно (skip directly open → merged) — упростить
  state machine.
- Если CRDT becomes shippable infrastructure (Yjs/Automerge для
  реляционных данных) — рассмотреть как complement (real-time для
  draft-edits, PR-flow для финализации).
- Если Phase 15.4c показывает, что atomic-rollback требует
  fundamental изменений в audit_log shape — extension или suppression
  этого ADR.

## Ссылки

- Связанные ADR: ADR-0003 (versioning strategy), ADR-0036 (Phase 11.0
  permissions / tree_memberships), ADR-0033 (Clerk authentication).
- Внешние: GitHub Pull Request data model, FamilySearch wiki-edit
  contamination case studies.
