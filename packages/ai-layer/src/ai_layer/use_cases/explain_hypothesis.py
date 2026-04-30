"""HypothesisExplainer — first production AI use case (Phase 10.1).

Принимает evidence-graph гипотезы ``same_person`` и возвращает
естественно-языковое объяснение для UI Phase 4.9 review queue.

Дизайнерские решения (см. ADR-0057):

- **Read-only.** Use-case не пишет в БД — только читает evidence,
  отдаёт текст. Risk profile минимальный.
- **Bilingual.** Локаль выбирается caller'ом (``"en"`` / ``"ru"``);
  prompt-template переключает язык ответа.
- **Decoupled от inference-engine.** Принимаем плоские Pydantic-модели
  (``PersonSubject``, ``EvidenceItem``); caller-уровень мапит
  ``inference_engine.types.Evidence`` → ``EvidenceItem``. Это удерживает
  ai-layer без import-зависимости от inference-engine.
- **Dry-run.** ``AI_DRY_RUN=true`` → возвращается зашитый mock без
  вызова Anthropic. Для локальной разработки без API-ключа и для
  smoke-тестов в окружениях без secrets.
- **Truncation.** Если evidence-список огромный — урезаем по принципу
  «первые N items по убыванию confidence», чтобы prompt не превысил
  100k символов (см. ``MAX_PROMPT_CHARS``).
- **Soft-fail на malformed JSON.** Если LLM вернул JSON, не подходящий
  под схему, делаем один retry с тем же промптом; если и второй раз —
  возвращаем ``HypothesisExplanation`` с error-summary, а не падаем
  exception'ом (UI должен показать гипотезу даже без LLM-объяснения).
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.pricing import estimate_cost_usd
from ai_layer.prompts.registry import PromptRegistry
from ai_layer.types import (
    HypothesisExplanation,
    HypothesisExplanationPayload,
    HypothesisInput,
    LocaleLabel,
)

if TYPE_CHECKING:
    from ai_layer.types import EvidenceItem

_logger = logging.getLogger(__name__)

#: Жёсткий лимит длины собранного user-промпта (system + user combined).
#: Sonnet 4.6 поддерживает 200k токенов context window, но чем длиннее
#: prompt, тем дороже и медленнее. 100k символов ≈ 25k токенов — хороший
#: компромисс для нашего use-case'а (типичная гипотеза — < 30 evidence
#: items, ≈ 5–10 KB). См. ADR-0057 §«Cost target».
MAX_PROMPT_CHARS = 100_000

#: Сколько evidence-items оставлять при truncation (отсортировав по
#: confidence DESC). 50 — больше, чем нам реально нужно: даже сложные
#: гипотезы редко превышают 30 items.
MAX_EVIDENCE_ITEMS = 50

DRY_RUN_ENV_VAR = "AI_DRY_RUN"


def _is_dry_run(env: dict[str, str] | None = None) -> bool:
    """``AI_DRY_RUN=true|1|yes|on`` — без зависимости от ``config._parse_bool``."""
    source = env if env is not None else os.environ
    raw = source.get(DRY_RUN_ENV_VAR, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _dry_run_payload(locale: LocaleLabel) -> HypothesisExplanationPayload:
    """Зашитый mock-ответ для разработки без Anthropic-ключа.

    Контракт: соответствует ``HypothesisExplanationPayload``-схеме,
    локаль учитывается. Не должен попадать в production: caller проверяет
    ``HypothesisExplanation.dry_run`` и фильтрует такие записи из
    биллинга / аналитики.
    """
    if locale == "ru":
        return HypothesisExplanationPayload(
            summary=(
                "Сильное совпадение по same_person: год и место рождения "
                "совпадают, имена различаются только транслитерацией."
            ),
            key_evidence=[
                "Год рождения совпадает точно",
                "Место рождения совпадает точно",
                "Имена согласованы стандартной транслитерацией",
            ],
            caveats=["Mock-ответ из dry-run режима; реальный LLM не вызывался"],
            confidence_label="high",
        )
    return HypothesisExplanationPayload(
        summary=(
            "Strong same-person match: birth year and place align exactly, "
            "and the names differ only by standard transliteration."
        ),
        key_evidence=[
            "Birth year matches exactly",
            "Birthplace matches exactly",
            "Names align under standard transliteration",
        ],
        caveats=["Mock response from dry-run mode; no real LLM call was made"],
        confidence_label="high",
    )


def _truncate_evidence(evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    """Оставить только top-N evidence-items по убыванию confidence.

    Стабильный сорт: items с одинаковой confidence сохраняют исходный
    порядок. Это нужно, чтобы вывод LLM был детерминированным для
    одинакового входа (важно для тестов и кеширования).
    """
    if len(evidence) <= MAX_EVIDENCE_ITEMS:
        return evidence
    indexed = list(enumerate(evidence))
    indexed.sort(key=lambda pair: (-pair[1].confidence, pair[0]))
    return [item for _, item in indexed[:MAX_EVIDENCE_ITEMS]]


class HypothesisExplainer:
    """Use-case ``same_person``-объяснение для review-queue UI.

    Args:
        anthropic: Клиент Claude API (в тестах — со stub'ом
            ``AnthropicClient(client=FakeAnthropic(...))``).
        registry: PromptRegistry (параметризуется ради тестов).
        env: Опциональный override для ``os.environ`` —
            нужно тестам, проверяющим dry-run.
    """

    def __init__(
        self,
        anthropic: AnthropicClient,
        *,
        registry: type[PromptRegistry] = PromptRegistry,
        env: dict[str, str] | None = None,
    ) -> None:
        self._anthropic = anthropic
        self._registry = registry
        self._env = env

    async def explain(
        self,
        hypothesis: HypothesisInput,
        locale: LocaleLabel = "en",
    ) -> HypothesisExplanation:
        """Запросить у LLM (или вернуть mock в dry-run) объяснение гипотезы.

        Никогда не бросает на malformed JSON — делает один retry, потом
        возвращает fail-soft объяснение. Это сознательный контракт: UI
        Phase 4.9 показывает гипотезу даже если LLM глючит.
        """
        truncated_evidence = _truncate_evidence(list(hypothesis.evidence))
        truncated = hypothesis.model_copy(update={"evidence": truncated_evidence})

        if _is_dry_run(self._env):
            payload = _dry_run_payload(locale)
            return HypothesisExplanation(
                summary=payload.summary,
                key_evidence=payload.key_evidence,
                caveats=payload.caveats,
                confidence_label=payload.confidence_label,
                locale=locale,
                tokens_used=0,
                cost_usd=0.0,
                model="dry-run",
                dry_run=True,
            )

        rendered = self._registry.HYPOTHESIS_EXPLANATION_V1.render(
            subjects=[{"id": s.id, "summary": s.summary} for s in truncated.subjects],
            evidence=[item.model_dump() for item in truncated.evidence],
            composite_score=(
                f"{truncated.composite_score:.2f}"
                if truncated.composite_score is not None
                else None
            ),
            locale=locale,
        )

        prompt_size = len(rendered.system) + len(rendered.user)
        if prompt_size > MAX_PROMPT_CHARS:
            # Это safety-net: даже после truncate_evidence prompt не должен
            # быть таким большим. Если попали сюда — явно срабатывает
            # extra-большой ``summary`` в subjects; обрезаем user prompt.
            _logger.warning(
                "explain_hypothesis prompt exceeds soft limit",
                extra={"prompt_chars": prompt_size},
            )

        return await self._call_with_soft_retry(rendered.system, rendered.user, locale)

    async def _call_with_soft_retry(
        self,
        system: str,
        user: str,
        locale: LocaleLabel,
    ) -> HypothesisExplanation:
        """Один retry на ValidationError + fail-soft в третий шаг."""
        for attempt in (1, 2):
            try:
                completion = await self._anthropic.complete_structured(
                    system=system,
                    user=user,
                    response_model=HypothesisExplanationPayload,
                    max_tokens=1024,
                )
            except ValidationError:
                if attempt == 1:
                    _logger.warning(
                        "explain_hypothesis got malformed JSON; retrying once",
                    )
                    continue
                return _fail_soft(
                    locale=locale,
                    reason="LLM returned malformed JSON twice in a row",
                )
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == 1:
                    _logger.warning(
                        "explain_hypothesis got non-JSON response; retrying once",
                        exc_info=exc,
                    )
                    continue
                return _fail_soft(
                    locale=locale,
                    reason=f"LLM response was not JSON: {exc}",
                )
            else:
                payload = completion.parsed
                cost = estimate_cost_usd(
                    completion.model,
                    completion.input_tokens,
                    completion.output_tokens,
                )
                return HypothesisExplanation(
                    summary=payload.summary,
                    key_evidence=payload.key_evidence,
                    caveats=payload.caveats,
                    confidence_label=payload.confidence_label,
                    locale=locale,
                    tokens_used=completion.input_tokens + completion.output_tokens,
                    cost_usd=cost,
                    model=completion.model,
                    dry_run=False,
                )
        # Недостижимо — цикл всегда выходит через return; required mypy/ruff.
        msg = "unreachable: soft-retry loop completed without return"
        raise AssertionError(msg)


def _fail_soft(*, locale: LocaleLabel, reason: str) -> HypothesisExplanation:
    """Заглушка-ответ при non-recoverable LLM-ошибке.

    UI Phase 4.9 покажет короткое summary вместо «нет объяснения».
    """
    if locale == "ru":
        summary = "Не удалось сгенерировать объяснение: модель вернула некорректный ответ."
    else:
        summary = "Could not generate an explanation: the model returned an invalid response."
    return HypothesisExplanation(
        summary=summary,
        key_evidence=[],
        caveats=[reason],
        confidence_label="low",
        locale=locale,
        tokens_used=0,
        cost_usd=0.0,
        model="error",
        dry_run=False,
    )
