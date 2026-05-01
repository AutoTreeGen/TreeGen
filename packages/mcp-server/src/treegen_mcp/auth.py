"""API-key аутентификация для MCP-сервера.

Пользователь вставляет свой AutoTreeGen API key в конфиг MCP-host'а
(Claude Desktop / ChatGPT). Сервер читает его из env ``TREEGEN_API_KEY``
и шлёт как ``Authorization: Bearer <key>`` на каждый HTTP-запрос.

Ключ хранится в отдельном ``ApiCredentials`` чтобы:

* не попадал в ``repr()`` config'а (см. ``config.py``);
* можно было лениво проверить наличие на старте сервера и выдать
  понятное ``MissingApiKeyError`` вместо мутного 401.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_API_KEY = "TREEGEN_API_KEY"


class MissingApiKeyError(RuntimeError):
    """``TREEGEN_API_KEY`` не задан — сервер не может авторизовать запросы."""


@dataclass(frozen=True, slots=True)
class ApiCredentials:
    """Bearer-token для AutoTreeGen API.

    Attributes:
        api_key: Сырой ключ. Через ``__repr__`` не светится — заменён
            на префикс из 4 символов + маску (``atg_live_xxxx****``).
    """

    api_key: str

    def __repr__(self) -> str:
        # Не утечь ключ в логи. Сохраняем 4 первых символа для
        # отличения dev/prod ключей и маскируем хвост.
        prefix_len = 4
        if len(self.api_key) <= prefix_len:
            return "ApiCredentials(api_key='****')"
        return f"ApiCredentials(api_key='{self.api_key[:prefix_len]}****')"

    def auth_header(self) -> dict[str, str]:
        """Возвращает HTTP-заголовок ``Authorization: Bearer ...``."""
        return {"Authorization": f"Bearer {self.api_key}"}


def load_credentials(env: dict[str, str] | None = None) -> ApiCredentials:
    """Читает ``TREEGEN_API_KEY`` из env и возвращает :class:`ApiCredentials`.

    Args:
        env: Словарь env (для тестов). По умолчанию — ``os.environ``.

    Raises:
        MissingApiKeyError: Если ``TREEGEN_API_KEY`` не задан или пуст.
    """
    source = env if env is not None else dict(os.environ)
    raw = source.get(ENV_API_KEY, "").strip()
    if not raw:
        msg = (
            f"{ENV_API_KEY} environment variable is required. "
            f"Set it in your MCP host config (e.g. claude_desktop_config.json) "
            f"to your AutoTreeGen API key."
        )
        raise MissingApiKeyError(msg)
    return ApiCredentials(api_key=raw)
