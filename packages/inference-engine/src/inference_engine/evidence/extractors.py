"""Phase 27.1 — safe accessors для tree-fixture list-полей.

Каждый extractor:

- Возвращает ``list[<TypedDict>]`` с filtered non-dict items.
- Возвращает пустой list при отсутствии ключа / non-list value /
  если все items не-dict.
- **Strip'ает nested answer-key sub-поля** из каждого item
  (см. ``primitives.ANSWER_KEY_NESTED_FIELDS``). Returned dicts
  shallow-copied — input tree не мутируется.

Это значит: detector, читающий tree только через эти функции,
литерально не имеет пути к ``embedded_errors[].expected_flag`` или
``input_archive_snippets[].expected_use`` и т.д. Phase 27.2+
migrations получают этот guarantee автоматически.

Existing Phase 26 detector'ы НЕ используют этот модуль — у них есть
private accessors. Их behavior не меняется. Diagnostic test (см.
``tests/test_phase_27_1_evidence_primitives.py``) фиксирует
текущий cheat surface, чтобы migration'ы можно было tracking'ать.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from inference_engine.evidence.primitives import (
    ANSWER_KEY_NESTED_FIELDS,
    ArchiveSnippet,
    DNAMatch,
    EmbeddedError,
    UserAssertion,
)

_DEFAULT_TEXT_FIELDS: tuple[str, ...] = (
    "transcription_excerpt",
    "type",
    "language",
)
"""Default-поля, по которым ``combined_snippet_text`` собирает текст.

Совпадает с de-facto pattern в 4 из 5 копий ``_combined_text`` в
существующих detector'ах. ``metric_book_ocr`` использует другой
набор — он передаст свой ``fields`` явно при миграции.
"""


def embedded_errors(tree: Mapping[str, Any]) -> list[EmbeddedError]:
    """``tree["embedded_errors"]`` без answer-key sub-полей.

    Strip'ает: ``expected_flag``, ``expected_confidence_when_flagged``,
    ``reason``. См. ``primitives.ANSWER_KEY_NESTED_FIELDS``.
    """
    return _extract_stripped(tree, "embedded_errors")  # type: ignore[return-value]


def archive_snippets(tree: Mapping[str, Any]) -> list[ArchiveSnippet]:
    """``tree["input_archive_snippets"]`` без ``expected_use``."""
    return _extract_stripped(tree, "input_archive_snippets")  # type: ignore[return-value]


def dna_matches(tree: Mapping[str, Any]) -> list[DNAMatch]:
    """``tree["input_dna_matches"]`` без ``expected_link``."""
    return _extract_stripped(tree, "input_dna_matches")  # type: ignore[return-value]


def user_assertions(tree: Mapping[str, Any]) -> list[UserAssertion]:
    """``tree["input_user_assertions"]`` (нечего strip'ать)."""
    return _extract_stripped(tree, "input_user_assertions")  # type: ignore[return-value]


def combined_snippet_text(
    snippets: Iterable[Mapping[str, Any]],
    fields: Sequence[str] = _DEFAULT_TEXT_FIELDS,
) -> str:
    """Конкатенация значений ``fields`` каждого snippet'а через ``\\n``.

    Default ``fields`` совпадает с de-facto pattern существующих
    detector'ов (см. ``_DEFAULT_TEXT_FIELDS``). Non-str значения
    silently skipped.

    Pure-функция: не читает tree-level answer-key. Snippets уже
    должны быть пропущены через ``archive_snippets()`` (или
    эквивалент), так что ``expected_use`` уже не там.
    """
    parts: list[str] = []
    for snippet in snippets:
        for key in fields:
            value = snippet.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def _extract_stripped(tree: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """Internal: filter list, strip nested answer-key sub-fields, shallow-copy.

    Возвращает new list of new dicts; input tree не мутируется.
    """
    raw = tree.get(key)
    if not isinstance(raw, list):
        return []
    forbidden = ANSWER_KEY_NESTED_FIELDS.get(key, frozenset())
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        # Shallow copy if no strip-set; otherwise build copy without forbidden keys.
        cleaned = {k: v for k, v in item.items() if k not in forbidden} if forbidden else dict(item)
        out.append(cleaned)
    return out


__all__ = [
    "archive_snippets",
    "combined_snippet_text",
    "dna_matches",
    "embedded_errors",
    "user_assertions",
]
