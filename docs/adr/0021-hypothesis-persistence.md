# ADR-0021: Hypothesis persistence — research notebook для inference-engine

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `inference-engine`, `persistence`, `hypotheses`, `phase-7`

## Контекст

После Phase 7.1 inference-engine считает composite scores с evidence-chain
in-memory: rules → `Evidence` → `compose_hypothesis()` → `Hypothesis`.
Pure-functions, никакого I/O — это сознательный design (ADR-0016).

Workflow владельца за неделю реальной работы:

1. Импортирует Ancestry GED + MyHeritage GED + свой собственный.
2. Запускает dedup_finder (Phase 3.4) → 200 пар-кандидатов.
3. Просматривает первые 10, остальное «потом разберусь».
4. Через неделю возвращается к делу — и заново гоняет dedup, потому что
   нигде не сохранено что он уже посмотрел и решил.

Phase 7.1 hypotheses живут только в памяти запроса. Без persistence:

- Нельзя пагинировать гипотезы (recompute on every page load).
- Нельзя помечать «отверг», «подтвердил» — нет identity у гипотезы.
- Нельзя строить research log, как наука работает: «вот что я думал
  две недели назад, вот evidence chain, вот рассмотренные альтернативы».
- Нельзя версионировать rules: если завтра рулы изменятся, то старые
  суждения уже нельзя воспроизвести.

Phase 7.2 — превратить гипотезы в **persistent research notes** с
identity, audit-trail, review-status и snapshot'ом версии правил.

**Жёсткое правило (CLAUDE.md §5):** confirm hypothesis ≠ auto-merge.
Если user пометил «это same person», system **не** мержит entities.
Слияние — отдельное явное действие (Phase 4.6 UI, отдельный endpoint
с audit-log записью). См. §«Что НЕ делать» брифа Phase 7.2.

## Рассмотренные варианты

### Вариант A — Только in-memory (Phase 7.1 status quo)

Не персистим, каждый раз recompute из dedup_finder.

- ✅ Минимум кода.
- ❌ Нельзя помечать просмотренное / подтверждённое.
- ❌ Кеш разогреть — секунды на recompute большого дерева.
- ❌ Нет audit trail: «когда у нас было это suggestion?».

### Вариант B — Денормализованный JSON в одной таблице

Hypothesis-row держит весь evidence-chain как JSONB:

```sql
hypotheses(id, type, score, evidences jsonb, ...)
```

- ✅ Один INSERT вместо двух.
- ❌ Невозможно индексировать по rule_id (нужен per-evidence фильтр в UI).
- ❌ Schema-evolution evidence-структуры → painful миграция JSONB.
- ❌ При persisted version mismatch'е (Phase 7.1 rule_id vs новая
  версия) теряется provenance.

### Вариант C — Нормализованные `hypotheses` + `hypothesis_evidences`

Две таблицы с FK. Каждый Evidence — своя строка с `rule_id`,
`direction`, `weight`, `observation`, `source_provenance` (JSONB).

- ✅ Индексы на `rule_id` (UI: «покажи все гипотезы где `surname_dm_match`
  не сработал»).
- ✅ Эволюция rules без ломки старых записей: schema стабильна, а
  сам rule_id просто становится "deprecated" в коде.
- ✅ Evidence-rows можно отдельно дёргать в research log без полной
  загрузки гипотезы.
- ❌ Два INSERT'а (hypothesis + 4-6 evidences). Незначительно для
  pairwise-вычислений; на bulk сценариях batch-insert.
- ❌ Чуть больше кода.

### Вариант D — Event-sourcing: журнал событий "rule fired Evidence(X)"

Все события rule-fire'ов сохраняются в `inference_events` table,
гипотеза — производная projection.

- ✅ Полный audit (когда какое правило что произвело).
- ❌ Сильно overengineered для текущих нужд: у нас нет
  multi-step inference, lambda-architectures, aggregate state.
- ❌ Phase 7.4 LLM-rules, если появятся, добавят сложность —
  отложим event-sourcing на потом.

## Решение

Выбран **Вариант C**.

### ORM design

```python
class HypothesisType(StrEnum):
    SAME_PERSON = "same_person"
    PARENT_CHILD = "parent_child"
    SIBLINGS = "siblings"
    MARRIAGE = "marriage"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_PLACE = "duplicate_place"


class HypothesisReviewStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Hypothesis(TreeEntityMixins, Base):
    __tablename__ = "hypotheses"
    hypothesis_type: str
    subject_a_type: str   # "person" | "source" | "place" | "family"
    subject_a_id: UUID
    subject_b_type: str
    subject_b_id: UUID
    composite_score: float
    computed_at: datetime
    computed_by: str       # "automatic" | "manual" | "imported"
    rules_version: str     # snapshot for reproducibility
    reviewed_status: str
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None

    evidences: relationship → list[HypothesisEvidence]


class HypothesisEvidence(IdMixin, TimestampMixin, Base):
    __tablename__ = "hypothesis_evidences"
    hypothesis_id: UUID  # FK CASCADE
    rule_id: str
    direction: str       # "supports" | "contradicts" | "neutral"
    weight: float
    observation: str
    source_provenance: dict  # JSONB
```

Особенности:

- Hypothesis наследует `TreeEntityMixins` (id / tree_id / status /
  confidence / provenance / version / timestamps / soft-delete) —
  как все остальные доменные записи. `composite_score` дополнительно
  к `confidence_score` (для гипотез это разные семантики: composite
  считается rules, confidence отражает user-judgment после review).
- Полиморфные subject FK: `subject_a_type` + `subject_a_id`. То же
  что у `Citation` / `EntityMultimedia`. Целостность — на уровне
  приложения. Это OK потому что «гипотеза про person и source» —
  разные типы entity.
- `rules_version` — opaque строка, заполняется hypothesis_runner'ом
  из inference_engine.**version** + хеш registry состояния. Это
  даёт reproducibility: «какие правила были включены when this score
  was computed». При апгрейде rules старые гипотезы помечены старой
  версией.
- `reviewed_*` — независимый трек user-judgment. **Не** мутирует
  доменные сущности. CLAUDE.md §5: confirm не вызывает `entity merge` —
  это отдельный manual flow Phase 4.6.

### Idempotency

Уникальный index по `(tree_id, hypothesis_type, subject_a_id,
subject_b_id)` (с canonical-ordered ids — меньшее первое). Re-run
`compute_hypothesis()` для той же пары:

- Если запись существует и rules_version совпадает — возвращаем как
  есть (no-op).
- Если запись существует но rules_version отличается — обновляем
  `composite_score`, `evidences`, `rules_version`, `computed_at`.
  Существующий `reviewed_status` сохраняется (user'ское суждение
  осталось его, даже если score изменился — добавим warning в UI).
- Если записи нет — insert.

Это бережёт user'ские review notes при апгрейдах правил.

### Performance

Индексы:

- `(tree_id, hypothesis_type)` — для фильтра по типу.
- `(tree_id, composite_score DESC)` — top-N hypotheses в UI.
- `(tree_id, subject_a_id)` и `(tree_id, subject_b_id)` — все
  гипотезы про одну персону.
- `(reviewed_status)` — pending count для значка в UI.
- `hypothesis_evidences(hypothesis_id)` — eager-load всей цепочки.

На дереве в 10k персон с dedup-suggestions ≥ 0.50: ожидается ~3–5k
гипотез. Каждая держит 3–6 evidences = 15–30k evidence-rows. Это
ничтожно для Postgres, индексы пойдут в RAM.

## Последствия

**Положительные:**

- User возвращается к работе через неделю и видит ranked hypotheses
  с разметкой «pending / reviewed / rejected». Research notebook
  experience.
- API может пагинировать без recompute.
- Audit-trail: каждое user-решение помечено `reviewed_by_user_id` +
  `reviewed_at` + опциональной `review_note`.
- Reproducibility через `rules_version` snapshot.
- Phase 7.4 (UI) бесплатно получает persistent state.

**Отрицательные / стоимость:**

- ~300 строк ORM + миграция + ~150 строк service layer.
- Новые индексы → +2–3% storage. Незначительно.
- Когда правила меняются — дрейф между сохранённой `rules_version` и
  текущим состоянием registry. UI должен показать "stale" значок.

**Риски:**

- User может за неделю натык накапиить тысячи гипотез из bulk-compute.
  Mitigation — UI с пагинацией + min_confidence фильтр (default 0.5)
  - статус-фильтр.
- `rules_version` snapshot может не покрыть всю изменчивость
  (например, изменения в DM table). Mitigation — при serious changes
  bumpанем version по semver и UI пометит старые гипотезы recompute-able.
- Полиморфные subject FK — нет referential integrity на уровне БД.
  Mitigation — явный CHECK что subject_a_type ∈ {"person", "source",
  "place", "family"}. Application-level проверка существования при INSERT.

**Что нужно сделать в коде:**

1. `packages/shared-models/src/shared_models/orm/hypothesis.py` — новый
   файл с двумя ORM моделями + 2 Enum'а в `enums.py`.
2. Migration в `infrastructure/alembic/versions/` (отдельный timestamp,
   не конфликтует с Agent 4).
3. `services/parser-service/src/parser_service/services/hypothesis_runner.py` —
   `compute_hypothesis()` + `bulk_compute_for_dedup_suggestions()`.
4. `services/parser-service/src/parser_service/api/hypotheses.py` —
   POST/GET/PATCH endpoints.
5. `services/parser-service/src/parser_service/schemas.py` — response DTOs.
6. `main.py` — register router (минимально, осторожно с merge'ами).

## Когда пересмотреть

- Если появятся ML-rules с большой serialized model state (Phase 10) —
  нужно сохранять model checkpoint id в `rules_version`. Возможно
  отдельная таблица `inference_model_versions`.
- Если число гипотез на дерево > 100k — пересмотреть индексы и
  pagination keyset (текущий offset на 100k будет медленный).
- Если появятся multi-subject hypotheses (3+ subjects, gen-3 family
  identification) — `subject_*_id` колонок мало, нужна `hypothesis_subjects`
  many-to-many.
- Если cross-tree hypothesis потребуется (например, public-tree
  matches) — отдельный ADR. Текущий scope строго within-tree.

## Ссылки

- ADR-0015 — entity-resolution scoring (Phase 3.4), читает гипотезы.
- ADR-0016 — inference-engine architecture (Phase 7.0).
- Phase 7.1 brief — rules implementation.
- Phase 7.2 brief — текущая фаза.
- CLAUDE.md §5 — запрет auto-merge персон.
- Phase 4.6 (TBD) — UI для review hypotheses.
