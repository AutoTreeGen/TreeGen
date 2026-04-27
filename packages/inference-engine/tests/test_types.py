"""Тесты Pydantic-моделей: валидация полей, дефолты, immutability."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from inference_engine import (
    Evidence,
    EvidenceDirection,
    Hypothesis,
    HypothesisType,
)
from pydantic import ValidationError


class TestEvidence:
    def test_minimal_evidence_validates(self) -> None:
        ev = Evidence(
            rule_id="x",
            direction=EvidenceDirection.SUPPORTS,
            weight=0.5,
            observation="ok",
        )
        assert ev.rule_id == "x"
        assert ev.direction is EvidenceDirection.SUPPORTS
        assert ev.weight == 0.5
        assert ev.observation == "ok"
        assert ev.source_provenance == {}

    def test_weight_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                rule_id="x",
                direction=EvidenceDirection.SUPPORTS,
                weight=-0.01,
                observation="ok",
            )

    def test_weight_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                rule_id="x",
                direction=EvidenceDirection.SUPPORTS,
                weight=1.01,
                observation="ok",
            )

    def test_empty_rule_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                rule_id="",
                direction=EvidenceDirection.SUPPORTS,
                weight=0.5,
                observation="ok",
            )

    def test_empty_observation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                rule_id="x",
                direction=EvidenceDirection.SUPPORTS,
                weight=0.5,
                observation="",
            )

    def test_evidence_is_frozen(self) -> None:
        """Evidence — immutable (frozen). Менять state после создания нельзя."""
        ev = Evidence(
            rule_id="x",
            direction=EvidenceDirection.SUPPORTS,
            weight=0.5,
            observation="ok",
        )
        with pytest.raises(ValidationError):
            ev.weight = 0.9  # type: ignore[misc]

    def test_source_provenance_default_factory_is_independent(self) -> None:
        """default_factory=dict не должен делиться между instance'ами."""
        a = Evidence(
            rule_id="x",
            direction=EvidenceDirection.NEUTRAL,
            weight=0.0,
            observation="o",
        )
        b = Evidence(
            rule_id="x",
            direction=EvidenceDirection.NEUTRAL,
            weight=0.0,
            observation="o",
        )
        assert a.source_provenance is not b.source_provenance

    def test_source_provenance_accepts_arbitrary_dict(self) -> None:
        ev = Evidence(
            rule_id="dna_segment",
            direction=EvidenceDirection.SUPPORTS,
            weight=0.7,
            observation="42 cM shared on chr1",
            source_provenance={
                "reference_data": "Shared cM Project 4.0",
                "version": "Bettinger-2020-03",
            },
        )
        assert ev.source_provenance["reference_data"] == "Shared cM Project 4.0"


class TestHypothesis:
    def test_minimal_hypothesis_validates(self) -> None:
        a, b = uuid4(), uuid4()
        hyp = Hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a_id=a,
            subject_b_id=b,
        )
        assert isinstance(hyp.id, UUID)
        assert hyp.subject_a_id == a
        assert hyp.subject_b_id == b
        assert hyp.evidences == []
        assert hyp.composite_score == 0.0
        assert hyp.alternatives == []

    def test_id_default_is_unique_per_instance(self) -> None:
        h1 = Hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a_id=uuid4(),
            subject_b_id=uuid4(),
        )
        h2 = Hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a_id=uuid4(),
            subject_b_id=uuid4(),
        )
        assert h1.id != h2.id

    def test_composite_score_clamp_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Hypothesis(
                hypothesis_type=HypothesisType.SAME_PERSON,
                subject_a_id=uuid4(),
                subject_b_id=uuid4(),
                composite_score=-0.1,
            )

    def test_composite_score_clamp_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Hypothesis(
                hypothesis_type=HypothesisType.SAME_PERSON,
                subject_a_id=uuid4(),
                subject_b_id=uuid4(),
                composite_score=1.5,
            )

    def test_alternatives_recursive_type(self) -> None:
        """alternatives — list[Hypothesis], рекурсивно валидируется."""
        sub_a, sub_b = uuid4(), uuid4()
        alt = Hypothesis(
            hypothesis_type=HypothesisType.SIBLINGS,
            subject_a_id=sub_a,
            subject_b_id=sub_b,
        )
        main = Hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a_id=sub_a,
            subject_b_id=sub_b,
            alternatives=[alt],
        )
        assert len(main.alternatives) == 1
        assert main.alternatives[0].hypothesis_type is HypothesisType.SIBLINGS


class TestEnums:
    def test_evidence_direction_values(self) -> None:
        assert EvidenceDirection.SUPPORTS.value == "supports"
        assert EvidenceDirection.CONTRADICTS.value == "contradicts"
        assert EvidenceDirection.NEUTRAL.value == "neutral"

    def test_hypothesis_type_values(self) -> None:
        assert HypothesisType.SAME_PERSON.value == "same_person"
        assert HypothesisType.PARENT_CHILD.value == "parent_child"
        assert HypothesisType.SIBLINGS.value == "siblings"
        assert HypothesisType.MARRIAGE.value == "marriage"
