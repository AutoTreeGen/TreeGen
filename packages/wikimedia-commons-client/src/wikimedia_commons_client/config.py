"""Конфигурация Wikimedia Commons API.

Дефолт endpoint'а — публичный production
(``https://commons.wikimedia.org/w/api.php``); у Wikimedia нет sandbox-
окружения, тестируем через httpx-mock.

User-Agent **обязателен** (см.
<https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy>):
дефолтное значение клиента — заглушка на случай unit-тестов; в
production caller передаёт собственное значение, идентифицирующее
TreeGen + контакт.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_API_URL = "https://commons.wikimedia.org/w/api.php"

# Дефолтный UA — для тестов и dev-окружений. В production parser-service
# собирает UA из настроек (имя приложения + git-tag + email мейнтейнера).
DEFAULT_USER_AGENT = (
    "AutoTreeGen/0.1 (+https://github.com/AutoTreeGen/TreeGen) wikimedia-commons-client/0.1"
)


@dataclass(frozen=True, kw_only=True, slots=True)
class WikimediaCommonsConfig:
    """Endpoint + User-Agent для Wikimedia Commons API.

    Attributes:
        api_url: URL MediaWiki Action API endpoint'а (один на read и
            write — Commons различает по параметру ``action``).
        user_agent: Полная User-Agent-строка по политике WMF.
            Generic UA (curl/python-requests) → 403.
        timeout: HTTP timeout для одного запроса, секунды. Conservative
            дефолт — Commons обычно отвечает за 200–500 мс, но при
            больших iiprop-наборах может задержаться.
    """

    api_url: str = DEFAULT_API_URL
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 30.0

    def __post_init__(self) -> None:
        # Защита от пустого UA — сэкономит цикл «отправил → 403 → разобрал
        # → перезапустил» в проде.
        if not self.user_agent or not self.user_agent.strip():
            msg = "WikimediaCommonsConfig.user_agent must be non-empty (WMF UA policy)"
            raise ValueError(msg)
