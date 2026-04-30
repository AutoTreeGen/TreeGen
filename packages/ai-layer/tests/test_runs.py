"""Тесты ``ai_layer.runs`` — AIRunStatus + build_raw_response."""

from __future__ import annotations

from ai_layer.clients.anthropic_client import AnthropicCompletion
from ai_layer.runs import AIRunStatus, build_raw_response
from ai_layer.types import HypothesisSuggestion


def test_status_string_values_stable_for_db() -> None:
    """Значения хранятся в БД как text — стабильность важна."""
    assert AIRunStatus.PENDING.value == "pending"
    assert AIRunStatus.COMPLETED.value == "completed"
    assert AIRunStatus.FAILED.value == "failed"


def test_build_raw_response_basic_shape() -> None:
    parsed = HypothesisSuggestion(rationale="x", confidence=0.5, evidence_refs=[])
    completion = AnthropicCompletion(
        parsed=parsed,
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        stop_reason="end_turn",
    )
    raw = build_raw_response(
        completion=completion,
        prompt_version="hypothesis_suggester_v1",
    )
    assert raw["model"] == "claude-sonnet-4-6"
    assert raw["prompt_version"] == "hypothesis_suggester_v1"
    assert raw["stop_reason"] == "end_turn"
    assert raw["input_tokens"] == 100
    assert raw["output_tokens"] == 50
    assert raw["parsed"]["rationale"] == "x"
    assert raw["parsed"]["confidence"] == 0.5
    assert "response_text" not in raw


def test_build_raw_response_with_text_includes_it() -> None:
    parsed = HypothesisSuggestion(rationale="y", confidence=0.1, evidence_refs=[])
    completion = AnthropicCompletion(
        parsed=parsed,
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        stop_reason=None,
    )
    raw = build_raw_response(
        completion=completion,
        prompt_version="some_prompt_v1",
        response_text='{"rationale": "y"}',
    )
    assert raw["response_text"] == '{"rationale": "y"}'
    assert raw["stop_reason"] is None
