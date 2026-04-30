"""Pricing-таблица Anthropic Claude (Phase 10.1).

Цены — из публичной страницы Anthropic <https://www.anthropic.com/pricing>
на 2026-04-30, USD за 1M tokens (input / output, без cache discount).
Захардкожено намеренно: вычисление стоимости должно быть
детерминированным и audit-able через git history. При смене pricing
владелец проекта обновляет этот файл в отдельном PR.

Не покрываем здесь: prompt caching discount (5-min TTL), batch API
discount (-50%), Voyage embeddings (Phase 10.2). Если callsite использует
кеш — он применяет коэффициент сверху.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Стоимость 1M input / output tokens для одной модели."""

    input_per_mtok_usd: float
    output_per_mtok_usd: float


# Snapshot 2026-04-30. Sonnet 4.6 — рабочая лошадка, Opus 4.7 —
# fallback для high-confidence вызовов (Phase 10.5+).
PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(input_per_mtok_usd=3.0, output_per_mtok_usd=15.0),
    "claude-opus-4-7": ModelPricing(input_per_mtok_usd=15.0, output_per_mtok_usd=75.0),
    "claude-haiku-4-5-20251001": ModelPricing(input_per_mtok_usd=1.0, output_per_mtok_usd=5.0),
}

# Fallback для неизвестных моделей: используем Sonnet 4.6 как baseline.
# Это намеренно НЕ raise: pricing-таблица не должна валить production
# из-за нового нерегистрированного model-id.
_FALLBACK_KEY = "claude-sonnet-4-6"


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Оценить стоимость одного вызова в USD.

    Args:
        model: Имя модели (например, ``claude-sonnet-4-6``). Если модель
            не в таблице, используется fallback (с пометкой в логе вызывает
            caller).
        input_tokens: Входные токены (включая system + user prompt).
        output_tokens: Сгенерированные токены.

    Returns:
        Стоимость в USD. Формула: ``(in × $/MTok + out × $/MTok) / 1e6``.
    """
    pricing = PRICING.get(model, PRICING[_FALLBACK_KEY])
    cost = (
        input_tokens * pricing.input_per_mtok_usd + output_tokens * pricing.output_per_mtok_usd
    ) / 1_000_000
    return round(cost, 6)
