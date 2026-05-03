"""Phase 27.1 — diagnostic helpers для anti-cheat regression тестов.

``poison_answer_key(tree)`` возвращает deep-copy с garbage'ом во всех
documented answer-key полях (top-level + nested), но input-evidence
поля не трогает. Detector, чей output меняется между ``run_tree(tree)``
и ``run_tree(poison_answer_key(tree))``, читает answer key — это и
есть cheat regression.

``assert_detector_ignores_answer_key(detect, tree)`` — convenience
для конкретного detector'а: вызывает ``detect`` дважды (clean +
poisoned) и assert'ит equality.

Эти helpers намеренно живут в ``tests/`` (не в ``inference_engine/``),
потому что они нужны только для тестов и не должны попасть в
production-import path.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from inference_engine.evidence.primitives import (
    ANSWER_KEY_NESTED_FIELDS,
    ANSWER_KEY_TOP_LEVEL_FIELDS,
)

ANSWER_KEY_GARBAGE: str = "__phase_27_1_poison__"
"""Sentinel-значение, которым заполняются answer-key поля в
``poison_answer_key``. Любой detector, чей output содержит эту
строку, очевидно читает answer-key (не просто реагирует на её
присутствие — копирует значение в свой output)."""


def poison_answer_key(tree: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy ``tree`` с garbage'ом во всех answer-key полях.

    Top-level ``ANSWER_KEY_TOP_LEVEL_FIELDS`` ставятся в
    ``ANSWER_KEY_GARBAGE``. Nested ``ANSWER_KEY_NESTED_FIELDS`` —
    каждое sub-поле в каждом list-item также ставится в garbage
    (если присутствует).

    Input-evidence поля (``input_gedcom_excerpt``,
    ``input_dna_matches``[non-answer-key sub-поля],
    ``input_archive_snippets``[non-answer-key sub-поля], …) не
    трогаются — детектор по-прежнему получает все настоящие input'ы.
    """
    poisoned: dict[str, Any] = copy.deepcopy(dict(tree))

    for key in ANSWER_KEY_TOP_LEVEL_FIELDS:
        if key in poisoned:
            poisoned[key] = ANSWER_KEY_GARBAGE

    for list_key, forbidden_subfields in ANSWER_KEY_NESTED_FIELDS.items():
        if not forbidden_subfields:
            continue
        items = poisoned.get(list_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for subfield in forbidden_subfields:
                if subfield in item:
                    item[subfield] = ANSWER_KEY_GARBAGE

    return poisoned


def assert_detector_ignores_answer_key(
    detect: Callable[[dict[str, Any]], Any],
    tree: Mapping[str, Any],
) -> None:
    """Assert: ``detect(tree) == detect(poison_answer_key(tree))``.

    Generic anti-overfit guard. Migrated detector'ы (Phase 27.2+)
    добавляют один вызов этой функции в свой test-файл и автоматически
    защищены от регрессии «detector начал читать answer key».

    На Phase 27.1 эта функция используется в diagnostic-тесте, чтобы
    pin'ить текущий cheat surface (см.
    ``test_phase_27_1_evidence_primitives.py``
    ``KNOWN_ANSWER_KEY_CONSUMERS``).
    """
    clean = detect(copy.deepcopy(dict(tree)))
    poisoned = detect(poison_answer_key(tree))
    assert clean == poisoned, (
        "detector output differs between clean and poisoned answer-key "
        "fixture — detector is reading answer-key fields"
    )


__all__ = [
    "ANSWER_KEY_GARBAGE",
    "assert_detector_ignores_answer_key",
    "poison_answer_key",
]
