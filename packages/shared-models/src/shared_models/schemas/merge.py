"""Pydantic v2 DTO для merge-сессий (Phase 5.7c-a / ADR-0070).

Идут в составе ``shared_models``, потому что merge-service потребляет
их в эндпоинтах, parser-service — для совместимости single-pair API
(ADR-0022 контракт сохраняется), а apps/web — через сгенерированные
TypeScript-типы. Wire-уровень — стабильный, поэтому общий пакет, не
internal-symbol сервиса.

Phase 5.7c-a — schemas only, без route-стороны (`services/merge-service`
scaffold будет в следующем PR).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import Field

from shared_models.orm.merge_session import (
    ChosenSource,
    DecisionMethod,
    MergeDecisionScope,
    MergeRefKind,
    MergeSessionStatus,
)
from shared_models.schemas.common import SchemaBase


class MergeRef(SchemaBase):
    """Полиморфная ссылка на одну сторону merge'а.

    ``kind`` определяет таблицу-источник: ``imported_doc`` →
    ``import_jobs``, ``tree`` → ``trees``, ``snapshot`` → reserved
    (Phase 7 запрещает на API-уровне; см. ADR-0070).

    ``id`` — UUID объекта в соответствующей таблице. FK на DB-уровне
    нет: валидация через ``resolve_ref`` в merge-service.
    """

    kind: MergeRefKind
    id: uuid.UUID


class MergeSessionCreate(SchemaBase):
    """Тело ``POST /sessions`` — открыть новую merge-сессию.

    ``target_tree_id`` опционально:

    - imported_doc + tree → автоматом тот ``tree``;
    - tree + tree → user выбирает (или None — отложить до first apply);
    - imported_doc + imported_doc → None (создаётся новое дерево на
      session.start, ADR-0070 §«Семантика target_tree_id»).
    """

    left: MergeRef
    right: MergeRef
    target_tree_id: uuid.UUID | None = None


class MergeSessionSummary(SchemaBase):
    """Лёгкий summary для list-view ``GET /sessions`` и dashboard'а.

    Цель — UI не делает агрегатных запросов: ``decisions_pending`` и
    ``decisions_resolved`` лежат денормализованно в
    ``merge_sessions.summary`` JSONB и обновляются application-side hook'ом.
    """

    id: uuid.UUID
    status: MergeSessionStatus
    left: MergeRef
    right: MergeRef
    target_tree_id: uuid.UUID | None
    decisions_pending: int = 0
    decisions_resolved: int = 0
    decisions_skipped: int = 0
    last_active_at: dt.datetime
    created_at: dt.datetime


class MergeSessionRead(MergeSessionSummary):
    """Полный read-DTO для ``GET /sessions/{id}``.

    ``summary`` пересылается raw-jsonb'ом — UI читает оттуда специфичные
    для конкретной сессии счётчики (например, ``conflicts_by_field``)
    без расширения wire-контракта.
    """

    summary: dict[str, Any] = Field(default_factory=dict)


class MergeFieldHint(SchemaBase):
    """UI-подсказка для одного поля на decision-screen'е.

    Подсказки **не выбирают за пользователя** (CLAUDE.md §5: no
    auto-merge для близкого родства), но снижают cognitive load.

    - ``transliteration``: пара значений нормализуется через
      multilingual name engine (Phase 15.10) — типично
      ``suggested_choice='both'``.
    - ``place_canonical``: place-gazetteer считает значения одним и тем
      же местом — типично ``suggested_choice='left'``/``'right'`` на
      каноническом написании.
    - ``ai_suggestion``: LLM-hint (опт-ин per-decision, не bulk —
      ADR-0070 §«Риски: Token / cost»).
    """

    kind: Literal["transliteration", "place_canonical", "ai_suggestion"]
    message: str
    suggested_choice: ChosenSource | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class MergeDecisionInput(SchemaBase):
    """Один decision при ``POST /sessions/{id}/decisions``.

    Bulk-приём — вызывается с массивом таких объектов. Транзакция
    создаёт все ``MergeDecision``-row'ы атомарно и обновляет
    ``MergeSession.summary``.

    Если ``decision_method == 'rule'``, ``rule_id`` обязан быть
    указан (CHECK на DB-уровне дублирует это требование, см.
    ``ck_merge_decisions_rule_id_consistency``).
    """

    scope: MergeDecisionScope
    target_kind: str
    target_id: uuid.UUID
    field_path: str = ""
    chosen_source: ChosenSource
    custom_value: dict[str, Any] | None = None
    decision_method: DecisionMethod = DecisionMethod.MANUAL
    rule_id: str | None = None


class MergeDecisionRead(MergeDecisionInput):
    """Read-DTO одного решения. Совмещает input-поля + meta."""

    id: uuid.UUID
    session_id: uuid.UUID
    decided_by_user_id: uuid.UUID | None = None
    decided_at: dt.datetime
    applied_in_batch_id: uuid.UUID | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class MergeApplyRequest(SchemaBase):
    """Тело ``POST /sessions/{id}/apply`` — зафиксировать batch.

    ``person_ids`` — какие именно persons применить в этом батче. Пустой
    массив = «применить всё ready_to_apply» (удобство клиента).
    """

    person_ids: list[uuid.UUID] = Field(default_factory=list)


class MergeApplyBatchRead(SchemaBase):
    """Read-DTO одного зафиксированного batch'а."""

    id: uuid.UUID
    session_id: uuid.UUID
    person_ids: list[uuid.UUID]
    applied_at: dt.datetime
    applied_by_user_id: uuid.UUID | None = None
    created_at: dt.datetime


__all__ = [
    "MergeApplyBatchRead",
    "MergeApplyRequest",
    "MergeDecisionInput",
    "MergeDecisionRead",
    "MergeFieldHint",
    "MergeRef",
    "MergeSessionCreate",
    "MergeSessionRead",
    "MergeSessionSummary",
]
