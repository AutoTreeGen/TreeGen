# ADR-0077: GEDCOM Fantasy Filter — rule-based fabrication detection

- **Status:** Accepted
- **Date:** 2026-05-03
- **Authors:** @autotreegen
- **Tags:** `gedcom-doctor`, `data-quality`, `phase-5`

## Контекст

Genealogy databases пропитаны viral fabrications — multi-researcher
confirmed cases типа Voikhansky, который propagated через MyHeritage.
Owners doing serious research need a tool that says: "this branch имеет
N high-severity fantasy flags — review before trusting".

Closes the GEDCOM Doctor stack pre-split:

- 5.6: compatibility simulator (#190 ✓)
- 5.8: validator (#192 ✓)
- 5.9: export audit (#188 in flight)
- **5.10 (this ADR):** fantasy filter — final element

Brief assumed a separate `gedcom-doctor` package and `tree-service`. На
момент implementation main содержит:

- 5.6 в `packages/gedcom-parser/src/gedcom_parser/compatibility/`
- 5.8 в `packages/gedcom-parser/src/gedcom_parser/validator/`
- Trees/import/jobs живут в `services/parser-service`

Поэтому 5.10 укладывается симметрично: code в
`packages/gedcom-parser/src/gedcom_parser/fantasy/`, endpoints в
`services/parser-service/src/parser_service/api/fantasy.py`.

## Рассмотренные варианты

### Detector strategy

#### Вариант A — Rule-based детерминированные правила (SELECTED)

- ✅ Deterministic, debuggable, explainable.
- ✅ Zero ML dependency surface (sklearn / torch / embeddings).
- ✅ Per-rule severity / confidence calibration без training data.
- ❌ Requires manual rule design; coverage of "creative" fabrications
   ограничена.

#### Вариант B — ML classifier (rejected)

- ❌ Opaque (для сложных models). User не сможет интерпретировать "why
   flagged".
- ❌ Training data risk: corpus biased toward Western European trees,
   bias risk reproducing detection asymmetries.
- ❌ Fairness concerns: имена / соц-знаки персон могут попасть в
   feature set, даже непреднамеренно.

#### Вариант C — Внешний service (FamilySearch research wiki / etc) (rejected)

- ❌ Privacy (sending tree to 3rd party).
- ❌ Dependency cost + cost-per-call.
- ❌ Coverage не соответствует Eastern European Jewish genealogy
   focus проекта.

#### Вариант D — Auto-purge fabrications (rejected, anti-drift)

- ❌ Destructive. Owner explicitly does NOT trust auto-mutation.
- ❌ False positives = data loss → unacceptable risk.

### Storage strategy

#### Вариант A — Standalone `fantasy_flags` service-table (SELECTED)

- ✅ Mirror других service-tables (`hypothesis_compute_jobs`,
  `audit_log`, etc).
- ✅ Dismiss-lifecycle через ``dismissed_at`` без SoftDeleteMixin
  (миксин подключил бы audit-listener — flag-mutation как domain change).
- ✅ FK CASCADE от ``trees.id`` — cleanup при удалении дерева.
- ❌ Brief предложил TREE_ENTITY_TABLES — корректировка к
  SERVICE_TABLES задокументирована в ORM-modulу.

#### Вариант B — JSONB column on persons (rejected)

- ❌ N×M denorm: один person может быть subject нескольких rules,
  персистить по-rule structurally cleaner.
- ❌ Filter "high-severity flags by tree" становится JSONB-array
  scan.

### Sync vs async scan

#### Вариант A — Synchronous POST (SELECTED для v1)

- ✅ Простота. Owner получает summary немедленно.
- ✅ 30k-person scan ≈ <10 секунд CPU (verified on Voikhansky-rich
  Ancestry.ged fixture, 35,203 persons + 12,144 families).
- ❌ Запрос блокируется на время scan'а — для very large trees
  (>200k) может timeout HTTP-клиент.

#### Вариант B — arq async с 202 (deferred)

- ✅ Brief specified contract.
- ❌ Brief priority — deliverable v1; async overhead не оправдан
  для типичного 5-50k tree size.
- ⏳ "When to revisit": если average scan > 60 секунд — переключаемся.

## Решение

1. **Rule-based v1 — 12 правил:** impossible_lifespan, birth_after_death,
   child_before_parent_birth, parent_too_young/old_at_birth,
   death_before_child_birth (mother + father),
   identical_birth_year_siblings_excess,
   suspicious_generational_compression,
   direct_descent_from_pre_1500_named_figure, mass_fabricated_branch,
   circular_descent.
2. **Storage:** `fantasy_flags` table в SERVICE_TABLES allowlist
   (Phase 5.10 / alembic 0039). NOT TreeEntity.
3. **Endpoints:** sync POST `/trees/{id}/fantasy-scan`, GET, dismiss,
   undismiss. Все в parser-service.
4. **Confidence cap:** ``MAX_CONFIDENCE = 0.95`` даже для critical-rules.
5. **No mutation:** rules — pure-функции от ``GedcomDocument``;
   ``test_no_mutation_invariant`` doc snapshot equality после scan.

### Explicit non-goals

- ❌ ML / embedding / training в v1.
- ❌ **Surname / ethnicity / origin heuristics** — bias-risk эксплицитный
  non-goal. ``known_fabrication_anchors.yaml`` — narrow whitelist
  multi-researcher confirmed historical figures (Charlemagne, Genghis,
  ..., библейские), **не** ethnic / surname filter.
- ❌ Auto-mutation / auto-delete пользовательских данных. Только flagging.
- ❌ Frontend в этом PR — отдельный post-Geoffrey-demo ticket.
- ❌ Внешние services (FamilySearch wiki / etc).
- ❌ Confidence > 0.95 даже на critical-rules.
- ❌ Claim "fabrication confirmed" — language только "potential" /
  "suspicious" / "implausible".

## Последствия

### Положительные

- Tooling для serious research: "tree X has 7 critical impossibilities" —
  немедленный quality signal.
- Закрывает GEDCOM Doctor stack (5.6/5.8/5.9/5.10).
- Pluggable: add-on правил — single-file PR без schema change.

### Отрицательные / стоимость

- **False positives expected.** Rule thresholds — heuristics не
  ground-truth. Mitigated by:
  1. Severity gradation (info / warning / high / critical).
  2. Dismiss-lifecycle (user marks как false positive с reason).
  3. Owner-tunable thresholds в `gedcom_parser.fantasy.rules.*` файлах.
- **DB-load для scan:** 4-query single-pass (persons + families +
  family_children + birth/death events). Для 50k-person tree — несколько
  MB-of-rows; intermittent admin-сканы приемлемы. Мониторинг p95
  scan-time — Phase 5.10b candidate.
- **Cross-service ORM coupling:** `shared_models.orm.fantasy_flag` lives
  в shared, parser-service импортирует. Schema changes требуют sync
  alembic + ORM update.

### Риски

- **Mass false-positive on legit-but-unusual lineages** —
  verified 122-year-old, immigrant date gaps, late-life IVF
  paternity. Mitigated:
  1. Calment limit (122) +-strict; HIGH only above; CRITICAL > 130.
  2. Father grace = 1y posthumous birth window.
  3. Mother >55 / father >80 — WARNING (not HIGH/CRITICAL).
  4. Confidence cap 0.95 leaves 5% room.
- **Scope creep к 13-му правилу.** Anti-drift: PR shipped 12 rules,
  no exceptions.
- **Anchor list maintenance.** `known_fabrication_anchors.yaml`
  вручную; обновления — отдельный chore PR. Rule корректно skips
  если файл missing (defensive load).

### Что нужно сделать в коде

- ✅ ORM `FantasyFlag` + `FantasySeverity` enum.
- ✅ Alembic 0039 + SERVICE_TABLES allowlist.
- ✅ `gedcom_parser.fantasy` module: types, engine, 12 rules, anchor data.
- ✅ DB-backed `TreeView` adapter + `execute_fantasy_scan`.
- ✅ FastAPI router `/trees/{id}/fantasy-*`.
- ✅ Tests: 29 unit, 4 golden (Voikhansky-rich Ancestry.ged), 3 alembic
  smoke, 8 endpoint round-trip.

## Когда пересмотреть

- **ML candidate trigger:** если false-positive rate > 25% в
  user-dismissed flag stats over 90 days. Then prototype shallow
  classifier (logistic on rule-output features), not deep model.
- **Async scan trigger:** if average POST `/fantasy-scan` latency
  exceeds 60 seconds на production traffic. Then replace sync handler
  with arq enqueue + return 202.
- **Schema rev:** when adding `fantasy_scans` job-tracking table
  (для polling status). Currently scan_id — UUID4 ephemeral; if UI
  needs persistent scan history, materialise.
- **Frontend ticket:** banner + person-card flag affordance (separate
  post-demo).
- **Per-tree config:** when owner-tunable thresholds (Calment limit,
  young-parent threshold) become user-facing — table `fantasy_config`
  with tree-scoped row.

## Ссылки

- Связанные ADR: ADR-0003 (versioning + schema invariants),
  ADR-0033 (Clerk auth), Phase 5.8 validator (no ADR — sibling
  module).
- Brief: `docs/briefs/phase-5-10-fantasy-filter.md`
- Origin: Voikhansky case — multi-researcher confirmed fabrication
  propagated через MyHeritage.
