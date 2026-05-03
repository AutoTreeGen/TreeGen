"""Engine output contract для Phase 26.1 evaluation harness.

Контракт между ``inference_engine.engine.run_tree`` и runner'ом
``scripts/run_eval.py`` / future API consumers. Каждый детектор (Phase 26.2+)
emitter'ит один или несколько списков этой схемы.

ADR-0084 фиксирует семантику полей. Схема намеренно permissive
(``extra="allow"`` на nested-моделях), чтобы детекторы могли постепенно
обогащать payload без bump'ания версии — но top-level keys строго
обязательны: runner полагается на их наличие при scoring.

Поля top-level:

- ``tree_id`` — id из tree JSON, должен round-trip'ить.
- ``engine_flags`` — flat list of machine-readable flag strings.
  Phase 26.1 baseline возвращает ``[]``; Phase 26.2 детекторы заполнят.
  Сравнивается с ``expected_engine_flags`` из tree-fixture при scoring.
- ``relationship_claims`` — biological/social/adoptive/unknown/hypothesis
  /rejected claims о связях. Phase 26.x подтянет full schema (см. ADR-0084
  §"Future shape").
- ``merge_decisions`` — person-merge decisions, включая blocked merges
  (например, NPE-кейсы должны блокировать auto-merge).
- ``place_corrections`` — historical jurisdiction corrections (например,
  Brest-Litovsk → Brest-Litovsk, Russian Empire по дате 1895).
- ``quarantined_claims`` — fabrication-suspected / public-tree-only / famous
  bridges, держатся outside sealed tree до confirmation.
- ``sealed_set_candidates`` — claims, которые могут стать sealed/confirmed
  после порога evidence (Phase 22.1+).
- ``evaluation_results`` — map ``assertion_id -> bool``. Baseline возвращает
  ``False`` для каждого assertion_id из tree-fixture's
  ``evaluation_assertions``. Detectors override entries по мере прохождения
  правил.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RelationshipClaim(BaseModel):
    """Claim о связи (biological / social / adoptive / unknown / hypothesis / rejected).

    Phase 26.1: forward-compatible пустая модель с ``extra="allow"``.
    Phase 26.x: tighten ``status`` enum, ``min_confidence``, evidence-graph.
    """

    model_config = ConfigDict(extra="allow")


class MergeDecision(BaseModel):
    """Решение о слиянии двух person'ов (или explicit block)."""

    model_config = ConfigDict(extra="allow")


class PlaceCorrection(BaseModel):
    """Historical place / jurisdiction correction."""

    model_config = ConfigDict(extra="allow")


class QuarantinedClaim(BaseModel):
    """Fabrication-suspected или public-tree-only claim, в quarantine."""

    model_config = ConfigDict(extra="allow")


class SealedSetCandidate(BaseModel):
    """Claim eligible to enter sealed set после порога evidence."""

    model_config = ConfigDict(extra="allow")


class EngineOutput(BaseModel):
    """Top-level engine output для одного tree.

    ``model_config(extra='forbid')`` — top-level keys строго фиксированы.
    Расширение контракта требует bump версии и обновления tests + runner.
    """

    model_config = ConfigDict(extra="forbid")

    tree_id: str = Field(min_length=1)
    engine_flags: list[str] = Field(default_factory=list)
    relationship_claims: list[RelationshipClaim] = Field(default_factory=list)
    merge_decisions: list[MergeDecision] = Field(default_factory=list)
    place_corrections: list[PlaceCorrection] = Field(default_factory=list)
    quarantined_claims: list[QuarantinedClaim] = Field(default_factory=list)
    sealed_set_candidates: list[SealedSetCandidate] = Field(default_factory=list)
    evaluation_results: dict[str, bool] = Field(default_factory=dict)


REQUIRED_OUTPUT_KEYS: frozenset[str] = frozenset(
    {
        "tree_id",
        "engine_flags",
        "relationship_claims",
        "merge_decisions",
        "place_corrections",
        "quarantined_claims",
        "sealed_set_candidates",
        "evaluation_results",
    }
)
"""Set of top-level keys required in any ``run_tree`` output. Used by tests
(``tests/test_inference_engine_output_schema.py``) и runner для validation."""


def validate_output(payload: dict[str, Any]) -> EngineOutput:
    """Прогнать payload через Pydantic-валидацию и вернуть модель.

    Raises:
        pydantic.ValidationError: При несоответствии схеме (missing required
            key, unknown extra key, bad type).
    """
    return EngineOutput.model_validate(payload)


__all__ = [
    "REQUIRED_OUTPUT_KEYS",
    "EngineOutput",
    "MergeDecision",
    "PlaceCorrection",
    "QuarantinedClaim",
    "RelationshipClaim",
    "SealedSetCandidate",
    "validate_output",
]
