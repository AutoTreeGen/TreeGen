"""HypothesisSuggester — первый use-case AI-слоя (Phase 10.0 skeleton).

Это **заглушка**: контракт зафиксирован, реальная интеграция с
inference-engine придёт в Phase 10.1+. На сейчас:

- Принимает список фактов и существующих гипотез.
- Рендерит ``hypothesis_suggester_v1`` через ``PromptRegistry``.
- Делает structured-вызов через ``AnthropicClient`` (мок в тестах).
- Валидирует, что все ``evidence_refs`` в ответе LLM есть среди input ID
  (защита от галлюцинаций — см. ADR-0043 §«Defense against fabricated
  citations»).

Не делает: персистенцию в БД, регистрацию в registry inference-engine,
cost-tracking, audit-log. Всё это — Phase 10.1+.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_layer.clients.anthropic_client import AnthropicClient, AnthropicCompletion
from ai_layer.prompts.registry import PromptRegistry
from ai_layer.types import HypothesisSuggestion


@dataclass(frozen=True)
class PersonFact:
    """Атомарный факт о персоне для входа в LLM.

    Attributes:
        id: Stable идентификатор (например, ``"person:42:birth_year"``);
            возвращается LLM в ``evidence_refs`` для трассировки.
        text: Естественно-языковое описание факта (на английском, в
            нейтральной форме). Caller отвечает за форматирование.
    """

    id: str
    text: str


class FabricatedEvidenceError(ValueError):
    """LLM сослался на ID, которого нет во входных фактах."""


class HypothesisSuggester:
    """Use-case: «по списку фактов предложи новую гипотезу».

    Args:
        anthropic: Клиент Claude API (в тестах — со stub'ом
            ``anthropic.AsyncAnthropic``).
        registry: Registry промптов (default — глобальный
            ``PromptRegistry``; параметризуется ради тестов).
    """

    def __init__(
        self,
        anthropic: AnthropicClient,
        registry: type[PromptRegistry] = PromptRegistry,
    ) -> None:
        self._anthropic = anthropic
        self._registry = registry

    async def suggest(
        self,
        facts: list[PersonFact],
        existing_hypotheses: list[str] | None = None,
    ) -> AnthropicCompletion[HypothesisSuggestion]:
        """Запросить у LLM одну новую гипотезу.

        Raises:
            FabricatedEvidenceError: Если LLM сослался на ID, которого нет
                во входных ``facts``. Это защита от галлюцинаций; caller
                может ловить её и логировать как «LLM нарушил контракт».
            pydantic.ValidationError: Если ответ LLM не соответствует
                ``HypothesisSuggestion``-схеме.
        """
        template = self._registry.HYPOTHESIS_SUGGESTER_V1
        rendered = template.render(
            facts=[{"id": f.id, "text": f.text} for f in facts],
            existing_hypotheses=existing_hypotheses or [],
        )

        completion: AnthropicCompletion[
            HypothesisSuggestion
        ] = await self._anthropic.complete_structured(
            system=rendered.system,
            user=rendered.user,
            response_model=HypothesisSuggestion,
        )
        _validate_evidence_refs(completion.parsed, facts)
        return completion


def _validate_evidence_refs(
    suggestion: HypothesisSuggestion,
    facts: list[PersonFact],
) -> None:
    """Убедиться, что все ``evidence_refs`` есть во входных фактах."""
    valid_ids = {f.id for f in facts}
    fabricated = [ref for ref in suggestion.evidence_refs if ref not in valid_ids]
    if fabricated:
        msg = (
            "LLM cited evidence IDs not present in input facts: "
            f"{fabricated!r}. Suggestion rejected (see ADR-0043 §"
            "'Defense against fabricated citations')."
        )
        raise FabricatedEvidenceError(msg)
