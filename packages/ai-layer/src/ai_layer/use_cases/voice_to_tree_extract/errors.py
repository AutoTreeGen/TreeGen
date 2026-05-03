"""Исключения voice extraction use-case'а.

Только cost-cap бросает; pass-failure → сохраняется в результате как
``status=partial_failed`` (ADR-0075 §«Failure handling»).
"""

from __future__ import annotations


class VoiceExtractError(RuntimeError):
    """Базовый класс — для catch-all в caller'ах (worker)."""


class VoiceExtractCostCapError(VoiceExtractError):
    """Pre-flight cost-cap превышен — отмена ДО Anthropic-вызова.

    Caller (worker) ловит это, помечает extraction_job_id status=cost_capped
    в provenance, не сохраняет proposals.
    """


__all__ = ["VoiceExtractCostCapError", "VoiceExtractError"]
