"""Тесты ``AudioTranscriber`` use case (Phase 10.9a).

Сценарии (см. ADR-0064 §G1):

- happy-path: Whisper success → :class:`TranscribeAudioOutput.error is None`,
  populated fields;
- soft-fail на :class:`WhisperApiError` → output.error populated, transcript="";
- soft-fail на :class:`WhisperConfigError` / :class:`AudioTooLongError` →
  отдельные категории error-стрингов;
- telemetry вызывается ровно один раз с правильным ``use_case`` и
  ``audio_duration_sec``;
- cost_usd возвращается как :class:`Decimal` с rounding 6 places.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import fakeredis.aioredis
import pytest
from ai_layer.clients.whisper import (
    AudioTooLongError,
    TranscriptResult,
    WhisperApiError,
    WhisperClient,
    WhisperConfigError,
)
from ai_layer.telemetry import LOG_KEY
from ai_layer.use_cases.transcribe_audio import (
    AudioTranscriber,
    TranscribeAudioInput,
    TranscribeAudioOutput,
)


class _StubWhisperClient(WhisperClient):
    """Stub WhisperClient: подменяет ``transcribe`` без сетевых вызовов.

    Наследуется, чтобы AudioTranscriber видел корректный
    ``isinstance(client, WhisperClient)`` и читал ``client.model``.
    """

    def __init__(
        self,
        *,
        result: TranscriptResult | None = None,
        exc: Exception | None = None,
        model: str = "whisper-1",
    ) -> None:
        # Намеренно не вызываем super().__init__(): мы не хотим читать
        # os.environ или иницировать openai-клиента.
        self._api_key = "stub"
        self._max_duration_sec = 600
        self._model = model
        self._client = None
        self._env: dict[str, str] = {}
        self._dry_run = False
        self._stub_result = result
        self._stub_exc = exc
        self.calls: list[dict[str, Any]] = []

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language_hint: str | None = None,
    ) -> TranscriptResult:
        self.calls.append(
            {
                "audio_bytes": audio_bytes,
                "mime_type": mime_type,
                "language_hint": language_hint,
            },
        )
        if self._stub_exc is not None:
            raise self._stub_exc
        if self._stub_result is None:
            msg = "stub: result not set"
            raise AssertionError(msg)
        return self._stub_result


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    server = fakeredis.aioredis.FakeServer()
    return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)


# ---------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_run_happy_path_populates_output_fields(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    stub = _StubWhisperClient(
        result=TranscriptResult(
            text="Прадед родился в 1880 году в Витебске.",
            language="ru",
            duration_sec=18.0,
            model="whisper-1",
            cost_usd=Decimal("0.001800"),
        ),
    )
    transcriber = AudioTranscriber(stub)

    output = await transcriber.run(
        TranscribeAudioInput(
            audio_bytes=b"\x00fake",
            mime_type="audio/webm",
            language_hint="ru",
        ),
        redis=fake_redis,
    )

    assert isinstance(output, TranscribeAudioOutput)
    assert output.error is None
    assert output.transcript == "Прадед родился в 1880 году в Витебске."
    assert output.language == "ru"
    assert output.duration_sec == 18.0
    assert output.provider == "openai-whisper-1"
    assert output.model_version == "whisper-1"
    assert isinstance(output.cost_usd, Decimal)
    assert output.cost_usd == Decimal("0.001800")

    # WhisperClient получил input верно.
    assert len(stub.calls) == 1
    assert stub.calls[0]["mime_type"] == "audio/webm"
    assert stub.calls[0]["language_hint"] == "ru"


# ---------------------------------------------------------------- soft-fail


@pytest.mark.asyncio
async def test_run_soft_fails_on_whisper_api_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    stub = _StubWhisperClient(exc=WhisperApiError("upstream 503"))
    transcriber = AudioTranscriber(stub)

    output = await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=fake_redis,
    )

    assert output.error is not None
    assert output.error.startswith("api:")
    assert "503" in output.error
    assert output.transcript == ""
    assert output.duration_sec is None
    assert output.cost_usd == Decimal("0")
    assert output.model_version == "whisper-1"  # из client.model


@pytest.mark.asyncio
async def test_run_soft_fails_on_config_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    stub = _StubWhisperClient(exc=WhisperConfigError("OPENAI_API_KEY missing"))
    transcriber = AudioTranscriber(stub)

    output = await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=fake_redis,
    )

    assert output.error is not None
    assert output.error.startswith("config:")
    assert output.transcript == ""


@pytest.mark.asyncio
async def test_run_soft_fails_on_audio_too_long(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    stub = _StubWhisperClient(exc=AudioTooLongError("700s exceeds 600s cap"))
    transcriber = AudioTranscriber(stub)

    output = await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=fake_redis,
    )

    assert output.error is not None
    assert output.error.startswith("audio_too_long:")
    assert "600" in output.error


# ---------------------------------------------------------------- telemetry


@pytest.mark.asyncio
async def test_run_writes_one_telemetry_record_on_success(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    stub = _StubWhisperClient(
        result=TranscriptResult(
            text="hi",
            language="en",
            duration_sec=5.5,
            model="whisper-1",
            cost_usd=Decimal("0.000550"),
        ),
    )
    transcriber = AudioTranscriber(stub)

    await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=fake_redis,
    )

    items = await fake_redis.lrange(LOG_KEY, 0, -1)
    assert len(items) == 1
    record = json.loads(items[0])
    assert record["use_case"] == "transcribe_audio"
    assert record["model"] == "whisper-1"
    assert record["audio_duration_sec"] == 5.5
    assert record["cost_usd"] == pytest.approx(0.00055, abs=1e-9)
    assert record["input_tokens"] == 0
    assert record["output_tokens"] == 0


@pytest.mark.asyncio
async def test_run_writes_telemetry_on_failure_with_error_in_extra(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    stub = _StubWhisperClient(exc=WhisperApiError("503 service unavailable"))
    transcriber = AudioTranscriber(stub)

    await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=fake_redis,
    )

    items = await fake_redis.lrange(LOG_KEY, 0, -1)
    assert len(items) == 1
    record = json.loads(items[0])
    assert record["use_case"] == "transcribe_audio"
    assert record["cost_usd"] == 0.0
    assert "audio_duration_sec" not in record  # None → key omitted
    assert record["extra"]["error"].startswith("api:")


@pytest.mark.asyncio
async def test_run_skips_telemetry_when_redis_is_none() -> None:
    """Caller без Redis (CLI / test) → telemetry просто пропускается."""
    stub = _StubWhisperClient(
        result=TranscriptResult(
            text="x",
            language="ru",
            duration_sec=1.0,
            model="whisper-1",
            cost_usd=Decimal("0.000100"),
        ),
    )
    transcriber = AudioTranscriber(stub)

    output = await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=None,
    )

    assert output.error is None
    assert output.transcript == "x"


# ---------------------------------------------------------------- cost type


@pytest.mark.asyncio
async def test_output_cost_usd_is_decimal_with_six_place_quantum(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Cost проходит сквозь use-case как Decimal без лишнего округления."""
    stub = _StubWhisperClient(
        result=TranscriptResult(
            text="x",
            language="ru",
            duration_sec=7.0,  # 7/60 * 0.006 = 0.000700
            model="whisper-1",
            cost_usd=Decimal("0.000700"),
        ),
    )
    transcriber = AudioTranscriber(stub)

    output = await transcriber.run(
        TranscribeAudioInput(audio_bytes=b"\x00", mime_type="audio/webm"),
        redis=fake_redis,
    )

    assert isinstance(output.cost_usd, Decimal)
    assert output.cost_usd == Decimal("0.000700")
    # 6 знаков после запятой сохранены — не «0.0007»
    assert str(output.cost_usd).split(".")[1] == "000700"
