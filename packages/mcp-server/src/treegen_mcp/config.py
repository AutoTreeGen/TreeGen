"""Конфигурация MCP-сервера: загрузка endpoint'а и таймаутов из env.

API-ключ живёт отдельно (см. ``auth.py``) — чтобы случайно не утащить
его в логи через ``__repr__`` config-объекта.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
ENV_API_URL = "TREEGEN_API_URL"
ENV_TIMEOUT = "TREEGEN_API_TIMEOUT"


@dataclass(frozen=True, kw_only=True, slots=True)
class TreeGenConfig:
    """Endpoint AutoTreeGen API + сетевые параметры.

    Attributes:
        api_url: Базовый URL API gateway (без trailing slash).
        timeout_seconds: Таймаут HTTP-запросов. Дефолт — 30 секунд;
            достаточно для context-pack, у которого может быть LLM-стадия.
    """

    api_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        # rstrip — чтобы urljoin/строковая конкатенация не давала "//".
        # frozen=True → используем object.__setattr__.
        object.__setattr__(self, "api_url", self.api_url.rstrip("/"))


def load_config(env: dict[str, str] | None = None) -> TreeGenConfig:
    """Возвращает :class:`TreeGenConfig`, читая переменные окружения.

    Args:
        env: Словарь env (для тестов). По умолчанию — ``os.environ``.

    Returns:
        Конфигурация. ``TREEGEN_API_URL`` дефолтится на localhost:8000
        (parser-service в dev-mode).
    """
    source = env if env is not None else dict(os.environ)
    api_url = source.get(ENV_API_URL, DEFAULT_API_URL)
    timeout_raw = source.get(ENV_TIMEOUT)
    timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
    return TreeGenConfig(api_url=api_url, timeout_seconds=timeout)
