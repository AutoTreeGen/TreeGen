"""SexConsistencyRule — несовпадение пола → CONTRADICTS для same_person.

Hard rule, симметричный entity-resolution.persons §«hard sex filter»:
если оба пола известны (M/F) и разные — same_person гипотеза получает
сильный негативный сигнал.

Применимо ТОЛЬКО к ``HypothesisType.SAME_PERSON``. Для PARENT_CHILD /
SIBLINGS / MARRIAGE разный пол не противоречит гипотезе (наоборот,
для MARRIAGE традиционно ожидаем M+F пару). Поэтому проверяем
``context["hypothesis_type"]`` (или fallback — пустой list, если caller
не передал контекст).

CLAUDE.md / shared_models.enums.Sex: ``M`` / ``F`` / ``U`` (unknown) /
``X`` (other / intersex). ``U`` и ``X`` считаем "не M и не F" — не
триггерим contradicts (мы просто не знаем). Это намеренно консервативно:
не хотим отбрасывать кандидатов с неопределённым полом.
"""

from __future__ import annotations

from typing import Any

from inference_engine.types import Evidence, EvidenceDirection

# Сильный negative weight для несовпадения пола в same_person —
# обычно perepere всех других сигналов и опускает composite score
# до 0 после clamp в composer.
_SEX_MISMATCH_WEIGHT = 0.95

# Какие значения считаем "известным определённым полом".
_KNOWN_SEX = frozenset({"M", "F"})


class SexConsistencyRule:
    """Hard contradiction для same_person при mismatch известных M/F."""

    rule_id = "sex_consistency"

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        # Применимо только к гипотезе same_person. Для других типов
        # (parent_child / siblings / marriage) разный пол ожидается /
        # допустим — silently skip.
        if context.get("hypothesis_type") != "same_person":
            return []

        a_sex = subject_a.get("sex")
        b_sex = subject_b.get("sex")
        if a_sex not in _KNOWN_SEX or b_sex not in _KNOWN_SEX:
            return []
        if a_sex == b_sex:
            return []

        return [
            Evidence(
                rule_id=self.rule_id,
                direction=EvidenceDirection.CONTRADICTS,
                weight=_SEX_MISMATCH_WEIGHT,
                observation=f"Sex mismatch for same_person hypothesis: {a_sex} vs {b_sex}",
                source_provenance={"hypothesis_type": "same_person"},
            )
        ]


__all__ = ["SexConsistencyRule"]
