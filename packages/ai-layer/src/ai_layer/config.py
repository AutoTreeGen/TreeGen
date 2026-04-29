"""Конфигурация AI-слоя (Phase 10.0).

Читает переменные окружения и предоставляет единую точку доступа к настройкам
Anthropic / Voyage клиентов. ``AI_LAYER_ENABLED=false`` — master kill-switch:
любая попытка инстанцировать клиент с API-вызовами поднимет
``AILayerDisabledError``.

Дизайн-нота: используем простой ``dataclass``-pattern через Pydantic
``BaseModel`` без ``BaseSettings``, чтобы не тянуть pydantic-settings
в зависимости skeleton'а. Загрузка из ENV — явно через ``from_env``.
"""

from __future__ import annotations

import os
from typing import Final

from pydantic import BaseModel, Field

DEFAULT_ANTHROPIC_MODEL: Final[str] = "claude-sonnet-4-6"
DEFAULT_VOYAGE_MODEL: Final[str] = "voyage-3"


class AILayerConfig(BaseModel):
    """Снимок настроек AI-слоя.

    Attributes:
        enabled: Master kill-switch. Когда ``False``, клиенты отказываются
            делать сетевые вызовы (см. ``AILayerDisabledError``).
        anthropic_api_key: Ключ Claude API. ``None`` — клиент не может
            инициализироваться, но импорт пакета остаётся безопасным.
        anthropic_model: Default-модель для Anthropic; перезаписывается
            на уровне отдельного вызова.
        voyage_api_key: Ключ Voyage AI.
        voyage_model: Default-модель эмбеддингов.
    """

    enabled: bool = False
    anthropic_api_key: str | None = None
    anthropic_model: str = Field(default=DEFAULT_ANTHROPIC_MODEL)
    voyage_api_key: str | None = None
    voyage_model: str = Field(default=DEFAULT_VOYAGE_MODEL)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AILayerConfig:
        """Собрать конфиг из переменных окружения.

        Args:
            env: Опциональный dict-substitute для ``os.environ`` — нужен,
                чтобы тесты не зависели от живого env процесса.
        """
        source = env if env is not None else dict(os.environ)
        return cls(
            enabled=_parse_bool(source.get("AI_LAYER_ENABLED", "false")),
            anthropic_api_key=source.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=source.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL,
            voyage_api_key=source.get("VOYAGE_API_KEY") or None,
            voyage_model=source.get("VOYAGE_MODEL") or DEFAULT_VOYAGE_MODEL,
        )


class AILayerDisabledError(RuntimeError):
    """Поднимается при попытке вызвать LLM/embedding API с ``enabled=false``.

    Контракт: импорт клиента всегда безопасен; падение происходит только
    в момент реального сетевого вызова. Это позволяет тестам и CI грузить
    пакет без ENV-конфигурации.
    """


class AILayerConfigError(RuntimeError):
    """Ошибка конфигурации (например, отсутствует API-ключ при enabled=true)."""


def _parse_bool(value: str) -> bool:
    """Разбирает ENV-флаг как boolean (true/1/yes — True; иначе False)."""
    return value.strip().lower() in {"1", "true", "yes", "on"}
