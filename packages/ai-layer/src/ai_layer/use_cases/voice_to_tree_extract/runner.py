"""3-pass voice extraction orchestrator (Phase 10.9b / ADR-0075).

Один transcript → 3 последовательных Anthropic-вызова, каждый со своим
tool-set'ом. Между pass'ами proposals из предыдущего шага передаются как
структурированный JSON в user-message (НЕ как tool_results — так модель
не «доводит» pass-1 догадки, а делает pass-2 заново на чистый transcript).

Pre-flight cost cap → ``VoiceExtractCostCapError`` ДО Anthropic-вызова.
Pass-fail → ``status="partial_failed"`` + сохраняем proposals из
предыдущих pass'ов (никогда не raise per-pass).

Не зависит от sqlalchemy / shared-models — caller (parser-service worker)
сам конвертирует ``VoiceExtractResult.proposals`` в ORM rows.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final, Protocol
from uuid import UUID, uuid4

from ai_layer.clients.anthropic_client import (
    AnthropicClient,
    AnthropicToolCallResult,
    ToolCall,
)
from ai_layer.pricing import estimate_cost_usd, estimate_input_tokens_from_text
from ai_layer.prompts.registry import PromptRegistry, PromptTemplate
from ai_layer.telemetry import log_ai_usage
from ai_layer.use_cases.voice_to_tree_extract.config import (
    VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS,
    VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION,
    VOICE_EXTRACT_TOP_N_SEGMENTS,
)
from ai_layer.use_cases.voice_to_tree_extract.errors import VoiceExtractCostCapError
from ai_layer.use_cases.voice_to_tree_extract.tools import (
    TOOLS_BY_PASS,
    pass_allowed_tool_names,
)

_logger = logging.getLogger(__name__)

PASS_NAMES: Final[dict[int, str]] = {
    1: "entities",
    2: "relationships",
    3: "temporal_spatial",
}

# Tool name → ProposalType (mirror ProposalType enum в shared-models;
# дублируем как строки чтобы ai-layer не зависел от shared-models).
_TOOL_TO_PROPOSAL_TYPE: Final[dict[str, str]] = {
    "create_person": "person",
    "add_place": "place",
    "link_relationship": "relationship",
    "add_event": "event",
    "flag_uncertain": "uncertain",
}

_USE_CASE_NAME: Final[str] = "voice_to_tree_extract"

# Default `max_tokens` для tool-use ответа. Ровно один tool-use round → не
# нужно много (ADR-0064 §«Out: streaming»). 2048 покрывает ~10–15 tool-calls.
_PASS_MAX_OUTPUT_TOKENS: Final[int] = 2_048


class _RedisLike(Protocol):
    """Async-Redis протокол для telemetry (mirror других use-case'ов)."""

    async def lpush(self, name: str, *values: str) -> object: ...
    async def expire(self, name: str, time: int) -> object: ...


@dataclass(frozen=True, slots=True)
class PassProposal:
    """Один proposal из одного pass'а — нормализованный tool-call.

    Caller (worker) маппит на ORM ``VoiceExtractedProposal`` row.
    """

    pass_number: int
    tool_name: str
    proposal_type: str  # mirrors ProposalType.value (см. _TOOL_TO_PROPOSAL_TYPE)
    payload: dict[str, Any]
    confidence: Decimal
    evidence_snippets: list[str]
    raw_tool_call: dict[str, Any]  # для raw_response в audit


@dataclass(frozen=True, slots=True)
class PassTelemetry:
    """Per-pass usage для аналитики (один Redis-record на pass)."""

    pass_number: int
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    proposals_emitted: int
    unexpected_tools: list[str]
    error: str | None  # категория ошибки если pass провалился


@dataclass(frozen=True, slots=True)
class VoiceExtractInput:
    """Вход VoiceExtractor.

    Attributes:
        transcript_text: ``AudioSession.transcript_text``. Caller гарантирует
            что не пустой (privacy-gate + status='ready' проверки в API/worker).
        language: ISO-639 код для system-prompt'а (не блокирующее, only-hint).
        prompt_version_suffix: ``"v1"`` обычно; override для A/B (напр. ``"v2"``).
    """

    transcript_text: str
    language: str | None = None
    prompt_version_suffix: str = "v1"


@dataclass(slots=True)
class VoiceExtractResult:
    """Финальный результат 3-pass extraction'а.

    Attributes:
        extraction_job_id: UUID-grouper (см. ``VoiceExtractedProposal.extraction_job_id``).
        proposals: Все proposals в порядке pass1→2→3.
        status: ``"succeeded"`` / ``"partial_failed"`` / ``"cost_capped"`` /
            ``"failed"`` (последнее — exceptional path; обычно partial_failed).
        total_input_tokens: Сумма по всем pass'ам.
        total_output_tokens: Сумма по всем pass'ам.
        total_cost_usd: Сумма Decimal по всем pass'ам.
        passes: Per-pass telemetry для UI/debug.
        error_message: Категория+описание ошибки (если status != succeeded).
    """

    extraction_job_id: UUID
    status: str
    proposals: list[PassProposal] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0")
    passes: list[PassTelemetry] = field(default_factory=list)
    error_message: str | None = None


class VoiceExtractor:
    """Use-case: 3-pass NLU extraction над одним transcript'ом.

    Args:
        anthropic: :class:`AnthropicClient` — caller (worker) конструирует
            из ``ANTHROPIC_API_KEY`` и переиспользует.
        registry: PromptRegistry-класс. Default — глобальный.
        max_total_usd: Cost cap на одну session (sum по всем pass'ам).
        max_input_tokens_per_pass: Token cap per pass (truncates segments).
        top_n_segments: Top-N segments из transcript'а (split по ``\\n\\n``).

    Тестовый паттерн: caller передаёт AnthropicClient с моком SDK
    (см. ``tests/test_voice_to_tree_extract.py``).
    """

    def __init__(
        self,
        anthropic: AnthropicClient,
        *,
        registry: type[PromptRegistry] = PromptRegistry,
        max_total_usd: Decimal = VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION,
        max_input_tokens_per_pass: int = VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS,
        top_n_segments: int = VOICE_EXTRACT_TOP_N_SEGMENTS,
    ) -> None:
        self._anthropic = anthropic
        self._registry = registry
        self._max_total_usd = max_total_usd
        self._max_input_tokens_per_pass = max_input_tokens_per_pass
        self._top_n_segments = top_n_segments

    async def run(
        self,
        input_: VoiceExtractInput,
        *,
        redis: _RedisLike | None = None,
        user_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> VoiceExtractResult:
        """Прогнать 3-pass extraction.

        Pre-flight cost-cap → ``VoiceExtractCostCapError`` (caller помечает
        ``cost_capped`` в provenance). Pass-fail → ``status='partial_failed'``,
        сохраняем proposals из предыдущих pass'ов.

        Args:
            input_: :class:`VoiceExtractInput`.
            redis: Async Redis-клиент для per-pass телеметрии.
            user_id: UUID owner'а для биллинг-агрегатов.
            request_id: Корреляционный ID; ``None`` → новый UUID4.

        Returns:
            :class:`VoiceExtractResult`. Caller проверяет ``status``.

        Raises:
            VoiceExtractCostCapError: Pre-flight estimate > ``max_total_usd``.
        """
        rid = request_id or uuid4()
        job_id = uuid4()
        segments = self._segment_transcript(input_.transcript_text)
        truncated_transcript = "\n\n".join(segments)

        self._enforce_preflight_cap(truncated_transcript)

        result = VoiceExtractResult(extraction_job_id=job_id, status="succeeded")
        accumulated: dict[str, list[dict[str, Any]]] = {
            "persons": [],
            "places": [],
            "relationships": [],
        }

        # Run 3 passes in sequence; abort early on partial-fail or cost-cap.
        for pass_number in (1, 2, 3):
            pass_telemetry = await self._run_pass(
                pass_number=pass_number,
                transcript=truncated_transcript,
                accumulated=accumulated,
                language=input_.language,
                prompt_version_suffix=input_.prompt_version_suffix,
                result=result,
                redis=redis,
                user_id=user_id,
                request_id=rid,
            )
            result.passes.append(pass_telemetry)
            result.total_input_tokens += pass_telemetry.input_tokens
            result.total_output_tokens += pass_telemetry.output_tokens
            result.total_cost_usd += pass_telemetry.cost_usd

            if pass_telemetry.error is not None:
                result.status = "partial_failed"
                result.error_message = f"pass{pass_number}:{pass_telemetry.error}"
                return result

            # Post-pass cost-cap: если превысили после pass'а — обрываем
            # оставшиеся, помечаем cost_capped. Pre-flight tells us best
            # case; per-pass tells us actual.
            if result.total_cost_usd > self._max_total_usd:
                result.status = "cost_capped"
                result.error_message = (
                    f"cost_cap_exceeded_after_pass_{pass_number}:"
                    f"total={result.total_cost_usd}>cap={self._max_total_usd}"
                )
                return result

        return result

    # ----------------------------------------------------------- internals

    def _segment_transcript(self, text: str) -> list[str]:
        """Split transcript по ``\\n\\n``, take top-N non-empty segments."""
        raw_segments = [seg.strip() for seg in text.split("\n\n")]
        non_empty = [seg for seg in raw_segments if seg]
        if not non_empty:
            # Edge-case: пустой transcript (caller должен отфильтровать
            # раньше). Возвращаем хотя бы исходную строку чтобы LLM видел
            # «нечего извлекать» и эмитил пустой result, а не raise.
            stripped = text.strip()
            return [stripped] if stripped else [""]
        return non_empty[: self._top_n_segments]

    def _enforce_preflight_cap(self, transcript: str) -> None:
        """Pre-flight: 3 pass'а × оценка → если > cap, raise."""
        per_pass_input = estimate_input_tokens_from_text(len(transcript))
        per_pass_cost = estimate_cost_usd(
            self._anthropic._config.anthropic_model,
            input_tokens=per_pass_input,
            output_tokens=_PASS_MAX_OUTPUT_TOKENS,
        )
        total_estimate = Decimal(str(per_pass_cost)) * Decimal(3)
        if total_estimate > self._max_total_usd:
            msg = (
                f"Pre-flight estimate {total_estimate} USD exceeds cap "
                f"{self._max_total_usd} USD (per_pass≈{per_pass_cost}, model="
                f"{self._anthropic._config.anthropic_model}). "
                "Truncate transcript or raise tier cap."
            )
            raise VoiceExtractCostCapError(msg)

    async def _run_pass(
        self,
        *,
        pass_number: int,
        transcript: str,
        accumulated: dict[str, list[dict[str, Any]]],
        language: str | None,
        prompt_version_suffix: str,
        result: VoiceExtractResult,
        redis: _RedisLike | None,
        user_id: UUID | None,
        request_id: UUID,
    ) -> PassTelemetry:
        """Запустить один pass: render prompt → Anthropic call → parse → telemetry."""
        template = self._template_for_pass(pass_number, prompt_version_suffix)
        rendered = template.render(
            transcript=transcript,
            language=language or "auto",
            persons_json=json.dumps(accumulated["persons"], ensure_ascii=False),
            places_json=json.dumps(accumulated["places"], ensure_ascii=False),
            relationships_json=json.dumps(accumulated["relationships"], ensure_ascii=False),
        )

        # One retry per pass per ADR-0064 §G1. Caller (worker) выше тоже не
        # ретраит — мы единственный owner retry-логики на этом слое.
        api_call_result: AnthropicToolCallResult | None = None
        last_error: str | None = None
        for attempt in (1, 2):
            try:
                api_call_result = await self._anthropic.complete_with_tools(
                    system=rendered.system,
                    user=rendered.user,
                    tools=TOOLS_BY_PASS[pass_number],
                    max_tokens=_PASS_MAX_OUTPUT_TOKENS,
                    tool_choice={"type": "auto"},
                )
                last_error = None
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}:{exc}"
                _logger.warning(
                    "voice_extract pass=%d attempt=%d failed: %s",
                    pass_number,
                    attempt,
                    last_error,
                )

        if api_call_result is None:
            telemetry = PassTelemetry(
                pass_number=pass_number,
                model=self._anthropic._config.anthropic_model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=Decimal("0"),
                proposals_emitted=0,
                unexpected_tools=[],
                error=last_error or "unknown_error",
            )
            await self._emit_telemetry(
                redis=redis,
                pass_number=pass_number,
                telemetry=telemetry,
                user_id=user_id,
                request_id=request_id,
            )
            return telemetry

        # Filter unexpected tools (модель попыталась использовать tool не из allowlist).
        allowed = pass_allowed_tool_names(pass_number)
        accepted: list[ToolCall] = []
        unexpected: list[str] = []
        for call in api_call_result.tool_calls:
            if call.name not in allowed:
                unexpected.append(call.name)
                continue
            accepted.append(call)

        # Materialize proposals + accumulate for next-pass context.
        new_proposals = self._proposals_from_tool_calls(
            pass_number=pass_number,
            tool_calls=accepted,
        )
        result.proposals.extend(new_proposals)
        self._update_accumulated(
            pass_number=pass_number,
            new_proposals=new_proposals,
            accumulated=accumulated,
        )

        cost_usd_decimal = Decimal(
            str(
                estimate_cost_usd(
                    api_call_result.model,
                    input_tokens=api_call_result.input_tokens,
                    output_tokens=api_call_result.output_tokens,
                )
            )
        )
        telemetry = PassTelemetry(
            pass_number=pass_number,
            model=api_call_result.model,
            input_tokens=api_call_result.input_tokens,
            output_tokens=api_call_result.output_tokens,
            cost_usd=cost_usd_decimal,
            proposals_emitted=len(new_proposals),
            unexpected_tools=unexpected,
            error=None,
        )
        await self._emit_telemetry(
            redis=redis,
            pass_number=pass_number,
            telemetry=telemetry,
            user_id=user_id,
            request_id=request_id,
        )
        return telemetry

    def _template_for_pass(self, pass_number: int, suffix: str) -> PromptTemplate:
        """Достать prompt-template для pass'а из реестра."""
        attr_name = f"VOICE_EXTRACT_PASS{pass_number}_{suffix.upper()}"
        template: PromptTemplate | None = getattr(self._registry, attr_name, None)
        if template is None:
            msg = (
                f"PromptRegistry.{attr_name} is not registered. "
                "Add the template file and update PromptRegistry."
            )
            raise AttributeError(msg)
        return template

    def _proposals_from_tool_calls(
        self,
        *,
        pass_number: int,
        tool_calls: list[ToolCall],
    ) -> list[PassProposal]:
        """Конвертировать tool-calls в нормализованные :class:`PassProposal`."""
        proposals: list[PassProposal] = []
        for call in tool_calls:
            proposal_type = _TOOL_TO_PROPOSAL_TYPE.get(call.name, "uncertain")
            confidence_raw = call.input.get("confidence", 0)
            try:
                confidence = Decimal(str(confidence_raw)).quantize(Decimal("0.001"))
            except Exception:
                # Модель вернула странный confidence; clamp к 0 + флажок в provenance.
                confidence = Decimal("0")
            evidence = list(call.input.get("evidence_snippets") or [])
            proposals.append(
                PassProposal(
                    pass_number=pass_number,
                    tool_name=call.name,
                    proposal_type=proposal_type,
                    payload=dict(call.input),
                    confidence=confidence,
                    evidence_snippets=evidence,
                    raw_tool_call={
                        "id": call.id,
                        "name": call.name,
                        "input": call.input,
                    },
                )
            )
        return proposals

    @staticmethod
    def _update_accumulated(
        *,
        pass_number: int,
        new_proposals: list[PassProposal],
        accumulated: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Подмешать persons/places/relationships в context для next-pass'а."""
        if pass_number == 1:
            for proposal in new_proposals:
                if proposal.proposal_type == "person":
                    accumulated["persons"].append(proposal.payload)
                elif proposal.proposal_type == "place":
                    accumulated["places"].append(proposal.payload)
        elif pass_number == 2:
            for proposal in new_proposals:
                if proposal.proposal_type == "relationship":
                    accumulated["relationships"].append(proposal.payload)
        # pass 3 — события не нужны на input'е других pass'ов.

    @staticmethod
    async def _emit_telemetry(
        *,
        redis: _RedisLike | None,
        pass_number: int,
        telemetry: PassTelemetry,
        user_id: UUID | None,
        request_id: UUID,
    ) -> None:
        """Записать одну запись Redis-телеметрии (best-effort).

        Telemetry пишется и при success, и при failure (cost_usd=0 на failure).
        Caller'у не нужно отдельно ловить network-errors — log_ai_usage
        swallow'ит их сам.
        """
        if redis is None:
            return
        await log_ai_usage(
            redis=redis,
            use_case=_USE_CASE_NAME,
            model=telemetry.model,
            input_tokens=telemetry.input_tokens,
            output_tokens=telemetry.output_tokens,
            cost_usd=telemetry.cost_usd,
            user_id=user_id,
            request_id=request_id,
            extra={
                "pass_number": pass_number,
                "pass_name": PASS_NAMES[pass_number],
                "proposals_emitted": telemetry.proposals_emitted,
                "unexpected_tools": telemetry.unexpected_tools,
                "error": telemetry.error,
            },
        )


__all__ = [
    "PASS_NAMES",
    "PassProposal",
    "PassTelemetry",
    "VoiceExtractInput",
    "VoiceExtractResult",
    "VoiceExtractor",
]
