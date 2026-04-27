"""Core types для hypothesis-aware inference engine.

Все модели — Pydantic v2. ADR-0016 §«Core types» фиксирует семантику:

- ``Hypothesis`` — claim о связи между двумя сущностями (например, что
  они один и тот же человек). Несёт список ``Evidence`` и composite
  score в ``[0, 1]``.
- ``Evidence`` — атомарный факт supporting / contradicting / neutral
  для гипотезы. Каждый Evidence знает свой ``rule_id`` (provenance) и
  human-readable ``observation`` для UI explanation.
- ``HypothesisType`` / ``EvidenceDirection`` — узкие enum'ы,
  чтобы исключить опечатки в строковых ключах.

Persistence (Phase 7.2) и HTTP-сериализация (Phase 7.3) маппятся на
эти модели 1:1 через ``model_dump`` / ``model_validate``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class EvidenceDirection(StrEnum):
    """Направление вклада Evidence в гипотезу.

    ``SUPPORTS`` повышает composite score, ``CONTRADICTS`` понижает,
    ``NEUTRAL`` фиксирует факт без влияния на score (важно отличать
    «есть данные, не за и не против» от «данных нет»).
    """

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


class HypothesisType(StrEnum):
    """Тип гипотезы о связи между двумя сущностями.

    Phase 7.0 ограничивается pairwise-гипотезами. Multi-subject
    (например, gen-3 family identification) — Phase 7.x, потребует
    расширения модели до ``subjects: list``.
    """

    SAME_PERSON = "same_person"
    PARENT_CHILD = "parent_child"
    SIBLINGS = "siblings"
    MARRIAGE = "marriage"


class Evidence(BaseModel):
    """Атомарный факт, который rule произвёл при сравнении двух subjects.

    Поля:
        rule_id: Идентификатор rule'а, произведшего этот Evidence.
            Используется для provenance: «почему мы это считаем».
        direction: SUPPORTS / CONTRADICTS / NEUTRAL — см. EvidenceDirection.
        weight: Вклад в composite score, ``[0.0, 1.0]``. Для NEUTRAL
            обычно 0, но мы не запрещаем — rule может задокументировать
            «факт средней силы, но нейтральный по отношению к гипотезе».
        observation: Человекочитаемая строка для UI («Birth year exact
            match (1945)»). Не должна содержать raw PII (privacy).
        source_provenance: Произвольный JSON-словарь с pointer на
            reference data, версии, sha256-хэши и т.п. Phase 7.0 —
            пустой по умолчанию; Phase 7.1+ rules заполняют.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(min_length=1)
    direction: EvidenceDirection
    weight: float = Field(ge=0.0, le=1.0)
    observation: str = Field(min_length=1)
    source_provenance: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    """Гипотеза о связи между двумя сущностями + её evidence-graph.

    Поля:
        id: UUID v4, генерируется при создании. Стабилен в памяти,
            пригоден для сериализации в Phase 7.2 ORM.
        hypothesis_type: SAME_PERSON / PARENT_CHILD / SIBLINGS / MARRIAGE.
        subject_a_id, subject_b_id: UUID сравниваемых сущностей.
            В Phase 7.0 — произвольные UUID; в Phase 7.x будут FK на
            ``persons.id`` / ``places.id`` / etc.
        evidences: Список Evidence, произведённых rules. Может быть
            пустым (если rules не применимы к данной паре) — в таком
            случае ``composite_score`` = 0.0.
        composite_score: Агрегированный score в ``[0.0, 1.0]``.
            Заполняется ``compose_hypothesis()`` через weighted-sum формулу
            (см. ADR-0016 §«Composer»). Не Bayes posterior.
        alternatives: Список альтернативных гипотез (например,
            «не same-person, а brothers»). Phase 7.0 — пустой; генерация
            альтернатив — Phase 7.4.
    """

    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4)
    hypothesis_type: HypothesisType
    subject_a_id: UUID
    subject_b_id: UUID
    evidences: list[Evidence] = Field(default_factory=list)
    composite_score: float = Field(default=0.0, ge=0.0, le=1.0)
    alternatives: list[Hypothesis] = Field(default_factory=list)
