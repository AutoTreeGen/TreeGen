"""Тесты ``WhisperClient`` (Phase 10.9a).

Сценарии (см. ADR-0064 §A1 + §G1):

- happy-path: SDK возвращает verbose-json → :class:`TranscriptResult` с
  text/language/duration/cost;
- soft-fail с одним retry: первый attempt 5xx, второй — success → result;
- hard-fail после двух 5xx → :class:`WhisperApiError`;
- non-retryable error (401 auth) → сразу :class:`WhisperApiError` без retry;
- ``AI_DRY_RUN=true`` без api_key → mock-payload, никаких сетевых вызовов;
- api_key=None и dry-run выключен → :class:`WhisperConfigError`;
- pre-flight duration cap > 600 → :class:`AudioTooLongError`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest
from ai_layer.clients import whisper as whisper_module
from ai_layer.clients.whisper import (
    AudioTooLongError,
    TranscriptResult,
    WhisperApiError,
    WhisperClient,
    WhisperConfigError,
)


@dataclass
class _FakeTranscription:
    """Эмуляция ``openai.types.audio.TranscriptionVerbose``."""

    text: str
    language: str | None = None
    duration: float | None = None


class _FakeTranscriptionsAPI:
    """Эмулирует ``client.audio.transcriptions``.

    Принимает ``responder``-функцию, чтобы тест мог:
    - вернуть успех (responder → :class:`_FakeTranscription`);
    - бросить exception (responder поднимает любое исключение);
    - имитировать retry: список responder'ов, по одному на attempt.
    """

    def __init__(
        self,
        responders: list[Callable[..., Any]] | Callable[..., Any],
    ) -> None:
        self._responders = responders if isinstance(responders, list) else [responders]
        self.calls: list[dict[str, Any]] = []
        self._idx = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        responder = self._responders[min(self._idx, len(self._responders) - 1)]
        self._idx += 1
        return responder(**kwargs)


class _FakeAudioNamespace:
    def __init__(self, transcriptions: _FakeTranscriptionsAPI) -> None:
        self.transcriptions = transcriptions


class _FakeAsyncOpenAI:
    """Минимальный ``openai.AsyncOpenAI``-stub: только ``.audio.transcriptions.create``."""

    def __init__(self, transcriptions: _FakeTranscriptionsAPI) -> None:
        self.audio = _FakeAudioNamespace(transcriptions)


def _build_fake_openai(
    responders: list[Callable[..., Any]] | Callable[..., Any],
) -> tuple[_FakeAsyncOpenAI, _FakeTranscriptionsAPI]:
    api = _FakeTranscriptionsAPI(responders)
    return _FakeAsyncOpenAI(api), api


def _fake_request() -> httpx.Request:
    """Подделка httpx.Request — нужна для конструкторов openai-исключений."""
    return httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")


def _make_timeout_error() -> Exception:
    """Создать ``openai.APITimeoutError`` — retryable."""
    from openai import APITimeoutError

    return APITimeoutError(request=_fake_request())


def _make_auth_error() -> Exception:
    """Создать ``openai.AuthenticationError`` — fatal (non-retryable)."""
    from openai import AuthenticationError

    # AuthenticationError(message, *, response, body)
    response = httpx.Response(
        status_code=401,
        request=_fake_request(),
    )
    return AuthenticationError(
        "invalid api key",
        response=response,
        body={"error": {"message": "invalid api key"}},
    )


# ---------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_transcribe_happy_path_returns_transcript_result() -> None:
    fake_openai, api = _build_fake_openai(
        lambda **_: _FakeTranscription(
            text="Привет, расскажу про прадеда.",
            language="ru",
            duration=12.5,
        ),
    )
    client = WhisperClient(api_key="test-key", client=fake_openai)

    result = await client.transcribe(b"\x00fake-bytes", "audio/webm")

    assert isinstance(result, TranscriptResult)
    assert result.text == "Привет, расскажу про прадеда."
    assert result.language == "ru"
    assert result.duration_sec == 12.5
    assert result.model == "whisper-1"
    # 12.5 sec * 0.006/60 = 0.00125 USD → quantize до 6 знаков
    assert result.cost_usd == Decimal("0.001250")

    # SDK получил правильные параметры.
    assert len(api.calls) == 1
    call = api.calls[0]
    assert call["model"] == "whisper-1"
    assert call["response_format"] == "verbose_json"
    filename, _buf, mime = call["file"]
    assert filename == "audio.webm"
    assert mime == "audio/webm"


@pytest.mark.asyncio
async def test_transcribe_passes_language_hint_to_sdk() -> None:
    fake_openai, api = _build_fake_openai(
        lambda **_: _FakeTranscription(text="hi", language="en", duration=2.0),
    )
    client = WhisperClient(api_key="test-key", client=fake_openai)

    await client.transcribe(b"\x00", "audio/webm", language_hint="en")

    assert api.calls[0]["language"] == "en"


# ---------------------------------------------------------------- retry path


@pytest.mark.asyncio
async def test_transcribe_retries_once_on_5xx_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5xx на первой попытке → один retry → success."""
    monkeypatch.setattr(whisper_module, "_RETRY_BACKOFF_SECONDS", 0.0)

    def fail_once(**_: Any) -> _FakeTranscription:
        raise _make_timeout_error()

    def succeed(**_: Any) -> _FakeTranscription:
        return _FakeTranscription(text="ok", language="ru", duration=5.0)

    fake_openai, api = _build_fake_openai([fail_once, succeed])
    client = WhisperClient(api_key="test-key", client=fake_openai)

    result = await client.transcribe(b"\x00", "audio/webm")

    assert result.text == "ok"
    assert len(api.calls) == 2  # ровно один retry


@pytest.mark.asyncio
async def test_transcribe_raises_after_two_retryable_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два retryable-fail подряд → :class:`WhisperApiError`."""
    monkeypatch.setattr(whisper_module, "_RETRY_BACKOFF_SECONDS", 0.0)

    def always_fail(**_: Any) -> _FakeTranscription:
        raise _make_timeout_error()

    fake_openai, api = _build_fake_openai([always_fail, always_fail])
    client = WhisperClient(api_key="test-key", client=fake_openai)

    with pytest.raises(WhisperApiError):
        await client.transcribe(b"\x00", "audio/webm")
    assert len(api.calls) == 2


@pytest.mark.asyncio
async def test_transcribe_does_not_retry_on_auth_error() -> None:
    """401 auth-error → :class:`WhisperApiError` без retry'я."""

    def fail(**_: Any) -> _FakeTranscription:
        raise _make_auth_error()

    fake_openai, api = _build_fake_openai([fail])
    client = WhisperClient(api_key="test-key", client=fake_openai)

    with pytest.raises(WhisperApiError):
        await client.transcribe(b"\x00", "audio/webm")
    assert len(api.calls) == 1  # без retry


# ---------------------------------------------------------------- dry-run


@pytest.mark.asyncio
async def test_transcribe_dry_run_returns_mock_without_api_call() -> None:
    """``AI_DRY_RUN=true`` без api_key → mock-payload, SDK не дёргается."""
    client = WhisperClient(api_key=None, env={"AI_DRY_RUN": "true"})

    result = await client.transcribe(b"\x00", "audio/webm", language_hint="ru")

    assert result.text == "[dry-run mock RU]"
    assert result.language == "ru"
    assert result.cost_usd == Decimal("0.000000")
    assert result.duration_sec == 0.0
    assert result.model == "whisper-1"


@pytest.mark.asyncio
async def test_transcribe_without_api_key_and_no_dry_run_raises_config_error() -> None:
    """``api_key=None`` + ``AI_DRY_RUN`` выключен → :class:`WhisperConfigError`."""
    client = WhisperClient(api_key=None, env={"AI_DRY_RUN": "false"})

    with pytest.raises(WhisperConfigError):
        await client.transcribe(b"\x00", "audio/webm")


# ---------------------------------------------------------------- duration cap


@pytest.mark.asyncio
async def test_transcribe_rejects_audio_longer_than_max_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight оценка > ``max_duration_sec`` → :class:`AudioTooLongError`."""
    # Подменяем оценщик длительности, чтобы не зависеть от реального
    # mutagen-парсинга в тесте.
    monkeypatch.setattr(whisper_module, "_estimate_duration_sec", lambda _b: 700.0)

    def should_not_be_called(**_: Any) -> _FakeTranscription:
        msg = "API must not be called when audio exceeds cap"
        raise AssertionError(msg)

    fake_openai, api = _build_fake_openai([should_not_be_called])
    client = WhisperClient(
        api_key="test-key",
        client=fake_openai,
        max_duration_sec=600,
    )

    with pytest.raises(AudioTooLongError):
        await client.transcribe(b"\x00", "audio/webm")
    assert len(api.calls) == 0


@pytest.mark.asyncio
async def test_transcribe_proceeds_when_duration_within_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight оценка ≤ cap → API дёргается."""
    monkeypatch.setattr(whisper_module, "_estimate_duration_sec", lambda _b: 30.0)

    fake_openai, api = _build_fake_openai(
        lambda **_: _FakeTranscription(text="ok", language="ru", duration=30.0),
    )
    client = WhisperClient(
        api_key="test-key",
        client=fake_openai,
        max_duration_sec=600,
    )

    result = await client.transcribe(b"\x00", "audio/webm")

    assert result.text == "ok"
    assert len(api.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_proceeds_when_duration_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutagen не смог разобрать → проходим (server-side cap отловит)."""
    monkeypatch.setattr(whisper_module, "_estimate_duration_sec", lambda _b: None)

    fake_openai, api = _build_fake_openai(
        lambda **_: _FakeTranscription(text="x", language="ru", duration=1.0),
    )
    client = WhisperClient(api_key="test-key", client=fake_openai)

    result = await client.transcribe(b"\x00", "audio/webm")
    assert result.text == "x"
    assert len(api.calls) == 1
