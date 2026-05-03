# ADR-0070: merge-service introduction + polymorphic merge refs

- **Status:** Proposed
- **Date:** 2026-05-02
- **Authors:** @autotreegen
- **Tags:** `merge`, `service-boundary`, `polymorphic-refs`, `sessions`, `audit`,
  `phase-7`

## Контекст

Phase 4.6 (ADR-0022) и Phase 6.4 (ADR-0044) реализовали **single-pair**
person-merge: пользователь приходит на пару кандидатов из duplicate-suggestions,
выбирает survivor, разрешает diff по ≈10 полям, коммитит. Этот flow живёт
в `parser-service/.../person_merger.py` плюс `apps/web/src/app/persons/.../merge`.

Реальный workflow владельца — другой. Он делает экспорты с Ancestry и
MyHeritage (132 person'а пересечения, как в дизайн-моке этого ADR), и
хочет:

1. **Сессионную работу:** «начать сегодня 47/132, продолжить завтра».
2. **Bulk-операции:** «auto-resolve все non-conflict'ы», «применить готовые 38
   персон сейчас, остальное потом».
3. **Per-field, per-person decision storage:** каждое решение явно записано,
   аудитно восстанавливается, опционально откатывается.
4. **Источники merge'а — гетерогенные:** не только «два import'а», но и
   «import vs существующее дерево», и в перспективе «дерево vs snapshot
   дерева».

ADR-0022 backend контракт (`MergeCommitRequest` с одним
`survivor_choice`) для этого недостаточен: ADR-0044 уже зафиксировал это
как known-limitation («majority → survivor_choice»). Растягивать его до
сессионного сценария — это ломать контракт parser-service'а ради
функциональности, которая по domain'у не относится к ingestion'у.

Параллельно идут смежные нагрузки, которым тоже нужна общая
session-orchestration инфраструктура:

- **Phase 10.7a self-anchor** (ADR-0068, merged) — длинные jobs резолва
  ego-relationship'ов после импорта.
- **Будущие dedup flows** — bulk-сравнение нескольких деревьев пользователя.
- **Media reconciliation** — слияние photo-метаданных при импортах
  (Phase 11+).

Все три имеют общий shape: «long-running diff job с persisted-сессией,
интерактивный UI разрешения, batched apply». Это — `merge-service`.

> **Memory note.** Дизайн-обсуждение этого ADR (модель `MergeSession` /
> `MergeDecision` + UI-мок) ведётся в чате с владельцем, не в отдельном
> документе. Решения 1–4 ниже зафиксированы из этого разговора.

### Фиксированные решения (вход в этот ADR)

В переписке владелец явно подтвердил четыре опоры, которые здесь
**не пересматриваются**, а только формализуются:

1. **Только 2-way merge.** N-way взрывает UI и conflict resolution.
   Sequential 2-way (`A → tmp1`, `tmp1 + B → tmp2`, `tmp2 + C → final`) —
   документированный workaround.
2. **Новый сервис `services/merge-service`.** Не parser-service (другой
   domain — ingestion vs reconciliation), не archive-service
   (внешние curated catalogs, ADR-0055), не api-gateway (placeholder).
3. **Полиморфные refs.** `left_ref_kind` / `left_ref_id` +
   `right_ref_kind` / `right_ref_id`, kind ∈ {`imported_doc`, `tree`,
   `snapshot`}.
4. **Scope:** import-vs-tree И tree-vs-tree, snapshot-vs-snapshot
   зарезервирован.

## Рассмотренные варианты

Два независимых axis'а: **где живёт сервис** и **какая форма у ref'ов**.
Решения 2 и 3 владельца закрывают axes выше уровня вариантов; ниже —
формальный разбор того, что осталось.

### Axis 1 — связь с существующим `person_merger`

Per-person merge logic живёт сейчас в parser-service
(`person_merger.compute_diff/apply_merge/undo_merge`). При сессионном
flow merge-service на каждом «mark resolved» / «apply ready 38» должен
исполнять эту же логику.

#### Вариант A — Дублировать person-merge в merge-service

merge-service пишет свой собственный merge-engine, parser-service
оставляет свой.

- ✅ Сервисы изолированы.
- ❌ Двойная имплементация инварианта CLAUDE.md §5 («no auto-merge close-kin»),
  двойные тесты, дрейф семантики через год.
- ❌ ADR-0022 §hypothesis-conflicts становится трудно держать в синхроне.

#### Вариант B — IPC: merge-service → parser-service per-person

merge-service на каждое решение делает HTTP-вызов в parser-service.

- ✅ Минимум изменений в parser-service.
- ❌ Транзакционность через сеть невозможна. «Apply ready 38» = 38
  сетевых merge'ев, любой может упасть посередине, и сессия становится
  частично применённой без ясного отката.
- ❌ Latency на bulk-apply растёт линейно по N persons (ожидаемое N
  ≈ 100–500 на крупный merge).

#### Вариант C — Извлечь person-merger в `packages/merge-engine`

Pure-functions (`compute_diff`, `apply_merge`, `undo_merge`,
`check_hypothesis_conflicts`) переезжают в новый workspace package.
parser-service продолжает экспонировать single-pair endpoint'ы поверх
этого пакета (ADR-0022 контракт сохраняется). merge-service использует
тот же пакет внутри своих session-endpoint'ов.

- ✅ Один источник правды для merge-семантики.
- ✅ Транзакционный «apply ready 38» возможен — всё в одной DB-транзакции
  внутри merge-service.
- ✅ Соответствует уже существующему паттерну с `packages/inference-engine`,
  `packages/entity-resolution`.
- ❌ Refactor parser-service: выделить, обновить импорты, не сломать
  ADR-0022 контракт. Оценка: ≈300 LOC чистого movement'а + тесты.

### Axis 2 — форма refs

#### Вариант α — Жёсткие колонки `left_doc_id` / `right_doc_id`

Только imported documents. Tree-vs-tree эмулируется через «экспорт
дерева в imported_doc и слияние».

- ❌ Решение 4 владельца требует tree-vs-tree first-class. Эмуляция через
  re-export ломает provenance: tree-rows становятся «ImportedDocs»,
  теряется original `tree_id`.

#### Вариант β — Жёсткие колонки `left_tree_id` / `right_tree_id`

Tree-only. Import-vs-tree эмулируется через «сначала импорт в shadow tree,
потом merge».

- ❌ Удваивает количество tree-рядов в БД ради одной операции merge'а.
- ❌ Shadow tree — это та же tree-row с другим статусом, которая
  засоряет users' UI («у меня 8 деревьев?»).

#### Вариант γ — Полиморфные refs (`kind` enum + `id`)

```python
class MergeRefKind(str, Enum):
    imported_doc = "imported_doc"
    tree = "tree"
    snapshot = "snapshot"  # зарезервировано, не используется в Phase 7
```

`MergeSession` имеет `left_ref_kind / left_ref_id` и
`right_ref_kind / right_ref_id`. Резолв в реальный объект — через
сервисный helper `resolve_ref(kind, id) -> RefHandle` с типизированными
вариантами для каждого kind'а.

- ✅ Решение 4 владельца поддерживается напрямую.
- ✅ Новые kinds добавляются без миграции схемы (только enum + helper).
- ❌ Полиморфизм без FK-constraint'ов — данные требуют application-level
  валидации. Mitigation: CHECK на enum + integration-тесты на
  «kind=tree, id ∈ trees(id)».
- ❌ JOIN'ы становятся conditional. Mitigation: запросы идут через
  service-layer (`MergeRepository`), не напрямую SQL.

## Решение

Выбраны **Axis 1 = Вариант C** (extract в `packages/merge-engine`) и
**Axis 2 = Вариант γ** (полиморфные refs с kind enum).

### Сервис

Новый workspace member `services/merge-service`:

- FastAPI, тот же middleware-стек ADR-0053 (auth via Clerk, rate-limit,
  request-id propagation).
- arq-worker для bulk-операций (`auto_resolve_non_conflicts`,
  `apply_ready_decisions`) — переиспользует Redis-конфиг ADR-0026.
- Owns три новые таблицы:
  - `merge_sessions` (см. модель ниже).
  - `merge_decisions`.
  - `merge_apply_batches` (для partial-apply, см. §«Apply semantics»).
- НЕ owns: persons / events / families / trees — это parser-service /
  shared schema. merge-service читает их через ORM (read-only по факту,
  пишет только при apply через `merge-engine`).

Сервис не размещается в parser-service потому, что:

- parser-service core competency — ingestion (GEDCOM parse, FS import,
  ImportJob orchestration). Reconciliation — другой domain.
- parser-service уже `≈8k LOC` без merge-session логики. Дальнейшее
  раздувание ухудшает blast radius (см. ADR-0055 §A).
- merge-service переиспользует queue + session паттерн для
  Phase 10.7a self-anchor jobs и будущих dedup flows.

### `packages/merge-engine` (extract)

Из `services/parser-service/.../person_merger.py` извлекаются pure-functions
в новый пакет:

```text
packages/merge-engine/
  src/merge_engine/
    __init__.py
    diff.py            # compute_diff(a, b, survivor_choice) -> MergeDiff
    apply.py           # apply_merge(session, diff, confirm_token) -> MergeLog
    undo.py            # undo_merge(session, merge_id) -> MergeLog
    conflicts.py       # check_hypothesis_conflicts(session, a_id, b_id)
    types.py           # MergeDiff, MergeLog, MergeFieldDiff, ...
  tests/
    ...
```

`parser-service` продолжает экспонировать ADR-0022 endpoint'ы
(`POST /persons/{id}/merge/preview` etc.) — внутри они становятся
тонкими адаптерами над `merge_engine.*`. Контракт ADR-0022 не меняется:
single-pair API остаётся жить там, где живёт сейчас.

merge-service зависит от `merge-engine` workspace-membership'ом и
вызывает те же функции прямо в своих DB-транзакциях.

### ORM модели (предварительные, итерация на детали в коде PR)

```python
class MergeSession(IdMixin, TimestampMixin, ProvenanceMixin, SoftDeleteMixin, Base):
    __tablename__ = "merge_sessions"

    user_id: Mapped[uuid.UUID]                # FK users
    target_tree_id: Mapped[uuid.UUID]         # FK trees — куда применяем

    left_ref_kind: Mapped[MergeRefKind]
    left_ref_id: Mapped[uuid.UUID]
    right_ref_kind: Mapped[MergeRefKind]
    right_ref_id: Mapped[uuid.UUID]

    status: Mapped[MergeSessionStatus]        # см. ниже
    summary: Mapped[dict] = mapped_column(JSONB)
    last_active_at: Mapped[datetime]

    # CHECK: left_ref_kind != "snapshot" AND right_ref_kind != "snapshot"
    #        (Phase 7 запрещает snapshot — открываем в будущем ADR)


class MergeSessionStatus(str, Enum):
    pending = "pending"             # создана, scoring ещё не пробежал
    in_progress = "in_progress"     # есть decisions, не все resolved
    ready_to_apply = "ready_to_apply"  # все decisions resolved или skipped
    partially_applied = "partially_applied"  # хотя бы один MergeApplyBatch применён
    applied = "applied"             # все decisions либо applied либо skipped
    abandoned = "abandoned"         # user явно отменил


class MergeDecision(IdMixin, TimestampMixin, Base):
    __tablename__ = "merge_decisions"

    session_id: Mapped[uuid.UUID]   # FK merge_sessions
    scope: Mapped[MergeDecisionScope]   # person | relation | source | media
    target_kind: Mapped[str]            # "person" | "family" | ...
    target_id: Mapped[uuid.UUID]        # ID объекта, по которому решение
    field_path: Mapped[str]             # "person.birth.date", "" для scope=person

    chosen_source: Mapped[ChosenSource]  # left | right | both | custom | skip
    custom_value: Mapped[dict | None] = mapped_column(JSONB)

    decision_method: Mapped[DecisionMethod]  # manual | auto | rule:<id>
    decided_by_user_id: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime]
    applied_in_batch_id: Mapped[uuid.UUID | None]   # FK merge_apply_batches


class MergeApplyBatch(IdMixin, TimestampMixin, Base):
    __tablename__ = "merge_apply_batches"

    session_id: Mapped[uuid.UUID]
    person_ids: Mapped[list[uuid.UUID]] = mapped_column(JSONB)  # которые применили
    applied_at: Mapped[datetime]
    applied_by_user_id: Mapped[uuid.UUID | None]
    apply_log_json: Mapped[dict] = mapped_column(JSONB)  # дельта для возможного undo
```

`ProvenanceMixin` и `SoftDeleteMixin` — обязательные по CLAUDE.md §3.
Обе таблицы регистрируются в `SERVICE_TABLES` (`test_schema_invariants.py`)
тем же PR — иначе CI рушится (memory note `feedback_orm_allowlist.md`).

### Семантика `target_tree_id`

| left.kind | right.kind | target_tree_id |
|---|---|---|
| imported_doc | tree | = right tree (импортируем в существующее) |
| tree | imported_doc | = left tree (симметрия) |
| tree | tree | user выбирает: в left, в right, или новое дерево |
| imported_doc | imported_doc | new tree (создаётся в момент session.start) |

Для `tree+tree → new tree` создание дерева происходит на «Apply first
batch», не на session.start — иначе abandoned-сессии плодят пустые
деревья.

### 2-way only — формальный constraint

CHECK-constraint на уровне БД не требуется (схема и так
двух-полная), но **API layer** в merge-service отвергает попытку
открыть третий ref. Sequential 2-way задокументирован в response error:

```json
{
  "detail": {
    "code": "MERGE_NWAY_NOT_SUPPORTED",
    "hint": "merge into intermediate tree, then merge that with next source",
    "docs_url": "/docs/merge#sequential-2way"
  }
}
```

### Per-decision granularity (расширение ADR-0044)

ADR-0044 §«UI mapping majority → survivor_choice» был known-limitation
именно потому, что backend не принимал per-field overrides.
**ADR-0070 поднимает этот лимит** для session-flow:

- В session-flow «майоритарка» **не используется**. Каждое поле — отдельный
  `MergeDecision`, явно applied или skipped.
- ADR-0044 single-pair UI остаётся как был: для одной пары без сессии
  по-прежнему majority-rule, потому что ADR-0022 single-pair API не
  меняется. Это сознательный split: добавлять `field_overrides` в
  ADR-0022-контракт — слом совместимости, который не нужен пока
  session-flow покрывает реальный use-case.

### Apply semantics — partial / batched

«Apply ready 38» создаёт `MergeApplyBatch`:

1. Транзакция: для каждой person в batch'е — `merge_engine.apply_merge`.
2. Все `MergeDecision`-row'ы по этим personам получают
   `applied_in_batch_id`.
3. `MergeSession.status` переходит:
   - `partially_applied`, если в session ещё есть unresolved decisions.
   - `applied`, если все decisions либо applied либо skipped.
4. В `apply_log_json` пишется дельта (concat'ed `dry_run_diff_json` от
   ADR-0022 — каждый per-person merge возвращает один). Это даёт
   возможность batched-undo, если в будущем понадобится.

Phase 7 **не реализует** batched-undo. ADR-0022 §90-day undo
работает на уровне отдельных `person_merge_logs`, и пользователь
откатывает каждый pair через существующий UI.

### Аудит и provenance

`MergeDecision.decision_method` — обязательное поле:

- `manual` — user кликнул radio.
- `auto` — bulk «Auto-resolve non-conflicts» (значения совпали побайтово
  после нормализации).
- `rule:<id>` — сработало именованное правило (например,
  `rule:place_canonicalization` свернул «Łódź vs Lodz» на
  canonical place_id). Каталог правил — отдельный YAML в
  `services/merge-service/config/auto_resolve_rules.yaml`.

После apply `provenance.merge_session_id` и
`provenance.merge_decision_ids` пишутся на каждую затронутую domain-
запись (persons / events / families) — это закрывает CLAUDE.md §3
требование «provenance everywhere».

### Skip person

Person-level skip = `MergeDecision(scope="person", field_path="",
chosen_source="skip", target_kind="person", target_id=<person_id>)`.
Один row, не размазанный по полям. UI показывает «skipped — review
later», и они не считаются в `summary.decisions_pending`, но и
не считаются `resolved` для apply-готовности — visually отдельная
колонка.

### Транслитерация и place-канонизация — UI-hints

Из обсуждения мока: «Yossel/Iosif» и «Łódź/Lodz» не должны быть
голым radio. UI получает от `merge_engine.compute_diff` опциональные
`hints[]`:

```python
class MergeFieldHint(BaseModel):
    kind: Literal["transliteration", "place_canonical", "ai_suggestion"]
    message: str  # "Both names normalize via Yiddish↔Russian (engine 15.10)"
    suggested_choice: ChosenSource | None  # "both" для транслитерации
    confidence: float
```

Подсказки **не выбирают за пользователя** (CLAUDE.md §5: no auto-merge
для близкого родства), но снижают cognitive load и направляют на
правильное решение. Phase 7 имплементирует два producer'а: Phase 15.10
multilingual name engine и existing place-gazetteer.

## Последствия

**Положительные:**

- Один источник правды для merge-семантики (`merge-engine`), вместо
  дублей или IPC.
- Session-flow готов к owner'ской реальности: 132-person'овый Ancestry+MH
  merge с pause/resume/partial-apply.
- Полиморфные refs готовы к Phase 11+ (snapshot diff) без миграции
  схемы.
- merge-service становится домом для Phase 10.7a-style long-running
  reconciliation jobs — общий queue + session-orchestration.
- Per-decision audit (`decision_method` + `decided_by_user_id`)
  закрывает gap, который ADR-0044 откладывал на «будущую фазу».
- AJ-endogamy specifics учитываются: hints API даёт name-engine
  возможность пометить «multiple Yossels born ~1880» как требующее
  ручного review до того, как пользователь дойдёт до radio.

**Отрицательные / стоимость:**

- Refactor parser-service: extract `person_merger` в `merge-engine`
  пакет. ≈300 LOC movement + контрактные тесты что ADR-0022 endpoint'ы
  ведут себя идентично. Mitigation: golden-file test snapshot до и
  после, плюс diff проверка в CI.
- Ещё один Cloud Run сервис (cost ≈ archive-service per ADR-0055).
- Полиморфные FK без БД-constraint'а — application-level validation
  обязательна. Mitigation: integration-test «kind=tree, id ∉ trees» →
  400, для каждого kind'а.
- Три новые таблицы — три новых места для денормализации `summary`.
  Mitigation: derive `summary` lazily (computed property), кэш в
  `merge_sessions.summary` JSONB обновляется по триггеру или
  application-side hook.

**Риски:**

- **Race на bulk-apply.** Два user'а одновременно apply'ят пересекающиеся
  персоны (одна и та же person как target в двух session'ах).
  Mitigation: `SELECT ... FOR UPDATE` на person-row'ы внутри batch'а;
  CHECK на уровне merge-engine что `merged_into_person_id IS NULL`
  (ADR-0022 уже это делает).
- **Сложность UI:** 132 persons × ~10 fields = 1320 потенциальных
  decisions. Без keyboard navigation и bulk-rules UX будет
  невыносимым. Mitigation: первый PR обязан включать (a) keyboard
  shortcuts (←/→ navigate persons, 1/2 left/right, b both, s skip),
  (b) рабочее «Auto-resolve non-conflicts». Без них фича непригодна.
- **Persons identity scoring** — допущение, что обе стороны уже
  matched (в моке header «Yossel Levitin (b. ~1880)» подразумевает
  что entity-resolution уже определил это same person). Если
  matching ошибся, UI должен иметь escape «split — they're not the
  same person». Phase 7 имплементирует, мок это не показывал.
- **Token / cost** для AI hints — если hints[] производятся LLM-
  агентом (ADR-0043), bulk-сессии могут жечь Claude-токены. Mitigation:
  hints от детерминированных engines (Phase 15.10 names, place-gazetteer)
  бесплатны; LLM-hint опт-ин per-decision, не bulk.

**Что нужно сделать в коде** (отдельные PR'ы, координированные phase
slot'ом 7.x):

1. `packages/merge-engine/` — extract из parser-service. Тесты-снапшоты
   на ADR-0022 контракт.
2. `services/parser-service/.../person_merger.py` — replace бодем на
   `from merge_engine import ...`. ADR-0022 endpoint'ы остаются.
3. `services/merge-service/` — scaffold (FastAPI + arq + middleware
   ADR-0053).
4. `packages/shared-models/.../orm/merge_*.py` — ORM модели.
   Регистрация в `SERVICE_TABLES`.
5. Alembic-миграция: 3 таблицы + индексы (`session_id`, `target_id`,
   `applied_in_batch_id`, `(left_ref_kind, left_ref_id)`).
6. merge-service routes: `POST /sessions`, `GET /sessions/{id}`,
   `POST /sessions/{id}/decisions` (bulk), `POST /sessions/{id}/auto-resolve`,
   `POST /sessions/{id}/apply` (создаёт batch), `POST /sessions/{id}/abandon`,
   `GET /sessions` (list).
7. Pydantic schemas: `MergeSessionCreate`, `MergeDecisionInput`,
   `MergeFieldHint`, `MergeApplyRequest`, `MergeSessionSummary`.
8. `apps/web/src/app/merge/[sessionId]/page.tsx` — interactive UI по
   моку (header progress, two-column person card, radio per field,
   keyboard shortcuts, bulk-actions toolbar).
9. ROADMAP §11 (Phase 7) — раздел про session-merge добавляется.
10. `docs/merge.md` — пользовательская документация sequential-2way
    workaround'а.

## Когда пересмотреть

- **N-way запрос:** если ≥30% активных пользователей делают 3+
  sequential merge'ей подряд для одного дедупа — пересмотреть отказ от
  N-way (новый ADR с N-column UI).
- **Snapshot diff:** когда Phase 11+ запросит «сравни состояние
  дерева на 1 января vs сейчас», `MergeRefKind.snapshot` снимается с
  reservation, и UI получает третий kind. Это **не** требует нового
  ADR — schema-готова.
- **Per-field в single-pair API:** если ADR-0044 majority-rule
  вызовет жалобы (или session-flow станет полным заменителем
  single-pair), вынести `field_overrides` и в ADR-0022 контракт.
- **`merge-engine` востребованность:** если 12 месяцев пакет
  используется только parser-service'ом и merge-service'ом (т.е.
  никто третий не подключился), переоценить, имеет ли смысл держать
  отдельный пакет vs inline-helper.
- **Cost merge-service'а в проде:** если throughput < 5 sessions/day
  на всю платформу год спустя — рассмотреть слияние обратно в
  parser-service (но с уже extracted `merge-engine`).

## Ссылки

- Связанные ADR: ADR-0003 (versioning), ADR-0015 (entity resolution),
  ADR-0021 (hypothesis persistence), ADR-0022 (person merge backend),
  ADR-0026 (arq jobs), ADR-0044 (person merge UI), ADR-0053 (security
  middleware), ADR-0055 (archive-service split rationale), ADR-0068
  (multilingual name engine — hint producer), ADR-0068 (self-anchor —
  shares queue infra).
- ROADMAP §11 (Phase 7 — Entity Resolution / Дедупликация).
- CLAUDE.md §3 (provenance / versioning), §5 (no auto-merge close-kin).
- Дизайн-обсуждение: чат-сессия 2026-05-02 (модель `MergeSession` +
  UI-мок 132-person Ancestry+MH).
