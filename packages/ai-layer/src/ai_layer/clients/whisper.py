"""Async-обёртка над OpenAI Whisper API (Phase 10.9a).

Дизайн (см. ADR-0064 §A1 + §G1, ADR-0057 §F):

- **Async-only.** Downstream — arq worker в parser-service. Тесты —
  ``pytest-asyncio``.
- **Injectable клиент.** Конструктор принимает опциональный
  ``openai.AsyncOpenAI`` — тестам не нужно monkey-patch'ить SDK; production
  лениво создаёт клиента из ``api_key``.
- **AI_DRY_RUN-aware.** Если ``api_key is None`` И ``AI_DRY_RUN=true`` —
  возвращаем mock-транскрипт без сетевых вызовов (паттерн ADR-0057 §D
  для CI/dev без ключей). Если ``api_key is None`` и dry-run выключен —
  ``WhisperConfigError`` (фаст-fail на неправильной конфигурации).
- **Pre-flight duration cap.** До egress'а оцениваем длительность через
  ``mutagen.File`` (поддерживает WebM/Opus/MP3/OGG/M4A). Если оценка
  > ``max_duration_sec`` — ``AudioTooLongError``, в API не идём. Это и
  cost-control (ADR-0064 §«Cost runaway»), и privacy-control (не отдаём
  длинное аудио в OpenAI logs если juser промахнулся).
- **Soft-fail с одним retry.** На retryable-ошибки (5xx / timeout /
  rate-limit / connection) — один retry с экспоненциальным backoff
  (1 сек). После двух неудач — ``WhisperApiError``. Non-retryable
  (auth, bad-request, audio-too-long-server-side) — сразу
  ``WhisperApiError``. Паттерн соответствует ADR-0057 §F (один retry,
  потом hard); отличие — здесь мы сами держим retry-loop вместо SDK,
  потому что нужна детерминированная семантика «exactly one retry» для
  тестов и cost-tracking'а.
- **Cost from API response.** Реальная длительность берётся из
  ``response.duration`` (Whisper ``response_format='verbose_json'``)
  и пропускается через ``estimate_whisper_cost_usd``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict

from ai_layer.pricing import estimate_whisper_cost_usd

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_DEFAULT_MODEL: Final[str] = "whisper-1"
_DEFAULT_MAX_DURATION_SEC: Final[int] = 600
_RETRY_BACKOFF_SECONDS: Final[float] = 1.0
_DRY_RUN_TRANSCRIPT: Final[str] = "[dry-run mock RU]"

# MIME → расширение для multipart upload. OpenAI SDK инференсит формат
# по filename в tuple ``(filename, file, mimetype)`` — некорректный ext
# приведёт к 400 invalid_audio. Список покрывает форматы, реально
# поступающие от MediaRecorder (WebM/Opus, OGG/Opus) и стандартных
# конвертеров (MP3/M4A/WAV/FLAC).
_MIME_TO_EXT: Final[dict[str, str]] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
}

_logger = logging.getLogger(__name__)


def _ext_for_mime(mime: str) -> str:
    """MIME-тип → расширение для OpenAI multipart upload.

    Сначала пробуем точный match (с codec-suffix отрезанным), потом —
    base type. Fallback — ``webm`` (самый частый формат от MediaRecorder).
    """
    base = mime.split(";", maxsplit=1)[0].strip().lower()
    return _MIME_TO_EXT.get(base, "webm")


def _parse_dry_run(env: Mapping[str, str]) -> bool:
    """Парсит ``AI_DRY_RUN`` env-флаг (true/1/yes/on — True; иначе False)."""
    return env.get("AI_DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}


def _estimate_duration_sec(audio_bytes: bytes) -> float | None:
    """Оценить длительность аудио в секундах через mutagen.

    Вернёт ``None`` если mutagen не смог разобрать формат — caller тогда
    либо доверится server-side cap'у Whisper, либо отклонит. Никогда не
    бросает: pre-flight check не должен падать на корявом аудио (это
    задача API-вызова и его error-handling'а).
    """
    try:
        # Лениво: mutagen — pure-python, дешёвый импорт, но семантически
        # это «сетевая» утилита pre-flight'а.
        import mutagen  # noqa: PLC0415
    except ImportError:  # pragma: no cover — mutagen в hard-deps pyproject.toml
        return None

    # ``mutagen.File`` реэкспортируется в __init__, но без ``__all__`` —
    # mypy strict отбивает прямой ``from mutagen import File``. Через
    # ``getattr`` обходим без ``# type: ignore`` (CLAUDE.md §6).
    mutagen_file_factory = getattr(mutagen, "File", None)
    if mutagen_file_factory is None:  # pragma: no cover — невозможно при installed mutagen
        return None

    try:
        parsed = mutagen_file_factory(io.BytesIO(audio_bytes))
    except Exception:
        # mutagen может выбрасывать что угодно (HeaderNotFoundError,
        # IndexError, ValueError) на корявом аудио. Pre-flight check не
        # должен падать — caller (caller of caller, реально — Whisper API)
        # отловит проблему через server-side validation.
        return None
    if parsed is None or parsed.info is None:
        return None
    length = getattr(parsed.info, "length", None)
    if length is None:
        return None
    try:
        return float(length)
    except (TypeError, ValueError):
        return None


def _is_retryable_error(exc: BaseException) -> bool:
    """Классификация исключений OpenAI SDK на retryable / fatal.

    Retryable (бросаем second attempt):
    - ``APITimeoutError``, ``APIConnectionError`` — сетевая нестабильность.
    - ``RateLimitError`` (429) — провайдер просит подождать.
    - 5xx (``InternalServerError``) — провайдерский баг, временный.

    Fatal (сразу ``WhisperApiError``):
    - ``AuthenticationError`` (401) — неправильный ключ, retry не поможет.
    - ``BadRequestError`` (400) — испорченный запрос (audio formatы).
    - ``NotFoundError`` (404) — неправильная модель.

    Импорт классов SDK ленивый — ``ai_layer`` грузится без openai в
    окружениях с ``AI_LAYER_ENABLED=false``.
    """
    try:
        from openai import (  # noqa: PLC0415 — лениво, см. модульный docstring
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover
        return False
    return isinstance(
        exc,
        APITimeoutError | APIConnectionError | RateLimitError | InternalServerError,
    )


class WhisperError(Exception):
    """Базовый класс для всех ошибок WhisperClient — для catch-all caller'ов."""


class WhisperConfigError(WhisperError):
    """API-ключ не задан и ``AI_DRY_RUN`` не включён."""


class AudioTooLongError(WhisperError):
    """Audio превышает ``max_duration_sec`` — отклоняем до egress'а."""


class WhisperApiError(WhisperError):
    """API недоступен после retry или вернул non-recoverable error."""


class TranscriptResult(BaseModel):
    """Pydantic-результат расшифровки.

    Attributes:
        text: Расшифрованный текст. Может быть пустой на тихом аудио
            (Whisper не галлюцинирует «текст из ниоткуда»).
        language: ISO-639 код языка (``ru``, ``en``, ``he``, ...).
            ``None`` — Whisper не определил.
        duration_sec: Реальная длительность из ответа API.
            ``None`` — поле отсутствует в response (старые SDK).
        model: Имя модели (например, ``whisper-1``).
        cost_usd: Стоимость, рассчитанная через
            :func:`ai_layer.pricing.estimate_whisper_cost_usd`. Decimal,
            не float — ADR-0064 §«Cost».
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    language: str | None
    duration_sec: float | None
    model: str
    cost_usd: Decimal


class WhisperClient:
    """Async-обёртка для STT-вызовов OpenAI Whisper API.

    Args:
        api_key: ``OPENAI_API_KEY``. ``None`` + ``AI_DRY_RUN=true`` →
            mock-режим без сетевых вызовов.
        max_duration_sec: Cap на длительность одного файла (default 600 —
            ADR-0064 §«Cost»; Whisper-цена $0.006/мин, 10 минут = $0.06).
        model: Whisper-модель; default ``whisper-1`` (единственная в
            pricing-таблице на 2026-04-30).
        client: Опциональный ``openai.AsyncOpenAI`` для тестов с stub'ом.
        env: Опциональный mapping для чтения ``AI_DRY_RUN`` (тесты, чтобы
            не зависеть от живого ``os.environ``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_duration_sec: int = _DEFAULT_MAX_DURATION_SEC,
        model: str = _DEFAULT_MODEL,
        client: AsyncOpenAI | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._max_duration_sec = max_duration_sec
        self._model = model
        self._client = client
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._dry_run = _parse_dry_run(self._env)

    @property
    def model(self) -> str:
        """Имя Whisper-модели — нужно caller'у для телеметрии."""
        return self._model

    @property
    def max_duration_sec(self) -> int:
        """Cap длительности — для UI/валидации на стороне caller'а."""
        return self._max_duration_sec

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language_hint: str | None = None,
    ) -> TranscriptResult:
        """Транскрибировать аудио-байты через Whisper API.

        Args:
            audio_bytes: Байты файла (WebM/Opus, MP3, M4A, WAV, FLAC, OGG).
            mime_type: MIME-тип файла (``audio/webm``, ...). Используется
                для inference расширения в multipart-payload'е.
            language_hint: ISO-639 код языка, если caller знает заранее
                (``ru``, ``en``). ``None`` — Whisper определит сам.
                Передача hint'а ускоряет inference и улучшает accuracy.

        Returns:
            :class:`TranscriptResult` с текстом, языком, длительностью и
            стоимостью.

        Raises:
            WhisperConfigError: ``api_key=None`` и ``AI_DRY_RUN`` выключен.
            AudioTooLongError: Pre-flight оценка длительности превышает
                ``max_duration_sec``.
            WhisperApiError: API недоступен после одного retry'я, либо
                non-recoverable ошибка (auth, bad-request).
        """
        # Dry-run путь — никаких API-вызовов и pre-flight'а (mock не
        # должен зависеть от наличия ключа или mutagen-парсинга).
        if self._api_key is None:
            if not self._dry_run:
                msg = "OPENAI_API_KEY is not set and AI_DRY_RUN!=true; cannot call Whisper API"
                raise WhisperConfigError(msg)
            return self._dry_run_result(language_hint)

        # Pre-flight duration cap.
        estimated = _estimate_duration_sec(audio_bytes)
        if estimated is not None and estimated > self._max_duration_sec:
            msg = (
                f"Audio duration {estimated:.1f}s exceeds cap "
                f"{self._max_duration_sec}s; refusing to call Whisper API"
            )
            raise AudioTooLongError(msg)

        client = self._get_client()
        ext = _ext_for_mime(mime_type)
        filename = f"audio.{ext}"

        # Один retry с экспоненциальным backoff — детерминированный
        # «exactly two attempts». ADR-0057 §F + override per ADR-0064 §G1
        # (мы сами держим retry-loop, не делегируем SDK).
        last_exc: BaseException | None = None
        for attempt in (0, 1):
            try:
                buf = io.BytesIO(audio_bytes)
                # SDK ожидает `language: str | Omit` — None запрещён.
                # Собираем kwargs условно, чтобы пропустить language при
                # отсутствии hint'а (Whisper определит язык сам).
                extra_kwargs: dict[str, Any] = {}
                if language_hint is not None:
                    extra_kwargs["language"] = language_hint
                response = await client.audio.transcriptions.create(
                    model=self._model,
                    file=(filename, buf, mime_type),
                    response_format="verbose_json",
                    **extra_kwargs,
                )
            except Exception as exc:
                if not _is_retryable_error(exc):
                    msg = f"Whisper API non-retryable error: {exc}"
                    raise WhisperApiError(msg) from exc
                last_exc = exc
                if attempt == 0:
                    _logger.warning(
                        "whisper transcribe got retryable error; retrying once",
                        exc_info=exc,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                # attempt == 1 → второй retryable-fail → hard
                msg = f"Whisper API failed after retry: {exc}"
                raise WhisperApiError(msg) from exc
            else:
                try:
                    return self._build_result(response)
                except (ValueError, TypeError) as exc:
                    # Ответ распарсился SDK'ом, но поля невалидны
                    # (нет text/duration/language). Один retry — потом hard.
                    last_exc = exc
                    if attempt == 0:
                        _logger.warning(
                            "whisper transcribe got malformed response; retrying once",
                            exc_info=exc,
                        )
                        await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                        continue
                    msg = f"Whisper API returned malformed response twice: {exc}"
                    raise WhisperApiError(msg) from exc

        # Недостижимо — цикл всегда выходит через return или raise.
        msg = f"unreachable: whisper retry-loop completed without return ({last_exc})"
        raise WhisperApiError(msg)

    # ------------------------------------------------------------- helpers

    def _dry_run_result(self, language_hint: str | None) -> TranscriptResult:
        """Mock-результат для ``AI_DRY_RUN=true`` без api_key."""
        return TranscriptResult(
            text=_DRY_RUN_TRANSCRIPT,
            language=language_hint or "ru",
            duration_sec=0.0,
            model=self._model,
            cost_usd=Decimal("0.000000"),
        )

    def _get_client(self) -> AsyncOpenAI:
        """Лениво создать ``AsyncOpenAI`` или вернуть инжектированный.

        Импорт SDK ленивый — ``ai_layer`` должен грузиться без openai
        в окружении с ``AI_LAYER_ENABLED=false`` / без ключа.
        """
        if self._client is not None:
            return self._client
        if self._api_key is None:  # pragma: no cover — отсечено в .transcribe()
            msg = "OPENAI_API_KEY is not set; cannot instantiate OpenAI client"
            raise WhisperConfigError(msg)
        # PLC0415 — намеренное отложение импорта; см. модульный docstring.
        from openai import AsyncOpenAI  # noqa: PLC0415

        # ``max_retries=0``: cовсем отключаем встроенные retry SDK, чтобы
        # семантика «exactly one our retry» оставалась наблюдаемой и
        # тестируемой. Без этого SDK сам сделает 2 retry на 5xx, и у нас
        # будет 4 attempt'а вместо 2.
        self._client = AsyncOpenAI(api_key=self._api_key, max_retries=0)
        return self._client

    def _build_result(self, response: Any) -> TranscriptResult:
        """Достать поля из ответа Whisper SDK и собрать TranscriptResult.

        Поддерживает оба варианта возврата SDK (Pydantic-модель ≥1.0 и
        dict от старых stub'ов): берём через :func:`getattr` с fallback'ом
        на ``__getitem__``.
        """
        text = _response_field(response, "text")
        if text is None:
            msg = "Whisper response missing 'text' field"
            raise ValueError(msg)
        language = _response_field(response, "language")
        duration = _response_field(response, "duration")
        duration_f: float | None
        if duration is None:
            duration_f = None
        else:
            try:
                duration_f = float(duration)
            except (TypeError, ValueError) as exc:
                msg = f"Whisper response has non-numeric duration: {duration!r}"
                raise ValueError(msg) from exc
        cost = (
            estimate_whisper_cost_usd(duration_f, model=self._model)
            if duration_f is not None
            else Decimal("0.000000")
        )
        return TranscriptResult(
            text=str(text),
            language=str(language) if language else None,
            duration_sec=duration_f,
            model=self._model,
            cost_usd=cost,
        )


def _response_field(response: Any, name: str) -> Any:
    """Достать поле из Whisper response — Pydantic-модель или dict."""
    value = getattr(response, name, None)
    if value is not None:
        return value
    if isinstance(response, dict):
        return response.get(name)
    return None


__all__ = [
    "AudioTooLongError",
    "TranscriptResult",
    "WhisperApiError",
    "WhisperClient",
    "WhisperConfigError",
    "WhisperError",
]
