# Agent brief — Phase 7.2: hypothesis ORM persistence + API

> **Кому:** Агент 2 — после Phase 3.4 + Phase 7.1.
> **Worktree:** `TreeGen-phase34` или новый.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

После Phase 7.1 (ты только что): inference-engine с 5 rules + Daitch-Mokotoff
helper + ISO-9 transliteration. 76 tests, 86% coverage. **Pure functions, no I/O.**

Phase 7.2 — **persistence layer**: hypotheses + evidences хранятся в БД,
queryable через HTTP API. Это где AutoTreeGen получает **research notebook
для гипотез** — возвращаешься к делу через неделю и видишь все ranked
hypotheses + evidences которые система прокрутила.

**Параллельно работают:**

- Агент 1: Phase 4.5 (dedup UI) — потенциально захочет UI для hypotheses в Phase 7.4
- Агент 3: Phase 5.1 FS import (Task 3-4)
- Агент 4: Phase 6.2 DNA service (Task 2-4)
- Агент 5: Phase 1.x gedcom (Task 3-4, медленный)
- Агент 6: Phase 7.0 (Task 3, finalizing)

**Твоя территория:**

- `packages/shared-models/orm.py` — добавить `Hypothesis` + `HypothesisEvidence` ORM
  (КООРДИНИРУЙ с Агентом 4 — он тоже правит orm.py для DnaConsent/DnaTestRecord)
- `infrastructure/alembic/versions/` — новая миграция
- `services/parser-service/src/parser_service/services/hypothesis_runner.py` — новый
- `services/parser-service/src/parser_service/api/hypotheses.py` — новый router
- `services/parser-service/src/parser_service/schemas.py` — добавить `HypothesisResponse`
- `services/parser-service/src/parser_service/main.py` — register router (минимально)
- ADR-0021 — hypothesis persistence design

**Что НЕ трогай:**

- `packages/inference-engine/` — твоё закрытое (Phase 7.1)
- `packages/entity-resolution/` — твоё закрытое (Phase 3.4)
- Чужие пакеты

---

## Цель Phase 7.2

1. **ORM:** `Hypothesis` + `HypothesisEvidence` tables с FK на entities
2. **Service:** `compute_and_persist_hypothesis(tree_id, subject_a, subject_b, type)`
3. **API:**
   - `POST /trees/{id}/hypotheses` — compute new
   - `GET /trees/{id}/hypotheses?subject_id=...&min_confidence=0.5` — query
   - `GET /hypotheses/{id}` — single with full evidence chain
4. **CLI** (опционально) для bulk compute из dedup_finder pairs

---

## Задачи

### Task 1 — docs(adr): ADR-0021 hypothesis persistence

- Контекст: связь между Phase 3.4 dedup_finder, Phase 7.1 rules, Phase 7.2 persistence
- ORM design:

  ```python
  class Hypothesis(Base, TreeOwnedMixins):
      id: UUID
      hypothesis_type: str  # "same_person", "parent_child", etc
      subject_a_type: str   # "person", "source", "place"
      subject_a_id: UUID
      subject_b_id: UUID
      composite_score: float
      computed_at: datetime
      computed_by: str      # "automatic" | "manual" | "imported"
      rules_version: str    # snapshot какие rules были — для reproducibility
      reviewed_status: str  # "pending" | "confirmed" | "rejected"
      reviewed_by_user_id: UUID | None
      reviewed_at: datetime | None
      review_note: str | None
  ```

- `HypothesisEvidence`:

  ```python
  class HypothesisEvidence(Base):
      id: UUID
      hypothesis_id: UUID  # FK
      rule_id: str
      direction: str       # "supports" | "contradicts" | "neutral"
      weight: float
      observation: str
      source_provenance: dict  # JSONB
  ```

- **CLAUDE.md §5 enforcement:** `reviewed_status='confirmed'` НЕ автоматически
  merge'ит entities — требует **отдельного explicit действия** (Phase 4.6 UI или
  manual SQL with audit log)
- Когда пересмотреть: при появлении ML rules — нужно сохранять model version

### Task 2 — feat(shared-models): Hypothesis + HypothesisEvidence ORM

В `packages/shared-models/src/shared_models/orm.py`:

```python
class HypothesisType(str, Enum):
    SAME_PERSON = "same_person"
    PARENT_CHILD = "parent_child"
    SIBLINGS = "siblings"
    MARRIAGE = "marriage"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_PLACE = "duplicate_place"


class HypothesisReviewStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Hypothesis(Base, TreeOwnedMixins):
    __tablename__ = "hypotheses"
    # ... как в ADR
    evidences: Mapped[list["HypothesisEvidence"]] = relationship(
        back_populates="hypothesis", cascade="all, delete-orphan"
    )


class HypothesisEvidence(Base):
    __tablename__ = "hypothesis_evidences"
    # ...
    hypothesis: Mapped["Hypothesis"] = relationship(back_populates="evidences")
```

**КООРДИНАЦИЯ с Агентом 4** (он правит orm.py для DnaConsent/DnaTestRecord):

- `git pull --rebase` перед commit
- Если конфликт — взять обе стороны
- Если Agent 4 ещё не merged — координируй порядок (открой issue в чате)

Migration:

```text
uv run alembic revision -m "add_hypotheses_and_evidences"
```

Tests в shared-models.

### Task 3 — feat(parser-service): hypothesis_runner service

`services/parser-service/src/parser_service/services/hypothesis_runner.py`:

```python
async def compute_hypothesis(
    session: AsyncSession,
    tree_id: UUID,
    subject_a_id: UUID,
    subject_b_id: UUID,
    hypothesis_type: HypothesisType,
) -> Hypothesis:
    """1. Fetch entities from DB
    2. Convert to dict-subjects format (как в Phase 7.1 rules)
    3. compose_hypothesis() из inference_engine
    4. Persist Hypothesis + HypothesisEvidence rows
    5. Return persisted ORM object
    """

async def bulk_compute_for_dedup_suggestions(
    session: AsyncSession,
    tree_id: UUID,
    min_confidence: float = 0.5,
) -> int:
    """Использует Phase 3.4 dedup_finder для генерации pairs,
    каждую конвертирует в Hypothesis. Returns count."""
```

Tests:

- test_compute_zhitnitzky_hypothesis_persists
- test_dedup_to_hypothesis_pipeline
- test_rerun_doesnt_duplicate_hypothesis (idempotency by `(subject_a, subject_b, type)`)

### Task 4 — feat(api): /hypotheses endpoints

`services/parser-service/src/parser_service/api/hypotheses.py`:

```python
@router.post("/trees/{tree_id}/hypotheses", response_model=HypothesisResponse)
async def create_hypothesis(...):
    """POST {subject_a_id, subject_b_id, hypothesis_type}"""

@router.get("/trees/{tree_id}/hypotheses", response_model=list[HypothesisSummary])
async def list_hypotheses(
    tree_id: UUID,
    subject_id: UUID | None = None,
    min_confidence: float = 0.5,
    status: HypothesisReviewStatus | None = None,
    limit: int = 50,
):

@router.get("/hypotheses/{hypothesis_id}", response_model=HypothesisDetailResponse)
async def get_hypothesis(hypothesis_id: UUID):
    """Includes full evidences[] chain"""

@router.patch("/hypotheses/{hypothesis_id}/review", response_model=HypothesisResponse)
async def review_hypothesis(
    hypothesis_id: UUID,
    review: HypothesisReviewRequest,  # {status, note}
):
    """Mark as confirmed/rejected. CLAUDE.md §5: NO auto-merge here.
    Just stores user's judgment."""
```

`schemas.py` дополнить.

`main.py` зарегистрировать router (минимально, аккуратно с Agent 1 параллельно).

Tests на каждый endpoint.

### Task 5 (опционально) — CLI для bulk compute

`scripts/compute_hypotheses.py`:

- Читает tree_id из argv
- Запускает `bulk_compute_for_dedup_suggestions`
- Печатает summary

Полезно для batch processing после import большого GED.

---

## Что НЕ делать

- ❌ Auto-merge entities при `status=confirmed` (CLAUDE.md §5)
- ❌ Modify other entities из hypothesis_runner (только READ + INSERT hypothesis rows)
- ❌ ML inference (Phase 10)
- ❌ HTTP API для compose_hypothesis directly из inference-engine (там нет I/O)
- ❌ Web UI (Phase 7.4)
- ❌ Cross-tree hypothesis (within-tree only)
- ❌ `git commit --no-verify`

---

## Сигналы успеха

После 4 PR:

1. ✅ ADR-0021
2. ✅ `Hypothesis` + `HypothesisEvidence` в shared-models с migration
3. ✅ `compute_hypothesis()` создаёт rows, idempotent
4. ✅ `GET /trees/{id}/hypotheses?subject_id=<vlad-uuid>` возвращает list
5. ✅ `PATCH /hypotheses/{id}/review` работает (без side effects на other entities)
6. ✅ Все CI green
7. ✅ Demo: после import + dedup + bulk_compute, твой Vlad node имеет
   N hypotheses pending, ranked by confidence

---

## Coordination

- **shared-models orm.py** — Агент 4 параллельно правит. Strategy:
  - Открой свой PR с migration. Если конфликт с Agent 4 — rebase + взять обе стороны
  - Migration filename: разные timestamps → не должно конфликтовать
- **services/parser-service/main.py** — Agent 3 (FS import router) параллельно. Same strategy
- Worktree isolation: `git worktree add ../TreeGen-phase72 main`

Удачи. После Phase 7.2 у тебя в БД накапливаются **research notes** —
гипотезы с evidence chain. Это где AutoTreeGen становится **research
notebook**, а не просто tree viewer.
