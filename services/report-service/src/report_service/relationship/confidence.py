"""Confidence aggregation для Relationship Research Report (Phase 24.3).

Формула 22.5: для каждого evidence-piece считается ``weight × match_certainty``.
Supporting и contradicting складываются раздельно; композитный score —
``Σ supporting − Σ contradicting``.

Метод confidence — `bayesian_22_5` если есть хоть одно non-citation
evidence (off-catalog Evidence-row, hypothesis-evidence или DNA), иначе
`naive_count` (если только citations), иначе `asserted_only`.
"""

from __future__ import annotations

from typing import Literal

from report_service.relationship.models import EvidencePiece

ConfidenceMethod = Literal["bayesian_22_5", "naive_count", "asserted_only"]


def compute_confidence(
    supporting: list[EvidencePiece],
    contradicting: list[EvidencePiece],
) -> tuple[float, ConfidenceMethod]:
    """Возвращает (confidence, method).

    * Pure-supporting случай (нет contradicting): confidence = Σ supporting.
    * При наличии contradicting: confidence = max(0, Σ supporting − Σ contradicting).
    * Confidence не нормализуется в [0,1] — потребители (UI) могут
      делить на ``len(supporting)`` или клампить по своим нуждам.
      Раскладка по 22.5 ADR-0071 §"weight semantics" — max теоретически
      tier-3 (3.0) × match_certainty (1.0) = 3.0 на piece.
    """
    if not supporting and not contradicting:
        return 0.0, "asserted_only"

    sup = _sum_score(supporting)
    contra = _sum_score(contradicting)
    score = max(0.0, sup - contra)

    method: ConfidenceMethod = (
        "bayesian_22_5"
        if any(p.kind != "citation" for p in (*supporting, *contradicting))
        else "naive_count"
    )
    return score, method


def _sum_score(pieces: list[EvidencePiece]) -> float:
    return sum(_piece_score(p) for p in pieces)


def _piece_score(piece: EvidencePiece) -> float:
    """22.5: ``weight × match_certainty``. Гарантирует non-negative."""
    return max(0.0, float(piece.weight) * float(piece.match_certainty))


__all__ = ["ConfidenceMethod", "compute_confidence"]
