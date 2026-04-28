"""Anthropic Claude API client wrapper.

Thin wrapper над ``anthropic.AsyncAnthropic``: задаёт безопасные defaults
для timeout / retry / model и читает ``ANTHROPIC_API_KEY`` из окружения,
если ключ не передан явно.

Дизайн-решения:

* **Async-only.** Все LLM-операции в AutoTreeGen вызываются из async
  контекста (FastAPI handlers, arq workers). Sync-клиент не нужен —
  лишний код-путь без потребителей.
* **Default model — claude-sonnet-4-6.** Sonnet 4.6 даёт лучший
  intelligence/cost trade-off для нормализации мест и группировки имён,
  где требуется reasoning, но не «max-quality long-horizon». Для
  hypothesis explainer (Phase 10.4) можно поднять до Opus 4.7 точечно.
* **`thinking={"type": "disabled"}` по умолчанию.** Текущие use-cases
  (классификация, JSON-extraction) — short-form, thinking только
  увеличивает latency и cost без видимой пользы. Если конкретный prompt
  захочет reasoning — передаст параметр явно.
* **Retry — встроенный SDK retry (default `max_retries=2`)**, плюс
  custom timeout. Не перепиливаем экспоненциальный backoff поверх — SDK
  уже делает это правильно (см. claude-api skill: «Always use SDK retry,
  not custom»).

См. ADR-0030 §«Client wrapper» для полного rationale.
"""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

DEFAULT_MODEL = "claude-sonnet-4-6"
"""Дефолтная модель для всех LLM-операций (см. модуль docstring).

Конкретный prompt может переопределить через параметр ``model=`` в
``messages.create(...)``.
"""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Timeout на один HTTP-запрос (в секундах).

30 секунд — компромисс: достаточно для structured-output ответа на
~16K tokens (Sonnet 4.6 streams ~150 t/s), но не парализует worker
при сетевом залипании. Для длинных операций (Phase 10.x research
assistant) использовать `with_options(timeout=...)`.
"""

DEFAULT_MAX_RETRIES = 2
"""SDK auto-retry на 408/409/429/5xx с exponential backoff."""


class MissingApiKeyError(RuntimeError):
    """ANTHROPIC_API_KEY не задан и не передан явно."""


def claude_client(
    api_key: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AsyncAnthropic:
    """Создать настроенный ``anthropic.AsyncAnthropic``.

    Args:
        api_key: API-ключ. Если ``None``, читается из
            ``os.environ["ANTHROPIC_API_KEY"]``. Передавать явно стоит
            только в тестах или при работе с несколькими ключами в одном
            процессе (multi-tenant SaaS, Phase 12+).
        timeout: Timeout одного HTTP-запроса в секундах.
        max_retries: Сколько раз SDK ретраит retryable-ошибки.

    Returns:
        Настроенный async-клиент Anthropic SDK.

    Raises:
        MissingApiKeyError: Ключ не передан и не найден в окружении.
            Намеренно отдельный класс ошибки (а не голый KeyError) —
            чтобы FastAPI middleware мог выдать 500 с понятным
            сообщением «AI features disabled: ANTHROPIC_API_KEY not set».
    """
    resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        msg = "ANTHROPIC_API_KEY is not set. Set it in your .env or pass api_key=... explicitly."
        raise MissingApiKeyError(msg)

    return AsyncAnthropic(
        api_key=resolved_key,
        timeout=timeout,
        max_retries=max_retries,
    )


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MissingApiKeyError",
    "claude_client",
]
