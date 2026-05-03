"""Pydantic-модели для match-list ingest (Phase 16.3).

Frozen-модели — pure-function парсеры не могут случайно мутировать
результат. Валидация на уровне Pydantic; всё что не вписывается в
shape (отрицательный cM, отсутствующий external_match_id и т.п.) —
ошибка парсинга, а не silent skip.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from shared_models.enums import DnaPlatform, PredictedRelationship


class MatchListEntry(BaseModel):
    """Один распарсенный match из CSV-экспорта платформы.

    Поля выровнены с :class:`shared_models.orm.DnaMatch` (Phase 16.3
    extension); persistence-слой делает 1:1 mapping с минимальной
    логикой. ``raw_payload`` — оригинальная CSV-row dict, сохраняется
    как есть для re-parse при эволюции схемы.

    Anti-drift (ADR-0072): мы НЕ предсказываем родство;
    ``predicted_relationship_raw`` — то что прислала платформа,
    ``predicted_relationship`` — наш канонический bucket из
    :class:`PredictedRelationship` (mapping per-platform внутри парсера).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: DnaPlatform
    external_match_id: str = Field(min_length=1)
    display_name: str | None = None
    match_username: str | None = None

    total_cm: float = Field(ge=0)
    longest_segment_cm: float | None = Field(default=None, ge=0)
    shared_segments_count: int | None = Field(default=None, ge=0)

    predicted_relationship_raw: str | None = None
    predicted_relationship: PredictedRelationship = PredictedRelationship.UNKNOWN
    shared_match_count: int | None = Field(default=None, ge=0)
    notes: str | None = None

    raw_payload: dict[str, Any] = Field(default_factory=dict)
