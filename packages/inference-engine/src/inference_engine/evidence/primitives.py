"""Phase 27.1 — fixture shape primitives + answer-key declarations.

Этот модуль намеренно маленький: TypedDict-проекции shape'а fixture'ов
(hint-only, без runtime-валидации) плюс канонический список «какие
поля fixture'а — answer key».

Терминология:

- **Top-level answer-key** (``ANSWER_KEY_TOP_LEVEL_FIELDS``) — ключи
  верхнего уровня tree-fixture'а, описывающие *ожидаемый output*, а
  не входное evidence: ``expected_engine_flags``,
  ``expected_confidence_outputs``, ``ground_truth_annotations``,
  ``expected_reasoning_chain``.
- **Nested answer-key** (``ANSWER_KEY_NESTED_FIELDS``) — sub-поля
  внутри list-items, которые тоже описывают ответ:
  ``embedded_errors[].expected_flag`` /
  ``embedded_errors[].expected_confidence_when_flagged`` /
  ``embedded_errors[].reason``,
  ``input_archive_snippets[].expected_use``,
  ``input_dna_matches[].expected_link``.

Phase 27.1 ``evidence.extractors`` физически удаляют nested
answer-key sub-поля из возвращаемых items. Phase 27.2+ migrations
получают железную гарантию: detector, читающий tree только через
extractors, литерально не имеет пути к answer-key полям.

TypedDict'ы здесь — hint-only. Detector'ы продолжают использовать
``.get()``-доступ; runtime-validation нет. TypedDict'ы существуют,
чтобы сделать сигнатуры extractor'ов и future detector'ов
self-documenting и дать ``mypy`` что проверять в Phase 27.2 при
миграции.

Почему ``reason`` помечен answer-key для ``embedded_errors``: текущие
6 detector'ов используют ``error.get("reason")`` как pass-through
rationale в output dict'ах — это форма cheat'а «copy answer-key
author's text into engine output» вместо синтеза собственного
объяснения. Stripping в extractor'е prevent'ит этот pattern в
Phase 27.2+ migration'ах. Existing detector'ы сохраняют свою
private logic (они не используют extractors), так что behavior не
меняется.
"""

from __future__ import annotations

from typing import Any, TypedDict

ANSWER_KEY_TOP_LEVEL_FIELDS: frozenset[str] = frozenset(
    {
        "expected_engine_flags",
        "expected_confidence_outputs",
        "ground_truth_annotations",
        "expected_reasoning_chain",
    }
)
"""Top-level ключи tree-fixture'а, которые detector'ам читать запрещено.

Каждый ключ описывает expected output (flag list, per-relationship
status/confidence, ground-truth lineage, reasoning chain) — это
``answer key``, не входное evidence."""

ANSWER_KEY_NESTED_FIELDS: dict[str, frozenset[str]] = {
    "embedded_errors": frozenset(
        {
            "expected_flag",
            "expected_confidence_when_flagged",
            "reason",
        }
    ),
    "input_archive_snippets": frozenset({"expected_use"}),
    "input_dna_matches": frozenset({"expected_link"}),
    "input_user_assertions": frozenset(),
}
"""Map ``list-field-on-tree -> sub-fields, удаляемые из каждого item``.

``evidence.extractors`` применяют этот mapping при возврате list'ов:
returned dicts физически не содержат указанных ключей.
"""


class EmbeddedError(TypedDict, total=False):
    """Один item в ``tree["embedded_errors"]`` после strip'а answer-key.

    NOTE: ``expected_flag`` / ``expected_confidence_when_flagged`` /
    ``reason`` намеренно отсутствуют — см.
    ``ANSWER_KEY_NESTED_FIELDS``.
    """

    type: str
    subtype: str
    persons: list[str]
    person_id: str
    snippet_id: str
    match_id: str


class ArchiveSnippet(TypedDict, total=False):
    """Один item в ``tree["input_archive_snippets"]`` после strip'а.

    NOTE: ``expected_use`` намеренно отсутствует.
    """

    snippet_id: str
    type: str
    year: int
    place: str
    transcription_excerpt: str
    language: str
    primary_or_derivative: str
    image_available: bool


class DNAMatch(TypedDict, total=False):
    """Один item в ``tree["input_dna_matches"]`` после strip'а.

    NOTE: ``expected_link`` намеренно отсутствует.
    """

    match_id: str
    match_name: str
    platform: str
    shared_cm: float
    longest_segment: float
    segment_count: int
    shared_matches_with: list[str]
    triangulated_segments: list[dict[str, Any]]


class UserAssertion(TypedDict, total=False):
    """Один item в ``tree["input_user_assertions"]``.

    User assertions не содержат answer-key sub-полей — все их
    ключи (``assertion``, ``scope``, ``evidence``) — input evidence
    (то, что пользователь сообщил о persons), которое detector'ы
    должны читать.
    """

    person_id: str
    assertion: str
    scope: str
    evidence: str


__all__ = [
    "ANSWER_KEY_NESTED_FIELDS",
    "ANSWER_KEY_TOP_LEVEL_FIELDS",
    "ArchiveSnippet",
    "DNAMatch",
    "EmbeddedError",
    "UserAssertion",
]
