"""Async-обёртка над Anthropic Claude API.

Дизайн (см. ADR-0043 §«Anthropic vs OpenAI vs Mistral»):

- **Async-only.** Никаких sync-вариантов; downstream — FastAPI и arq, оба
  async-native. Тесты пользуются ``pytest-asyncio``.
- **Injectable клиент.** Конструктор принимает опциональный
  ``anthropic.AsyncAnthropic`` — это позволяет тестам передавать stub
  без monkey-patch, а production-коду полагаться на дефолтную
  инстанциализацию из конфига.
- **Structured output через JSON.** Anthropic SDK 0.40+ поддерживает
  ``messages.create(..., response_format={"type": "json_object"})`` —
  но для phase-10.0 skeleton мы оставляем интерфейс провайдер-агностичным:
  caller передаёт Pydantic-модель, обёртка парсит ответ через
  ``model_validate_json``. Это даёт детерминированный fail-fast
  (Pydantic ValidationError) если LLM нарушил схему.
- **Retries.** Делегируются SDK (``max_retries`` параметр клиента) —
  не реимплементируем backoff-loop руками.
- **Rate-limit aware.** SDK возвращает ``RateLimitError`` через retry-loop;
  если retries исчерпаны — поднимается наверх к caller'у. Cost-control —
  ответственность caller'а (Phase 10.1+ добавит budget enforcement).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ai_layer.config import AILayerConfig, AILayerConfigError, AILayerDisabledError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


@dataclass(frozen=True)
class ImageInput:
    """Image-блок для vision-режима ``complete_structured``.

    Attributes:
        data_b64: Base64-кодированные байты изображения. Caller сам
            кодирует — wrapper не делает file-IO.
        media_type: ``image/jpeg``, ``image/png``, ``image/gif`` или
            ``image/webp`` (поддерживаемые Anthropic vision API).
    """

    data_b64: str
    media_type: str


class AnthropicCompletion[T: BaseModel](BaseModel):
    """Результат structured-вызова Claude.

    Attributes:
        parsed: Распарсенная Pydantic-модель ответа.
        model: Имя модели, которая обслужила запрос (для аудита и
            биллинга — Anthropic возвращает actual-model в response).
        input_tokens: Сколько токенов потратили на промпт.
        output_tokens: Сколько токенов сгенерировал ассистент.
        stop_reason: ``end_turn`` / ``max_tokens`` / ``stop_sequence`` —
            сигнал caller'у, был ли ответ truncated.
    """

    parsed: T
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None


class AnthropicClient:
    """Async-обёртка для structured-вызовов Claude API.

    Args:
        config: Конфигурация (API key, default model, kill-switch).
        client: Опциональный ``anthropic.AsyncAnthropic``. Если ``None`` —
            создаётся лениво при первом вызове из ``config.anthropic_api_key``.
            Тесты передают stub, чтобы не зависеть от ENV.
    """

    def __init__(
        self,
        config: AILayerConfig,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._config = config
        self._client = client

    async def complete_structured[T: BaseModel](
        self,
        *,
        system: str,
        user: str,
        response_model: type[T],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        image: ImageInput | None = None,
    ) -> AnthropicCompletion[T]:
        """Сделать вызов Claude и распарсить ответ в ``response_model``.

        Args:
            system: System-промпт (обычно из ``PromptRegistry``).
            user: User-промпт (обычно из ``PromptRegistry``).
            response_model: Pydantic-класс ожидаемого структурированного ответа.
            model: Override модели; ``None`` → ``config.anthropic_model``.
            max_tokens: Лимит на ответ.
            temperature: ``0.0`` для детерминированности (skeleton-default).
            image: Опциональный image-input для vision-режима. Когда
                задан, в SDK отправляется content-list с image-block
                плюс text-block (см. Anthropic vision API docs). Phase
                10.2 / ADR-0059: используется ``SourceExtractor`` для
                сканов и фотографий низкокачественных документов.

        Raises:
            AILayerDisabledError: Если ``config.enabled is False``.
            AILayerConfigError: Если API-ключ не настроен.
            pydantic.ValidationError: Если LLM вернул JSON, не соответствующий
                ``response_model``. Caller отвечает за обработку.
        """
        if not self._config.enabled:
            msg = "AI_LAYER_ENABLED is false; refusing to call Anthropic API"
            raise AILayerDisabledError(msg)

        client = self._get_client()
        chosen_model = model or self._config.anthropic_model

        user_content: str | list[dict[str, Any]]
        if image is None:
            user_content = user
        else:
            # Vision-формат SDK: content — список блоков. Image кладём
            # первым, чтобы text-инструкция (отсылка к "image above")
            # читалась естественно.
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": image.data_b64,
                    },
                },
                {"type": "text", "text": user},
            ]

        response = await client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

        text = _extract_text(response)
        parsed = response_model.model_validate_json(text)

        # Generic-параметризация runtime'ом не нужна: Pydantic v2 делает
        # type check через annotation generic-класса и будет валидировать
        # ``parsed`` как ``BaseModel``-инстанс. Caller получает корректный
        # статический тип через type-hint на месте присваивания.
        return AnthropicCompletion(
            parsed=parsed,
            model=getattr(response, "model", chosen_model),
            input_tokens=_usage_field(response, "input_tokens"),
            output_tokens=_usage_field(response, "output_tokens"),
            stop_reason=getattr(response, "stop_reason", None),
        )

    def _get_client(self) -> AsyncAnthropic:
        """Лениво создать ``AsyncAnthropic`` или вернуть ранее переданный.

        Импорт SDK выполняется здесь (а не на уровне модуля), чтобы
        ``import ai_layer`` работал без установленного ``anthropic``
        в окружении, где enabled=false.
        """
        if self._client is not None:
            return self._client

        if not self._config.anthropic_api_key:
            msg = "ANTHROPIC_API_KEY is not set; cannot instantiate Anthropic client"
            raise AILayerConfigError(msg)

        # Лениво: ``anthropic`` не должен загружаться, если AI_LAYER_ENABLED=false
        # (CI / dev без ключа). PLC0415 — намеренное исключение.
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        self._client = AsyncAnthropic(api_key=self._config.anthropic_api_key)
        return self._client


def _extract_text(response: Any) -> str:
    """Извлечь текстовый контент из ``Message`` SDK.

    SDK ≥0.40 возвращает ``content: list[ContentBlock]`` с разными типами
    (TextBlock / ToolUseBlock). Skeleton использует только text-блоки —
    конкатенируем их в одну строку.
    """
    content = getattr(response, "content", None)
    if not content:
        msg = "Anthropic response has empty content"
        raise ValueError(msg)

    parts: list[str] = []
    for block in content:
        text_attr = getattr(block, "text", None)
        if text_attr is not None:
            parts.append(text_attr)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    if not parts:
        msg = "Anthropic response has no text blocks"
        raise ValueError(msg)
    return "".join(parts)


def _usage_field(response: Any, name: str) -> int:
    """Достать счётчик токенов из ``response.usage`` робастно к моку."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return int(getattr(usage, name, 0) or 0)
