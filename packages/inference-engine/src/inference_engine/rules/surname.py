"""SurnameMatchRule — фонетическое совпадение фамилий через Daitch-Mokotoff.

Использует ``entity_resolution.phonetic.daitch_mokotoff`` — own-implementation
Phase 3.4, который специально настроен на еврейские / восточно-европейские
фамилии. Фамилии считаются "фонетически совпадающими", если их множества
DM-кодов **пересекаются** (любой общий код).

Cyrillic вход транслитерируется в латиницу до DM (см. ``_transliteration``).
DM работает только на A–Z, так что без шага транслитерации
``Zhitnitzky`` и ``Житницкий`` оказались бы в разных bucket'ах.

Subject-контракт: ключ ``"surname"`` (строка). Если у любой стороны
surname отсутствует / пустой / не разбирается — rule возвращает пустой
list (NEUTRAL = no evidence), а не CONTRADICTS.
"""

from __future__ import annotations

from typing import Any

from entity_resolution.phonetic import daitch_mokotoff

from inference_engine.rules._transliteration import transliterate_cyrillic
from inference_engine.types import Evidence, EvidenceDirection

# Вес SUPPORTS evidence — соглашение с брифом Phase 7.1 §«SurnameMatchRule»:
# 0.5 как одиночный сильный сигнал. Композитный score складывается с
# другими rules в composer'е (weighted sum, см. ADR-0016).
_DM_MATCH_WEIGHT = 0.5


class SurnameMatchRule:
    """Daitch-Mokotoff bucket overlap → SUPPORTS Evidence.

    Pure-функция. Применима к любому ``HypothesisType``, который
    подразумевает родственную / тождественную связь между двумя
    persons (SAME_PERSON, SIBLINGS, PARENT_CHILD). Marriage hypothesis
    обычно не выигрывает от surname-matching (супруги часто из разных
    родов), но rule всё равно безопасно отрабатывает и UI может
    отфильтровать.
    """

    rule_id = "surname_dm_match"

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        """Return SUPPORTS evidence, если DM-bucket'ы пересеклись."""
        del context  # не используется
        a_surname = subject_a.get("surname")
        b_surname = subject_b.get("surname")
        if not a_surname or not b_surname:
            return []

        a_codes = set(daitch_mokotoff(transliterate_cyrillic(a_surname)))
        b_codes = set(daitch_mokotoff(transliterate_cyrillic(b_surname)))
        if not a_codes or not b_codes:
            return []

        overlap = a_codes & b_codes
        if not overlap:
            return []

        # Observation специально без raw PII: только bucket-codes
        # (это анонимные phonetic-keys, не сами имена). Если потребуется
        # имя для UI, оно живёт в subject_a/b и фронт может их соединить
        # с rule_id. См. ADR-0012 §«Privacy» — observations не должны
        # утечь в логи.
        observation = (
            f"Daitch-Mokotoff bucket overlap "
            f"(a={sorted(a_codes)}, b={sorted(b_codes)}, "
            f"shared={sorted(overlap)})"
        )
        return [
            Evidence(
                rule_id=self.rule_id,
                direction=EvidenceDirection.SUPPORTS,
                weight=_DM_MATCH_WEIGHT,
                observation=observation,
                source_provenance={
                    "algorithm": "daitch_mokotoff",
                    "package": "entity-resolution",
                },
            )
        ]


__all__ = ["SurnameMatchRule"]
