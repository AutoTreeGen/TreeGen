"""BirthPlaceMatchRule — fuzzy-сравнение мест рождения.

Использует ``entity_resolution.places.place_match_score`` — token_set +
иерархический prefix-subset boost (Phase 3.4). Cyrillic-входы
транслитерируются как у surname-rule.

Threshold semantics (Phase 7.1 §«BirthPlaceMatchRule»):

* score ≥ 0.80 → SUPPORTS, weight = 0.4 × score (т.е. 0.32–0.40).
  Сильный, но не определяющий сигнал — близкое место часто разделяют
  родственники, не только идентичные personы.
* score < 0.30 → CONTRADICTS, weight = 0.30. Очень разные места при
  гипотезе same_person — лёгкий negative сигнал. Не CONTRADICTS на
  weight 1.0, потому что user мог иммигрировать (Slonim 1850 vs
  New York 1900 — не противоречит SAME_PERSON, просто две разных
  записи для одного человека до и после переезда).
* 0.30–0.80 → no evidence (NEUTRAL silence): неопределённо, лучше
  оставить решение другим rule's.

Subject-контракт: ключ ``"birth_place"`` (строка) — opt'нo. Отсутствие
у любой стороны → пустой list.
"""

from __future__ import annotations

from typing import Any

from entity_resolution.places import place_match_score

from inference_engine.rules._transliteration import transliterate_cyrillic
from inference_engine.types import Evidence, EvidenceDirection

_SUPPORTS_THRESHOLD = 0.80
_CONTRADICTS_THRESHOLD = 0.30
_SUPPORTS_WEIGHT_FACTOR = 0.4  # weight = factor × score, capped through composer
_CONTRADICTS_WEIGHT = 0.30


class BirthPlaceMatchRule:
    """Birth place fuzzy-match → SUPPORTS / CONTRADICTS / no evidence."""

    rule_id = "birth_place_match"

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        del context  # не используется (place-сравнение само-содержательное)
        a_place = subject_a.get("birth_place")
        b_place = subject_b.get("birth_place")
        if not a_place or not b_place:
            return []

        # Транслитерация: «Днепр» → «Dnepr», иначе token_set_ratio
        # сравнит кириллицу с латиницей и даст ~0.
        a_normalized = transliterate_cyrillic(a_place)
        b_normalized = transliterate_cyrillic(b_place)
        score = place_match_score(a_normalized, b_normalized)

        if score >= _SUPPORTS_THRESHOLD:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.SUPPORTS,
                    weight=_SUPPORTS_WEIGHT_FACTOR * score,
                    observation=(
                        f"Birth place fuzzy match: score={score:.2f} (a={a_place!r}, b={b_place!r})"
                    ),
                    source_provenance={
                        "algorithm": "place_match_score",
                        "package": "entity-resolution",
                        "score": round(score, 4),
                    },
                )
            ]

        if score < _CONTRADICTS_THRESHOLD:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.CONTRADICTS,
                    weight=_CONTRADICTS_WEIGHT,
                    observation=(
                        f"Birth places diverge: score={score:.2f} (a={a_place!r}, b={b_place!r})"
                    ),
                    source_provenance={
                        "algorithm": "place_match_score",
                        "package": "entity-resolution",
                        "score": round(score, 4),
                    },
                )
            ]

        # Серая зона 0.30–0.80: не выдаём evidence, остальные rule's
        # пусть решают.
        return []


__all__ = ["BirthPlaceMatchRule"]
