"""LlmPlaceMatchRule — LLM-augmented place match для gray-zone (Phase 10.0).

Идея: ``BirthPlaceMatchRule`` (Phase 7.1) хорошо справляется с очевидными
случаями — identical match (≥0.80, SUPPORTS) и radically разные места
(<0.30, CONTRADICTS). Между ними — серая зона 0.30–0.80, где fuzzy-score
ничего не говорит: «Slonim, Grodno» vs «Pinsk, Minsk» — оба в Беларуси,
но это разные местечки. А «Slonim, Russian Empire» vs «Slonim, BLR» —
score ~0.4, но это **одно и то же** место в разные исторические периоды.

Этот rule вызывает ``llm_services.normalize_place_name`` для пары мест,
попавших в **узкую** gray-zone полосу 0.40–0.70, и сравнивает
канонизированные формы. Если LLM канонизировал оба к одному и тому же
``(name, country_code)`` — выдаёт SUPPORTS; если разные страны — CONTRADICTS.

Cost-aware design (ADR-0030):

* **Gray-zone gating.** LLM вызывается ТОЛЬКО для score ∈ [0.40, 0.70].
  Slam-dunk случаи (>0.80 или <0.30) уже решены rule-based, тратить
  LLM-токены на них — bad ROI.
* **Sync-callable injection.** Rule принимает ``normalizer`` callable
  на construction-time. Caller отвечает за async/sync bridge,
  caching, и budget-tracking. Default — ``None`` → rule no-op.
* **Pure-function контракт.** При фиксированном ``normalizer`` (например,
  cache-lookup) rule полностью детерминистичен. Реальный LLM-вызов
  делается caller'ом снаружи протокола.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from entity_resolution.places import place_match_score

from inference_engine.rules._transliteration import transliterate_cyrillic
from inference_engine.types import Evidence, EvidenceDirection

if TYPE_CHECKING:
    from llm_services import NormalizedPlace

# Узкая gray-zone полоса: вне её rule делегирует решение `BirthPlaceMatchRule`.
# Нижняя граница 0.40 (а не 0.30) — ниже rule-based уже выдаёт CONTRADICTS,
# нет смысла дублировать. Верхняя граница 0.70 (а не 0.80) — выше
# rule-based выдаёт SUPPORTS.
_LLM_GRAY_ZONE_MIN = 0.40
_LLM_GRAY_ZONE_MAX = 0.70

# Минимальный confidence от LLM для production-evidence. Ниже — игнорируем
# (LLM сам сообщает «неуверен»; не множим шум).
_MIN_LLM_CONFIDENCE = 0.50

# Weight'ы — мягче чем у BirthPlaceMatchRule, потому что LLM может
# галлюцинировать. Композер увидит как «дополнительный сигнал», не как
# определяющий.
_SUPPORTS_WEIGHT_FACTOR = 0.30  # weight = factor × LLM-confidence
_CONTRADICTS_WEIGHT_FACTOR = 0.25


PlaceNormalizer = Callable[[str], "NormalizedPlace"]
"""Sync-callable, превращающий raw-строку в ``NormalizedPlace``.

Caller отвечает за async/sync bridge — в простейшем случае
``lambda raw: asyncio.run(normalize_place_name(raw, {}))`` для batch-job;
в FastAPI-handler — pre-compute через ``asyncio.gather`` и оборачивание
в dict-lookup callable.
"""


class LlmPlaceMatchRule:
    """LLM-augmented place match — работает только в gray-zone."""

    rule_id = "llm_place_match"

    def __init__(self, normalizer: PlaceNormalizer | None = None) -> None:
        """Создать rule.

        Args:
            normalizer: Sync-callable для канонизации места. Если ``None``,
                rule всегда возвращает пустой list (zero-cost mode для
                окружений без ANTHROPIC_API_KEY). В тестах — mock-callable.
        """
        self._normalizer = normalizer

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        del context
        if self._normalizer is None:
            return []

        a_place = subject_a.get("birth_place")
        b_place = subject_b.get("birth_place")
        if not a_place or not b_place:
            return []

        # Тот же fuzzy-score, что и у BirthPlaceMatchRule — единый источник.
        a_normalized = transliterate_cyrillic(a_place)
        b_normalized = transliterate_cyrillic(b_place)
        score = place_match_score(a_normalized, b_normalized)

        if not (_LLM_GRAY_ZONE_MIN <= score <= _LLM_GRAY_ZONE_MAX):
            # Не gray-zone → rule не применим. BirthPlaceMatchRule уже
            # выдал/не выдал evidence согласно своей логике.
            return []

        norm_a = self._normalizer(a_place)
        norm_b = self._normalizer(b_place)

        # Композитный confidence — geometric mean (наказывает asymmetry,
        # когда LLM уверен в одном и сомневается в другом).
        confidence = (norm_a.confidence * norm_b.confidence) ** 0.5
        if confidence < _MIN_LLM_CONFIDENCE:
            return []

        same_canonical = norm_a.name.casefold() == norm_b.name.casefold()
        same_country = (
            norm_a.country_code is not None
            and norm_b.country_code is not None
            and norm_a.country_code == norm_b.country_code
        )

        provenance: dict[str, Any] = {
            "algorithm": "llm_place_match",
            "package": "llm-services",
            "rule_based_score": round(score, 4),
            "llm_normalization": {
                "a": norm_a.model_dump(),
                "b": norm_b.model_dump(),
            },
            "llm_confidence": round(confidence, 4),
        }

        if same_canonical and same_country:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.SUPPORTS,
                    weight=_SUPPORTS_WEIGHT_FACTOR * confidence,
                    observation=(
                        f"LLM canonicalized both places to {norm_a.name!r} "
                        f"({norm_a.country_code}); rule-based score was "
                        f"ambiguous ({score:.2f})"
                    ),
                    source_provenance=provenance,
                )
            ]

        if (
            norm_a.country_code is not None
            and norm_b.country_code is not None
            and norm_a.country_code != norm_b.country_code
        ):
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.CONTRADICTS,
                    weight=_CONTRADICTS_WEIGHT_FACTOR * confidence,
                    observation=(
                        f"LLM resolved to different countries: "
                        f"{norm_a.name!r} ({norm_a.country_code}) vs "
                        f"{norm_b.name!r} ({norm_b.country_code})"
                    ),
                    source_provenance=provenance,
                )
            ]

        # Один canonical name, но country_code различается / отсутствует —
        # неоднозначно, evidence не выдаём.
        return []


__all__ = ["LlmPlaceMatchRule", "PlaceNormalizer"]
