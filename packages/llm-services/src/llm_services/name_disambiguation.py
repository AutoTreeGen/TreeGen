"""LLM-операция: группировка вариантов имени одного человека.

`disambiguate_name_variants(variants)` отправляет промпт
``name_disambiguation.txt`` в Claude с structured-output и возвращает
список ``NameCluster``.

Use case: импортирует GED-файл со списком людей, у некоторых записан
«Vladimir», у других «Volodya», «Володя». Phase 3.4 entity-resolution
делает фонетический матч (Daitch-Mokotoff) — но для transliteration
+ diminutive'ов это работает плохо. LLM добавляет реальное языковое
понимание там, где фонетика провалилась.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from llm_services.client import DEFAULT_MODEL, claude_client
from llm_services.prompts import load_prompt
from llm_services.types import NameCluster

_CLUSTERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "variants": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["canonical", "variants", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}

_MAX_TOKENS = 2048
"""Список кластеров. 2K токенов хватает на ~30–50 имён."""


async def disambiguate_name_variants(
    variants: list[str],
    *,
    client: AsyncAnthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> list[NameCluster]:
    """Сгруппировать варианты имени в кластеры одного человека.

    Args:
        variants: Список имён-вариантов («Vladimir», «Volodya», «Володя», «Yaakov», ...).
        client: Готовый ``AsyncAnthropic`` (для тестов / DI).
        model: Override модели (default — ``claude-sonnet-4-6``).

    Returns:
        Список ``NameCluster``. Объединение `cluster.variants` по всем
        кластерам — точное множество входных `variants` (контракт
        промпта: каждый вариант ровно в одном кластере).

    Raises:
        ValueError: Если список variants пуст.
        MissingApiKeyError / APIError / ValidationError: см. ``normalize_place_name``.
    """
    if not variants:
        msg = "variants must contain at least one name"
        raise ValueError(msg)

    _, prompt_body = load_prompt("name_disambiguation")
    rendered = prompt_body.format(
        variants=json.dumps(variants, ensure_ascii=False),
    )

    api = client if client is not None else claude_client()

    response = await api.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        thinking={"type": "disabled"},
        output_config={
            "format": {
                "type": "json_schema",
                "schema": _CLUSTERS_SCHEMA,
            }
        },
        messages=[{"role": "user", "content": rendered}],
    )

    text_block = next(b for b in response.content if b.type == "text")
    payload = json.loads(text_block.text)
    return [NameCluster.model_validate(c) for c in payload["clusters"]]


__all__ = ["disambiguate_name_variants"]
