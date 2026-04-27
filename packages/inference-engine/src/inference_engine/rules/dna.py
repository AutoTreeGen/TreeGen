"""DnaSegmentRelationshipRule — pairwise DNA-aware inference rule (Phase 7.3).

Cм. ADR-0023. Rule потребляет pre-aggregated DNA-данные из ``context``
и выдаёт SUPPORTS/CONTRADICTS Evidence для гипотез ``same_person`` /
``parent_child`` / ``siblings`` в зависимости от total shared cM.

Pure-функция: никакого I/O, никаких ORM. DNA aggregate готовит caller
(в Phase 7.3.1 — `hypothesis_runner` через `_person_to_subject` или
отдельный context-loader). Этот rule не имеет доступа к raw rsids /
genotypes — только к aggregate-полям, передаваемым через context.

Источник cM-порогов — Shared cM Project 4.0 (Bettinger, CC-BY 4.0),
выровнено с таблицей в
``packages/dna-analysis/src/dna_analysis/matching/relationships.py``.
Шум-floor 7 cM синхронизирован с ADR-0014.

Endogamy: multiplier из ``EthnicityPopulation`` enum применяется как
weight-divisor (НЕ переклассифицируем direction). См. ADR-0023
«Endogamy adjustment».

Distant relationships (1C / 2C / great-grandparent) этим rule **не
покрываются** — pairwise total cM на distant overlapping диапазонах даёт
слабый дискриминационный сигнал. Cluster-rule (multi-subject) — Phase 7.4.
"""

from __future__ import annotations

from typing import Any, Final

from inference_engine.types import Evidence, EvidenceDirection

# ----- Source attribution ---------------------------------------------------

_SOURCE: Final = "Shared cM Project 4.0 (Bettinger, CC-BY 4.0)"

# ----- Hypothesis types этого rule ------------------------------------------

_SAME_PERSON: Final = "same_person"
_PARENT_CHILD: Final = "parent_child"
_SIBLINGS: Final = "siblings"

_SUPPORTED_HYPOTHESES: Final = frozenset({_SAME_PERSON, _PARENT_CHILD, _SIBLINGS})

# ----- Шум-floor (синхронно с ADR-0014 / dna_analysis.matching.relationships)

_NOISE_FLOOR_CM: Final = 7.0

# ----- cM-диапазоны из Shared cM Project 4.0 (см. ADR-0023) -----------------
#
# SAME_PERSON: identical twin / self ≥ 3400 cM (mean 3487).
# CONTRADICTS если total cM < full-sibling lower bound — clearly не один
# и тот же человек.
_SAME_PERSON_SUPPORT_MIN_CM: Final = 3400.0
_SAME_PERSON_CONTRADICT_MAX_CM: Final = 1500.0

# PARENT_CHILD: 2376–3720 (mean 3485). Выше 3800 — twin-zone (CONTRADICTS,
# probably same person, не parent-child). Ниже 1500 — clearly distant.
_PARENT_CHILD_SUPPORT_MIN_CM: Final = 2376.0
_PARENT_CHILD_SUPPORT_MAX_CM: Final = 3720.0
_PARENT_CHILD_CONTRADICT_LOW_CM: Final = 1500.0
_PARENT_CHILD_CONTRADICT_HIGH_CM: Final = 3800.0

# SIBLINGS (full): 1613–3488. CONTRADICTS если < 1000 (≪ full-sibling
# lower bound).
_SIBLINGS_SUPPORT_MIN_CM: Final = 1613.0
_SIBLINGS_SUPPORT_MAX_CM: Final = 3488.0
_SIBLINGS_CONTRADICT_MAX_CM: Final = 1000.0

# ----- Confidence weights (см. ADR-0023 «Confidence formula») ---------------

_W_SAME_PERSON_SUPPORT: Final = 0.85
_W_SAME_PERSON_CONTRADICT: Final = 0.85
_W_PARENT_CHILD_SUPPORT: Final = 0.80
_W_PARENT_CHILD_CONTRADICT: Final = 0.70
_W_SIBLINGS_SUPPORT: Final = 0.65
_W_SIBLINGS_CONTRADICT: Final = 0.60

# ----- Endogamy multipliers (Bettinger studies; ADR-0023 + EthnicityPopulation enum)
#
# Inference-engine — light pure-functions package (ADR-0016) и сознательно
# не зависит от shared-models. Multiplier-таблица здесь — копия значений из
# ``shared_models.enums.EthnicityPopulation``. Если enum пополнится новой
# популяцией, добавить ключ сюда (и в ADR-0023).
_ENDOGAMY_MULTIPLIER: Final[dict[str, float]] = {
    "general": 1.0,
    "ashkenazi": 1.6,
    "sephardi": 1.4,
    "amish": 2.0,
    "lds_pioneer": 1.5,
}


class DnaSegmentRelationshipRule:
    """Pairwise rule: total shared cM → SUPPORTS / CONTRADICTS Evidence.

    Применима к hypothesis types ``same_person``, ``parent_child``,
    ``siblings``. Для остальных (`marriage`, любые DUPLICATE_*) — silent.

    Subject-контракт: rule не читает поля subject'ов (вся работа через
    context). Subject'ы передаются для homogeneity Protocol-сигнатуры
    `apply(subject_a, subject_b, context)`.

    Context-контракт:

    - ``context["hypothesis_type"]`` (str) — обязателен.
    - ``context["dna_evidence"]`` (dict) — опционален. Shape:

      .. code-block:: python

         {
             "total_cm": float,
             "longest_segment_cm": float,
             "segment_count": int,
             "ethnicity_population_a": str,  # EthnicityPopulation value
             "ethnicity_population_b": str,
             "source": str,                  # "ancestry_match_list" | ...
             "kit_id_a": str | None,         # UUID-псевдоним
             "kit_id_b": str | None,
         }

      Если отсутствует / None — rule silent (пустой list).
    """

    rule_id = "dna_segment_relationship"

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        del subject_a, subject_b  # rule context-driven; subject keys не читаются

        hypothesis_type = context.get("hypothesis_type")
        if hypothesis_type not in _SUPPORTED_HYPOTHESES:
            return []

        dna_evidence = context.get("dna_evidence")
        if not isinstance(dna_evidence, dict):
            return []

        try:
            total_cm = float(dna_evidence["total_cm"])
        except (KeyError, TypeError, ValueError):
            return []

        if total_cm < _NOISE_FLOOR_CM:
            return []

        # Endogamy multiplier — самый консервативный (max) из двух subject'ов.
        # ADR-0023 §«Endogamy adjustment»: weight делится на multiplier;
        # direction не переклассифицируется.
        multiplier = self._endogamy_multiplier(dna_evidence)

        if hypothesis_type == _SAME_PERSON:
            evidence = self._evaluate_same_person(total_cm, multiplier)
        elif hypothesis_type == _PARENT_CHILD:
            evidence = self._evaluate_parent_child(total_cm, multiplier)
        else:  # _SIBLINGS — единственный оставшийся
            evidence = self._evaluate_siblings(total_cm, multiplier)

        if evidence is None:
            return []

        # Дополнительный provenance: aggregate-only метаданные для UI и
        # evidence-graph audit. raw genotypes не пишем — ADR-0023 / ADR-0012.
        provenance = {
            "source": _SOURCE,
            "total_cm": round(total_cm, 2),
            "longest_segment_cm": round(
                _coerce_float(dna_evidence.get("longest_segment_cm"), default=0.0), 2
            ),
            "segment_count": _coerce_int(dna_evidence.get("segment_count"), default=0),
            "endogamy_multiplier": round(multiplier, 2),
            "ethnicity_population_a": str(dna_evidence.get("ethnicity_population_a", "general")),
            "ethnicity_population_b": str(dna_evidence.get("ethnicity_population_b", "general")),
            "dna_source": str(dna_evidence.get("source", "unknown")),
        }
        kit_id_a = dna_evidence.get("kit_id_a")
        kit_id_b = dna_evidence.get("kit_id_b")
        if kit_id_a is not None:
            provenance["kit_id_a"] = str(kit_id_a)
        if kit_id_b is not None:
            provenance["kit_id_b"] = str(kit_id_b)

        return [
            Evidence(
                rule_id=self.rule_id,
                direction=evidence.direction,
                weight=evidence.weight,
                observation=evidence.observation,
                source_provenance=provenance,
            )
        ]

    # ----- per-hypothesis evaluators ----------------------------------------

    def _evaluate_same_person(
        self,
        total_cm: float,
        multiplier: float,
    ) -> _PartialEvidence | None:
        if total_cm >= _SAME_PERSON_SUPPORT_MIN_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.SUPPORTS,
                weight=_apply_multiplier(_W_SAME_PERSON_SUPPORT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} ≥ "
                    f"{_SAME_PERSON_SUPPORT_MIN_CM:.0f} (identical-twin / self range)"
                ),
            )
        if total_cm < _SAME_PERSON_CONTRADICT_MAX_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.CONTRADICTS,
                weight=_apply_multiplier(_W_SAME_PERSON_CONTRADICT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} < "
                    f"{_SAME_PERSON_CONTRADICT_MAX_CM:.0f} "
                    "(below full-sibling range; cannot be the same person)"
                ),
            )
        # Серая зона 1500–3400 cM — pairwise total cM не дискриминирует
        # same_person от parent/sibling. Silent.
        return None

    def _evaluate_parent_child(
        self,
        total_cm: float,
        multiplier: float,
    ) -> _PartialEvidence | None:
        if _PARENT_CHILD_SUPPORT_MIN_CM <= total_cm <= _PARENT_CHILD_SUPPORT_MAX_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.SUPPORTS,
                weight=_apply_multiplier(_W_PARENT_CHILD_SUPPORT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} in parent-child range "
                    f"[{_PARENT_CHILD_SUPPORT_MIN_CM:.0f}, "
                    f"{_PARENT_CHILD_SUPPORT_MAX_CM:.0f}]"
                ),
            )
        if total_cm > _PARENT_CHILD_CONTRADICT_HIGH_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.CONTRADICTS,
                weight=_apply_multiplier(_W_PARENT_CHILD_CONTRADICT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} > "
                    f"{_PARENT_CHILD_CONTRADICT_HIGH_CM:.0f} "
                    "(identical-twin / self range — not parent-child)"
                ),
            )
        if total_cm < _PARENT_CHILD_CONTRADICT_LOW_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.CONTRADICTS,
                weight=_apply_multiplier(_W_PARENT_CHILD_CONTRADICT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} < "
                    f"{_PARENT_CHILD_CONTRADICT_LOW_CM:.0f} "
                    "(too low for parent-child)"
                ),
            )
        # Серая зона: между full-sibling и parent-child нижней границей.
        return None

    def _evaluate_siblings(
        self,
        total_cm: float,
        multiplier: float,
    ) -> _PartialEvidence | None:
        if _SIBLINGS_SUPPORT_MIN_CM <= total_cm <= _SIBLINGS_SUPPORT_MAX_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.SUPPORTS,
                weight=_apply_multiplier(_W_SIBLINGS_SUPPORT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} in full-sibling range "
                    f"[{_SIBLINGS_SUPPORT_MIN_CM:.0f}, "
                    f"{_SIBLINGS_SUPPORT_MAX_CM:.0f}]"
                ),
            )
        if total_cm < _SIBLINGS_CONTRADICT_MAX_CM:
            return _PartialEvidence(
                direction=EvidenceDirection.CONTRADICTS,
                weight=_apply_multiplier(_W_SIBLINGS_CONTRADICT, multiplier),
                observation=(
                    f"Total shared cM = {total_cm:.0f} < "
                    f"{_SIBLINGS_CONTRADICT_MAX_CM:.0f} "
                    "(too low for full-sibling)"
                ),
            )
        # Выше full-sibling верхней границы — скорее twin/parent. Не
        # CONTRADICTS siblings строго (range пересекается с parent-child),
        # silent.
        return None

    @staticmethod
    def _endogamy_multiplier(dna_evidence: dict[str, Any]) -> float:
        """Самый консервативный (max) multiplier из двух subject'ов.

        Неизвестные / отсутствующие populations → fallback ``general`` (1.0).
        """
        pop_a = str(dna_evidence.get("ethnicity_population_a", "general"))
        pop_b = str(dna_evidence.get("ethnicity_population_b", "general"))
        mul_a = _ENDOGAMY_MULTIPLIER.get(pop_a, 1.0)
        mul_b = _ENDOGAMY_MULTIPLIER.get(pop_b, 1.0)
        return max(mul_a, mul_b)


# ----- Internals -----------------------------------------------------------


class _PartialEvidence:
    """Тонкий holder для (direction, weight, observation) до build'а Evidence.

    Используется чтобы centralize'ить provenance-сборку в одном месте
    (в `apply()`) и не дублировать `Evidence(...)` в каждой ветке.
    """

    __slots__ = ("direction", "observation", "weight")

    def __init__(
        self,
        *,
        direction: EvidenceDirection,
        weight: float,
        observation: str,
    ) -> None:
        self.direction = direction
        self.weight = weight
        self.observation = observation


def _apply_multiplier(base_weight: float, multiplier: float) -> float:
    """Endogamy adjustment: weight / multiplier, clamp в [0, 1].

    multiplier ≥ 1 → weight уменьшается (endogamy снижает уверенность).
    multiplier == 1 → weight без изменений (general population).
    multiplier < 1 — теоретически invalid (Bettinger даёт ≥ 1.0), но
    защищаемся clamp'ом, чтобы не вывалиться за `weight ≤ 1` в Pydantic.
    """
    if multiplier <= 0:
        return base_weight
    adjusted = base_weight / multiplier
    return max(0.0, min(1.0, adjusted))


def _coerce_float(value: object, *, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, (int, float, str, bytes)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return default
    return default


__all__ = ["DnaSegmentRelationshipRule"]
