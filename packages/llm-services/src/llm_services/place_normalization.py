"""LLM-операция: канонизация исторических топонимов.

`normalize_place_name(raw, context)` отправляет промпт ``place_normalization.txt``
в Claude с structured-output (json_schema) и возвращает валидированный
``NormalizedPlace``.

Cost-aware: рассчитан на вызов из inference-engine только в gray-zone
случаях (см. ``llm_place`` rule, Phase 10.0). Для full-corpus
batch-канонизации (Phase 10.x) использовать Batches API напрямую — не
этот hot-path вход.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from llm_services.client import DEFAULT_MODEL, claude_client
from llm_services.prompts import load_prompt
from llm_services.types import NormalizedPlace

_NORMALIZED_PLACE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Short canonical place name."},
        "country_code": {
            "type": ["string", "null"],
            "description": "ISO 3166-1 alpha-2 (modern sovereign state) or null.",
        },
        "historical_period": {
            "type": ["string", "null"],
            "description": "Free-form historical period label or null.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the normalization, [0.0, 1.0].",
        },
    },
    "required": ["name", "country_code", "historical_period", "confidence"],
    "additionalProperties": False,
}

_MAX_TOKENS = 512
"""Канонизация одного места — короткий ответ. 512 токенов с запасом."""


async def normalize_place_name(
    raw: str,
    context: dict[str, Any] | None = None,
    *,
    client: AsyncAnthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> NormalizedPlace:
    """Канонизировать сырое название места через LLM.

    Args:
        raw: Исходная строка («Slonim, Russian Empire», «Слоним», «Slonim, BLR»).
        context: Дополнительный контекст для промпта — например,
            ``{"person_birth_year": 1880}``. Помогает LLM выбрать
            правильный исторический период. Может быть ``None``.
        client: Готовый ``AsyncAnthropic`` (для тестов / dependency injection).
            По умолчанию создаётся через ``claude_client()``.
        model: Override модели (default — ``claude-sonnet-4-6``).

    Returns:
        Валидированный ``NormalizedPlace``.

    Raises:
        MissingApiKeyError: Если ``client`` не передан и ``ANTHROPIC_API_KEY``
            не задан в окружении.
        anthropic.APIError: Любая ошибка SDK (rate limit, server error и т.п.).
            SDK уже ретраит 429/5xx по policy в ``claude_client()``.
        pydantic.ValidationError: Если LLM вернул JSON, не проходящий
            валидацию ``NormalizedPlace`` (теоретически невозможно при
            ``json_schema``-режиме, но guard на случай мутации схемы).
    """
    _, prompt_body = load_prompt("place_normalization")
    rendered = prompt_body.format(
        context=json.dumps(context or {}, ensure_ascii=False, sort_keys=True),
        raw=raw,
    )

    api = client if client is not None else claude_client()

    response = await api.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        thinking={"type": "disabled"},
        output_config={
            "format": {
                "type": "json_schema",
                "schema": _NORMALIZED_PLACE_SCHEMA,
            }
        },
        messages=[{"role": "user", "content": rendered}],
    )

    # Structured-output гарантирует, что первый text-блок содержит валидный JSON.
    text_block = next(b for b in response.content if b.type == "text")
    payload = json.loads(text_block.text)
    return NormalizedPlace.model_validate(payload)


__all__ = ["normalize_place_name"]
