"""Kill-switch helpers для AI-слоя (Phase 10.2 / ADR-0059).

``AnthropicClient`` уже проверяет ``config.enabled`` внутри
``complete_structured`` — но для FastAPI-уровня нужен ранний gate, чтобы:

1. Endpoint не делал лишних DB-запросов и не создавал PENDING-row
   когда AI-слой выключен.
2. UI получал явный 503 «AI layer is disabled» вместо 500.

Этот модуль — тонкая обёртка, чтобы все use-case'ы AI-слоя имели один
способ сказать «отказ — kill switch».
"""

from __future__ import annotations

from collections.abc import Callable

from ai_layer.config import AILayerConfig, AILayerDisabledError


def ensure_ai_layer_enabled(config: AILayerConfig) -> None:
    """Бросить :class:`AILayerDisabledError` если ``config.enabled is False``.

    Вызывать в начале endpoint-handler'а / use-case'а до любых side
    effects (DB-write, network).
    """
    if not config.enabled:
        msg = "AI layer is disabled (AI_LAYER_ENABLED=false); refusing operation"
        raise AILayerDisabledError(msg)


def make_ai_layer_gate(
    get_config: Callable[[], AILayerConfig],
) -> Callable[[], None]:
    """Фабрика FastAPI-зависимости-гейта.

    Использование::

        from fastapi import Depends
        from ai_layer.gates import make_ai_layer_gate

        require_ai_enabled = make_ai_layer_gate(get_ai_layer_config)

        @router.post("/ai-extract", dependencies=[Depends(require_ai_enabled)])
        async def trigger_extract(...): ...

    Зависимость ничего не возвращает; единственный смысл —
    ``AILayerDisabledError`` (caller-уровневый exception handler
    конвертирует в 503).
    """

    def _gate() -> None:
        ensure_ai_layer_enabled(get_config())

    return _gate


__all__ = [
    "ensure_ai_layer_enabled",
    "make_ai_layer_gate",
]
