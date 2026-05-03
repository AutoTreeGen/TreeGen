"""DetectorResult dataclass — общий тип для всех Phase 26.x детекторов.

Лежит в отдельном module (не в ``registry.py``), чтобы детекторы могли
импортить ``DetectorResult`` без циркулярной зависимости с registry,
который сам импортит детекторов на module-level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DetectorResult:
    """Output одного tree-level детектора.

    Поля совпадают с list-полями ``EngineOutput`` (см. ADR-0084) плюс
    ``evaluation_results``. Engine мерджит несколько ``DetectorResult``
    в финальный output: list-поля extend'ятся, ``evaluation_results``
    update'ится.
    """

    engine_flags: list[str] = field(default_factory=list)
    relationship_claims: list[dict[str, Any]] = field(default_factory=list)
    merge_decisions: list[dict[str, Any]] = field(default_factory=list)
    place_corrections: list[dict[str, Any]] = field(default_factory=list)
    quarantined_claims: list[dict[str, Any]] = field(default_factory=list)
    sealed_set_candidates: list[dict[str, Any]] = field(default_factory=list)
    evaluation_results: dict[str, bool] = field(default_factory=dict)


__all__ = ["DetectorResult"]
