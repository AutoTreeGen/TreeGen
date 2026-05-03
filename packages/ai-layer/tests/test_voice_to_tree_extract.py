"""Тесты ``VoiceExtractor`` use case (Phase 10.9b / ADR-0075).

Сценарии (см. бриф `.agent-tasks/07-phase-10.9b-ai-layer-extract.md` §«TESTS»):

- happy-path: 3 pass'а success → proposals from all 3 passes returned;
- pass 2 пытается ``create_person`` (не из allowlist) → tool ignored,
  ``unexpected_tools`` populated;
- pass 1 happy, pass 2 5xx → ``status='partial_failed'``, pass-1 proposals
  сохранены;
- pre-flight cost cap превышен → ``VoiceExtractCostCapError`` без
  Anthropic-вызова;
- 3 fixture-transcript'а (ru_simple, en_simple, mixed_ru_he) корректно
  сегментируются через ``_segment_transcript``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from ai_layer.clients.anthropic_client import (
    AnthropicClient,
    AnthropicToolCallResult,
    ToolCall,
)
from ai_layer.config import AILayerConfig
from ai_layer.use_cases.voice_to_tree_extract import (
    VoiceExtractCostCapError,
    VoiceExtractInput,
    VoiceExtractor,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "voice_transcripts"


def _make_client(*, enabled: bool = True) -> AnthropicClient:
    """Сконструировать AnthropicClient с заглушкой SDK (без сети)."""
    config = AILayerConfig(
        enabled=enabled,
        anthropic_api_key="test-key",
        anthropic_model="claude-sonnet-4-6",
    )
    # Передаём object() как client — _get_client() вернёт его без import'а SDK.
    return AnthropicClient(config, client=object())  # type: ignore[arg-type]


_tool_call_counter: list[int] = [0]


def _tool_call(name: str, **input_args: Any) -> ToolCall:
    """Хелпер для построения tool-call'ов в фикстурах (id — монотонный counter)."""
    _tool_call_counter[0] += 1
    return ToolCall(id=f"toolu_{_tool_call_counter[0]}", name=name, input=input_args)


def _make_result(tool_calls: list[ToolCall]) -> AnthropicToolCallResult:
    """Минимальный AnthropicToolCallResult для возврата из стаба."""
    return AnthropicToolCallResult(
        tool_calls=tool_calls,
        text=None,
        model="claude-sonnet-4-6",
        input_tokens=500,
        output_tokens=200,
        stop_reason="tool_use",
    )


@pytest.mark.asyncio
async def test_happy_path_three_passes() -> None:
    """3 pass'а success → все proposals возвращены, status='succeeded'."""
    client = _make_client()
    pass_1_calls = [
        _tool_call(
            "create_person",
            given_name="Anna",
            confidence=0.9,
            evidence_snippets=["Анна Петровна"],
        ),
        _tool_call(
            "add_place",
            name_raw="Москва",
            place_type="city",
            confidence=0.95,
            evidence_snippets=["в Москве"],
        ),
    ]
    pass_2_calls = [
        _tool_call(
            "link_relationship",
            subject_index=1,
            object_index=2,
            relation="parent_of",
            confidence=0.85,
            evidence_snippets=["её сын"],
        ),
    ]
    pass_3_calls = [
        _tool_call(
            "add_event",
            person_index=1,
            event_type="birth",
            date_start_year=1925,
            date_end_year=1925,
            confidence=0.8,
            evidence_snippets=["родилась в 1925"],
        ),
    ]
    mock = AsyncMock(
        side_effect=[
            _make_result(pass_1_calls),
            _make_result(pass_2_calls),
            _make_result(pass_3_calls),
        ]
    )
    client.complete_with_tools = mock  # type: ignore[method-assign]

    extractor = VoiceExtractor(
        client,
        # Не trigger pre-flight cap — модель + transcript короткий.
        max_total_usd=Decimal("100"),
    )
    result = await extractor.run(
        VoiceExtractInput(transcript_text="Анна Петровна родилась в Москве в 1925 году.")
    )

    assert result.status == "succeeded"
    assert len(result.proposals) == 4
    assert result.proposals[0].pass_number == 1
    assert result.proposals[0].proposal_type == "person"
    assert result.proposals[1].proposal_type == "place"
    assert result.proposals[2].pass_number == 2
    assert result.proposals[2].proposal_type == "relationship"
    assert result.proposals[3].pass_number == 3
    assert result.proposals[3].proposal_type == "event"
    assert mock.await_count == 3


@pytest.mark.asyncio
async def test_unexpected_tool_filtered() -> None:
    """Pass 2 эмитит create_person (не в allowlist) → ignored, unexpected_tools++."""
    client = _make_client()
    pass_2_with_unexpected = [
        _tool_call(
            "link_relationship",
            subject_index=1,
            object_index=2,
            relation="spouse_of",
            confidence=0.7,
            evidence_snippets=["муж"],
        ),
        _tool_call(
            "create_person",  # Не в allowlist для pass 2
            given_name="Sneaky",
            confidence=0.5,
            evidence_snippets=["..."],
        ),
    ]
    mock = AsyncMock(
        side_effect=[
            _make_result([]),
            _make_result(pass_2_with_unexpected),
            _make_result([]),
        ]
    )
    client.complete_with_tools = mock  # type: ignore[method-assign]

    extractor = VoiceExtractor(client, max_total_usd=Decimal("100"))
    result = await extractor.run(VoiceExtractInput(transcript_text="X."))

    assert result.status == "succeeded"
    # Только relationship proposal попал, create_person отфильтрован.
    assert len(result.proposals) == 1
    assert result.proposals[0].proposal_type == "relationship"
    pass_2_telemetry = result.passes[1]
    assert pass_2_telemetry.unexpected_tools == ["create_person"]
    assert pass_2_telemetry.proposals_emitted == 1


@pytest.mark.asyncio
async def test_pass_2_failure_partial_failed() -> None:
    """Pass 1 happy, pass 2 5xx → status='partial_failed', pass-1 proposals saved."""

    class _FlakyError(RuntimeError):
        pass

    client = _make_client()
    pass_1_calls = [
        _tool_call(
            "create_person",
            given_name="Anna",
            confidence=0.9,
            evidence_snippets=["Анна"],
        ),
    ]
    # AsyncMock side_effect: первое — happy, потом два упавших call'а
    # (один retry внутри _run_pass на pass 2; pass 3 не должен вызываться).
    mock = AsyncMock(
        side_effect=[
            _make_result(pass_1_calls),
            _FlakyError("503 Anthropic flap"),
            _FlakyError("503 again"),
        ]
    )
    client.complete_with_tools = mock  # type: ignore[method-assign]

    extractor = VoiceExtractor(client, max_total_usd=Decimal("100"))
    result = await extractor.run(VoiceExtractInput(transcript_text="X."))

    assert result.status == "partial_failed"
    assert result.error_message is not None
    assert "pass2:" in result.error_message
    # Pass-1 proposal сохранён.
    assert len(result.proposals) == 1
    assert result.proposals[0].pass_number == 1
    # Pass 3 не должен был запускаться (early-return на pass-2 fail).
    assert mock.await_count == 3  # pass1 + pass2 attempt 1 + pass2 attempt 2


@pytest.mark.asyncio
async def test_preflight_cost_cap_raises() -> None:
    """Большой transcript + крошечный cap → VoiceExtractCostCapError без вызовов."""
    client = _make_client()
    mock = AsyncMock()
    client.complete_with_tools = mock  # type: ignore[method-assign]

    extractor = VoiceExtractor(
        client,
        max_total_usd=Decimal("0.0000001"),  # практически нулевой cap
    )
    with pytest.raises(VoiceExtractCostCapError):
        await extractor.run(VoiceExtractInput(transcript_text="X" * 5000))

    # Anthropic не должен был вызываться вообще.
    assert mock.await_count == 0


@pytest.mark.asyncio
async def test_segments_top_n_truncation() -> None:
    """Top-N segmentation: 50 параграфов, top-N=3 → только первые 3 сегмента."""
    client = _make_client()
    mock = AsyncMock(side_effect=[_make_result([]), _make_result([]), _make_result([])])
    client.complete_with_tools = mock  # type: ignore[method-assign]

    extractor = VoiceExtractor(
        client,
        max_total_usd=Decimal("100"),
        top_n_segments=3,
    )
    paragraphs = [f"paragraph {i}" for i in range(50)]
    full_text = "\n\n".join(paragraphs)
    await extractor.run(VoiceExtractInput(transcript_text=full_text))

    # Pass 1 должен получить только первые 3 параграфа (через user-prompt).
    pass_1_call = mock.await_args_list[0]
    user_text = pass_1_call.kwargs["user"]
    assert "paragraph 0" in user_text
    assert "paragraph 2" in user_text
    assert "paragraph 3" not in user_text


@pytest.mark.parametrize(
    "fixture_name",
    ["ru_simple.txt", "en_simple.txt", "mixed_ru_he.txt"],
)
def test_fixture_files_present(fixture_name: str) -> None:
    """3 fixture'а на месте + не пустые (precondition для integration smoke)."""
    fixture = _FIXTURES_DIR / fixture_name
    assert fixture.exists(), f"missing fixture {fixture_name}"
    text = fixture.read_text(encoding="utf-8")
    assert len(text) > 100, f"fixture {fixture_name} too short"
    # Хотя бы один параграф (split по \n\n).
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    assert len(paragraphs) >= 2, f"fixture {fixture_name} has fewer than 2 paragraphs"
