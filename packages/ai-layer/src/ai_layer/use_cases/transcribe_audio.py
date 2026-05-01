"""AudioTranscriber — Phase 10.9a use case.

Тонкий orchestrator вокруг :class:`WhisperClient`: вызывает транскрипцию,
пишет одну запись Redis-телеметрии и возвращает чистый
:class:`TranscribeAudioOutput`. Все «жирные» решения (как мокать, как
ретраить, как считать стоимость) живут в WhisperClient + pricing —
этот слой только склеивает их с telemetry и формализует output-контракт
для caller'а (arq worker в parser-service, Phase 10.9a agent #10).

Ключевые свойства (см. ADR-0064 §G1):

- **Soft-fail.** ``run()`` НИКОГДА не бросает на ошибках
  WhisperClient — каждая категория (config / too-long / api) транслируется
  в ``TranscribeAudioOutput.error`` строкой. Caller (worker) маппит на
  ``AudioSession.status`` (`failed`) + ``error_message`` + опциональный
  retry в arq. Это выгоднее, чем raise: телеметрия пишется в обоих
  путях, и worker не ловит N разных exception-типов.
- **Telemetry — fire-and-forget.** Запись в Redis-list пишется и при
  success, и при failure (см. log_ai_usage). Failure пишется с
  ``cost_usd=0`` и ``audio_duration_sec=None`` — чтобы агрегаты были
  правдивы (биллим только успешные транскрипции).
- **Никакой ORM-зависимости.** Use-case принимает ``bytes`` и возвращает
  Pydantic — caller сам делает persistence. Это держит ai-layer без
  sqlalchemy / shared-models (см. ADR-0043 §«Layer boundaries»).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from ai_layer.clients.whisper import (
    AudioTooLongError,
    TranscriptResult,
    WhisperApiError,
    WhisperClient,
    WhisperConfigError,
    WhisperError,
)
from ai_layer.telemetry import log_ai_usage

# Стандартный provider-string в outputе. Phase 10.9.x введёт
# `self-hosted-whisper-large-v3` — тогда вынести в config.
_PROVIDER_OPENAI: str = "openai-whisper-1"
_USE_CASE_NAME: str = "transcribe_audio"

_logger = logging.getLogger(__name__)


class _RedisLike(Protocol):
    """Минимальный async-Redis протокол (совпадает с ``telemetry._RedisLike``).

    Дублируем здесь, чтобы caller не зависел от приватного имени из
    модуля telemetry.
    """

    async def lpush(self, name: str, *values: str) -> object: ...
    async def expire(self, name: str, time: int) -> object: ...


@dataclass(frozen=True, slots=True)
class TranscribeAudioInput:
    """Вход AudioTranscriber.

    Attributes:
        audio_bytes: Сырые байты файла.
        mime_type: MIME-тип (``audio/webm``, ``audio/ogg``, ...).
        language_hint: Опциональный ISO-639 код языка для ускорения
            inference Whisper'а.
    """

    audio_bytes: bytes
    mime_type: str
    language_hint: str | None = None


@dataclass(frozen=True, slots=True)
class TranscribeAudioOutput:
    """Результат AudioTranscriber.

    Attributes:
        transcript: Расшифрованный текст. Пустая строка при ошибке —
            caller должен смотреть на ``error``.
        language: ISO-639 код языка из ответа Whisper. ``None`` —
            не определён или ошибка.
        duration_sec: Реальная длительность аудио (из API). ``None`` —
            не вернулась или ошибка.
        provider: Канонический provider-string (``openai-whisper-1``) —
            пишется в ``AudioSession.transcript_provider``.
        model_version: Имя модели (``whisper-1``) — пишется в
            ``AudioSession.transcript_model_version``.
        cost_usd: Стоимость транскрипции в USD как :class:`Decimal`.
            ``Decimal("0")`` при ошибке (биллим только success).
        error: ``None`` при успехе; строка с категорией+сообщением
            при soft-fail. Категории:
            - ``"config:..."`` — WhisperConfigError;
            - ``"audio_too_long:..."`` — AudioTooLongError;
            - ``"api:..."`` — WhisperApiError;
            - ``"unexpected:..."`` — non-WhisperError exception (баг).
    """

    transcript: str
    language: str | None
    duration_sec: float | None
    provider: str
    model_version: str
    cost_usd: Decimal
    error: str | None = None


class AudioTranscriber:
    """Use-case ``AudioTranscriber``.

    Args:
        client: Инстанс :class:`WhisperClient` — caller (parser-service)
            конструирует его из ``OPENAI_API_KEY`` и cap-настроек.

    Тестовый паттерн: caller передаёт WhisperClient с инжектированным
    ``AsyncOpenAI``-stub'ом (см. ``tests/test_transcribe_audio.py``).
    """

    def __init__(self, client: WhisperClient) -> None:
        self._client = client

    async def run(
        self,
        input_: TranscribeAudioInput,
        *,
        redis: _RedisLike | None = None,
        user_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> TranscribeAudioOutput:
        """Транскрипция + телеметрия + soft-fail.

        Args:
            input_: :class:`TranscribeAudioInput` со всем нужным.
            redis: Async Redis-клиент для телеметрии. ``None`` —
                телеметрия не пишется (тесты, dry-run-cli). Production-caller
                (parser-service worker) обязан передать.
            user_id: UUID owner'а — для биллинг-агрегатов.
            request_id: Корреляционный ID, проброшенный из API-handler'а
                (если есть). ``None`` — telemetry сгенерирует UUID4.

        Returns:
            :class:`TranscribeAudioOutput` всегда. Caller проверяет
            ``output.error is None`` для определения success/failure.
        """
        try:
            result = await self._client.transcribe(
                audio_bytes=input_.audio_bytes,
                mime_type=input_.mime_type,
                language_hint=input_.language_hint,
            )
        except WhisperConfigError as exc:
            output = self._failed_output(f"config:{exc}")
        except AudioTooLongError as exc:
            output = self._failed_output(f"audio_too_long:{exc}")
        except WhisperApiError as exc:
            output = self._failed_output(f"api:{exc}")
        except WhisperError as exc:  # base-class catch-all
            output = self._failed_output(f"unexpected:{exc}")
        else:
            output = self._success_output(result)

        await self._emit_telemetry(
            redis=redis,
            output=output,
            user_id=user_id,
            request_id=request_id,
        )
        return output

    # ----------------------------------------------------------- helpers

    def _success_output(self, result: TranscriptResult) -> TranscribeAudioOutput:
        return TranscribeAudioOutput(
            transcript=result.text,
            language=result.language,
            duration_sec=result.duration_sec,
            provider=_PROVIDER_OPENAI,
            model_version=result.model,
            cost_usd=result.cost_usd,
            error=None,
        )

    def _failed_output(self, error: str) -> TranscribeAudioOutput:
        return TranscribeAudioOutput(
            transcript="",
            language=None,
            duration_sec=None,
            provider=_PROVIDER_OPENAI,
            model_version=self._client.model,
            cost_usd=Decimal("0"),
            error=error,
        )

    async def _emit_telemetry(
        self,
        *,
        redis: _RedisLike | None,
        output: TranscribeAudioOutput,
        user_id: UUID | None,
        request_id: UUID | None,
    ) -> None:
        """Записать одну запись об операции в Redis-list (best-effort).

        ``redis is None`` — телеметрия пропускается (тестовый/CLI путь).
        Сама ``log_ai_usage`` уже swallow'ит сетевые ошибки — здесь
        дополнительная защита не нужна.
        """
        if redis is None:
            return
        await log_ai_usage(
            redis=redis,
            use_case=_USE_CASE_NAME,
            model=output.model_version,
            input_tokens=0,
            output_tokens=0,
            cost_usd=output.cost_usd,
            audio_duration_sec=output.duration_sec,
            user_id=user_id,
            request_id=request_id,
            extra={"error": output.error} if output.error else None,
        )


__all__ = [
    "AudioTranscriber",
    "TranscribeAudioInput",
    "TranscribeAudioOutput",
]
