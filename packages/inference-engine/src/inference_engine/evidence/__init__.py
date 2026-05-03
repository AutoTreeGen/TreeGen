"""Phase 27.1 — shared evidence primitives для Phase 26.x detector'ов.

Public surface:

- ``embedded_errors(tree)`` / ``dna_matches(tree)`` /
  ``archive_snippets(tree)`` / ``user_assertions(tree)`` — safe
  accessors для tree-fixture list-полей. Strip'ают nested answer-key
  sub-поля (``expected_flag``, ``expected_use``, ``expected_link``,
  ``reason``, …).
- ``combined_snippet_text(snippets)`` — join ``transcription_excerpt``
  / ``type`` / ``language`` через ``\\n``. Default-поля можно override.
- ``ANSWER_KEY_TOP_LEVEL_FIELDS`` / ``ANSWER_KEY_NESTED_FIELDS`` —
  канонический список «какие fixture-поля — answer key».
- ``EmbeddedError`` / ``DNAMatch`` / ``ArchiveSnippet`` /
  ``UserAssertion`` — TypedDict-проекции (hint-only).

Phase 27.1 НЕ мигрирует detector'ы. Existing Phase 26 detector'ы
сохраняют свои private accessors. Phase 27.2+ migration'ы заменяют
``detectors._embedded_errors`` и т.д. на ``evidence.embedded_errors``
по одному detector'у за PR.

Builders для output dict-shape'ов (``relationship_claim``,
``merge_decision``, …) — deferred to Phase 27.2. См.
``evidence/builders.py``.
"""

from __future__ import annotations

from inference_engine.evidence.extractors import (
    archive_snippets,
    combined_snippet_text,
    dna_matches,
    embedded_errors,
    user_assertions,
)
from inference_engine.evidence.primitives import (
    ANSWER_KEY_NESTED_FIELDS,
    ANSWER_KEY_TOP_LEVEL_FIELDS,
    ArchiveSnippet,
    DNAMatch,
    EmbeddedError,
    UserAssertion,
)

__all__ = [
    "ANSWER_KEY_NESTED_FIELDS",
    "ANSWER_KEY_TOP_LEVEL_FIELDS",
    "ArchiveSnippet",
    "DNAMatch",
    "EmbeddedError",
    "UserAssertion",
    "archive_snippets",
    "combined_snippet_text",
    "dna_matches",
    "embedded_errors",
    "user_assertions",
]
