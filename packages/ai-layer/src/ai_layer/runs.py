"""Reusable AI-run conventions (Phase 10.2 / ADR-0059).

Этот модуль не тянет sqlalchemy и не определяет ORM-таблицы — каждый
use-case имеет собственную таблицу (``source_extractions``,
``hypothesis_runs``, ...). Здесь — только общие enum'ы и conventions
формата ``raw_response`` jsonb, чтобы analytics-/debug-tooling работало
с одинаковой shape для всех use-case'ов AI-слоя.

Пример use-case'а, использующего этот модуль:

    from ai_layer.runs import AIRunStatus, build_raw_response

    completion = await suggester.suggest(...)
    record.status = AIRunStatus.COMPLETED.value
    record.raw_response = build_raw_response(
        completion=completion,
        prompt_version="source_extractor_v1",
    )
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_layer.clients.anthropic_client import AnthropicCompletion


class AIRunStatus(StrEnum):
    """Lifecycle одной попытки AI-вызова.

    Хранится в БД как text (см. ADR-0003 §«Enums как text»).

    * ``PENDING`` — row создана, вызов ещё не выполнен (или idempotency
      placeholder для inflight reproject).
    * ``COMPLETED`` — Claude вернул валидный structured response,
      Pydantic схема прошла, fabricated-evidence guard прошёл.
    * ``FAILED`` — exception на любой стадии (network, validation,
      kill-switch, budget). ``error`` поле хранит текст.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


def build_raw_response(
    *,
    completion: AnthropicCompletion[Any],
    prompt_version: str,
    response_text: str | None = None,
) -> dict[str, Any]:
    """Сформировать ``raw_response`` jsonb для AI-run row.

    Структура зафиксирована для cross-use-case analytics:

    .. code-block:: json

        {
          "model": "claude-sonnet-4-6",
          "prompt_version": "source_extractor_v1",
          "stop_reason": "end_turn",
          "input_tokens": 1234,
          "output_tokens": 567,
          "response_text": "<the actual JSON Claude returned>",
          "parsed": { ... pydantic dump ... }
        }

    Args:
        completion: ``AnthropicCompletion`` от ``AnthropicClient``.
        prompt_version: Имя файла шаблона без ``.md`` — например,
            ``"source_extractor_v1"``. Понадобится при rollback'е prompt'а.
        response_text: Опциональный raw-text, который вернул Claude
            (полезно для debugging, если parsed-объект уже агрегирован).
            ``None`` → не записываем поле.
    """
    payload: dict[str, Any] = {
        "model": completion.model,
        "prompt_version": prompt_version,
        "stop_reason": completion.stop_reason,
        "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens,
        "parsed": completion.parsed.model_dump(mode="json"),
    }
    if response_text is not None:
        payload["response_text"] = response_text
    return payload


__all__ = ["AIRunStatus", "build_raw_response"]
