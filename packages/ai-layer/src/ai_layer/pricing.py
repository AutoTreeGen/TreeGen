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


# Phase 10.2b — pre-flight cost estimation для per-source $ cap.

# Approximate chars-per-token для англо-русско-польско-ивритского текста:
# Anthropic tokenizer документов на этих языках в среднем ~4 chars / token
# (latin) и ~2 chars / token (кириллица/иврит). Берём 3 как смешанный
# default — чуть пессимистично для англ. текстов, но safer для двуязычных.
_AVG_CHARS_PER_TOKEN: float = 3.0

# Image-tokens на одну vision-image (Anthropic vision API spec на 2026-04):
# ≈ 1568 tokens для image ≤ 1568×1568 px, scale-up для бо́льших разрешений.
# После image_preprocessing.preprocess_image мы downscaling'аем до
# MAX_DIMENSION_PX = 2048; берём 2200 как conservative ceiling — лучше
# слегка переоценить, чем разрешить превышение per-source cap'а.
_IMAGE_TOKENS_CEILING: int = 2200

# System+template prompt в source_extractor_v1 ~3500 chars фиксированно
# (см. source_extractor_v1.md). Округляем вверх до целых сотен.
_PROMPT_OVERHEAD_TOKENS: int = 1200


def estimate_input_tokens_from_text(text_length_chars: int) -> int:
    """Оценить input tokens по длине user-text'а в символах.

    Прибавляет ``_PROMPT_OVERHEAD_TOKENS`` за system+template — caller
    не должен сам считать накладные.
    """
    user_text_tokens = int(text_length_chars / _AVG_CHARS_PER_TOKEN) + 1
    return user_text_tokens + _PROMPT_OVERHEAD_TOKENS


def estimate_input_tokens_from_image(*, ocr_text_hint_length_chars: int = 0) -> int:
    """Оценить input tokens для vision-вызова.

    Args:
        ocr_text_hint_length_chars: Длина опционального OCR-hint'а
            (caller передаёт, если использует gradient text+image).
            ``0`` — vision-only режим.
    """
    text_tokens = (
        int(ocr_text_hint_length_chars / _AVG_CHARS_PER_TOKEN) + 1
        if ocr_text_hint_length_chars > 0
        else 0
    )
    return _IMAGE_TOKENS_CEILING + text_tokens + _PROMPT_OVERHEAD_TOKENS


def estimate_extraction_cost_usd(
    *,
    model: str,
    estimated_input_tokens: int,
    max_output_tokens: int,
) -> float:
    """Pre-flight cost-cap для одного source-extraction вызова.

    ``max_output_tokens`` — потолок, который мы передадим Claude'у (т.е.
    SourceExtractor.max_tokens). Реальный output может быть меньше, но
    для cap-проверки берём worst-case.
    """
    return estimate_cost_usd(
        model=model,
        input_tokens=estimated_input_tokens,
        output_tokens=max_output_tokens,
    )
