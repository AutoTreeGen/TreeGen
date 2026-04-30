"""PlaceNormalizer + NameNormalizer — Phase 10.3 use cases (см. ADR-0060).

Принимают одну raw-строку (place или person-name), возвращают
структурированную нормализацию + опциональный ranked candidate-match
к canonical-формам, которые уже есть у пользователя.

Дизайнерские решения:

- **Read-only.** Use-case'ы ничего не пишут в БД сами; caller-уровень
  решает, что делать с результатом (показать в UI, обновить
  ``places.canonical_name``, добавить ``place_aliases``-row).
- **Decoupled от ORM.** На вход — plain Pydantic-модели, на выход —
  ``NormalizationResult``. Никакой импорт sqlalchemy / shared_models.
  Это сохраняет ai-layer без heavyweight import-graph (см. ADR-0043).
- **Voyage-match опционален.** Caller передаёт список candidates
  (например, существующие ``places.canonical_name`` для tree'а
  пользователя); если список пустой — Voyage не вызывается, экономим
  cost. Если непустой — embedded и ranked top-K возвращаются в
  ``NormalizationResult.candidates``.
- **Soft-fail на malformed JSON.** Один retry; если и второй раз
  невалидный — fail-soft ``NormalizationResult`` с low-confidence и
  пометкой в notes (зеркалит поведение HypothesisExplainer из 10.1).
- **Dry-run mode.** ``AI_DRY_RUN=true`` → возвращается зашитый mock,
  Anthropic не вызывается, Voyage не вызывается. Для локального dev.
- **Cost.** Лимит вывода — 512 tokens (нормализация одной строки —
  компактный JSON). Truncation prompt'а здесь не нужна (raw — одна
  строка), но caller отвечает за длину raw / context.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.clients.voyage_client import VoyageEmbeddingClient
from ai_layer.pricing import estimate_cost_usd
from ai_layer.prompts.registry import PromptRegistry
from ai_layer.types import (
    CandidateMatch,
    LocaleHintLabel,
    NameNormalization,
    NormalizationResult,
    PlaceNormalization,
    ScriptLabel,
)

_logger = logging.getLogger(__name__)

#: Жёсткий лимит длины raw-входа. Нормализация — это **одна строка**:
#: typical 8-100 символов, длинные edge-case'ы 200-300 (полное имя со
#: всеми титулами). > 1024 символов значит caller передаёт документ,
#: а не имя/место — отказываемся.
MAX_RAW_LENGTH = 1024

#: Сколько токенов отдаём ответу. Один JSON ~150-300 токенов; 512 даёт
#: запас на длинный notes / большой alternative_forms-список.
DEFAULT_MAX_OUTPUT_TOKENS = 512

#: Сколько candidate'ов оставляем в выдаче после Voyage-ranking.
DEFAULT_TOP_K_CANDIDATES = 5

#: Если caller передал > MAX_CANDIDATES, обрезаем — Voyage биллит за
#: токены, embedding 200 названий за один вызов нормализации = выход
#: за target $0.005/call. Caller pre-фильтрует (по country / tree_id).
MAX_CANDIDATES = 50

DRY_RUN_ENV_VAR = "AI_DRY_RUN"


class NormalizationError(RuntimeError):
    """Базовый класс для ошибок normalize-use-case'ов."""


class EmptyInputError(NormalizationError):
    """Raw-строка пустая после strip."""


class RawInputTooLargeError(NormalizationError):
    """Raw-строка превышает ``MAX_RAW_LENGTH`` — caller передал не имя/место."""


@dataclass(frozen=True)
class CandidateRecord:
    """Один кандидат для Voyage-match.

    Caller-уровень собирает их из БД (например, ``places.canonical_name``
    для всех мест дерева пользователя). Контракт: ``id`` — opaque
    строка (UUID или surrogate); ``text`` — то, что эмбеддится.
    """

    id: str
    text: str


def _is_dry_run(env: dict[str, str] | None = None) -> bool:
    """``AI_DRY_RUN=true|1|yes|on`` → mock-режим без Anthropic/Voyage."""
    source = env if env is not None else os.environ
    raw = source.get(DRY_RUN_ENV_VAR, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _validate_raw(raw: str) -> str:
    """Очистить и проверить raw-строку перед вызовом LLM."""
    cleaned = raw.strip()
    if not cleaned:
        msg = "Raw input is empty after strip"
        raise EmptyInputError(msg)
    if len(cleaned) > MAX_RAW_LENGTH:
        msg = (
            f"Raw input is {len(cleaned)} chars; limit is {MAX_RAW_LENGTH}. "
            "This use case normalizes a single name/place, not a document."
        )
        raise RawInputTooLargeError(msg)
    return cleaned


def _dry_run_place() -> PlaceNormalization:
    """Зашитый mock для разработки без Anthropic-ключа."""
    return PlaceNormalization(
        canonical_name="Yuzerin",
        country_modern="Belarus",
        country_historical="Russian Empire",
        admin1="Gomel Region",
        settlement="village",
        confidence=0.6,
        ethnicity_hint="ashkenazi_jewish",
        alternative_forms=["Юзерин", "Yuzeryn"],
        notes="Dry-run mock; no real LLM call was made.",
    )


def _dry_run_name() -> NameNormalization:
    return NameNormalization(
        given="Ivan",
        surname="Zhidnitsky",
        patronymic="Petrovich",
        given_alts=["Иван", "Iwan"],
        surname_alts=["Жидницкий", "Żydnicki"],
        script_detected="cyrillic",
        transliteration_scheme="bgn_pcgn",
        ethnicity_hint="slavic",
        tribe_marker="unknown",
        confidence=0.7,
        notes="Dry-run mock; no real LLM call was made.",
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Косинусное сходство двух equal-длины векторов в [-1, 1].

    Pure-функция; clip'аем в [0, 1] на возврате — для генеалогических
    эмбеддингов отрицательные cosine-значения почти не встречаются и
    UI-удобнее уметь mapnut'ь на progress-bar.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    raw_score = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, raw_score))


async def _rank_candidates(
    *,
    voyage: VoyageEmbeddingClient,
    query_text: str,
    candidates: list[CandidateRecord],
    top_k: int,
) -> list[CandidateMatch]:
    """Получить top-K candidates по cosine similarity к ``query_text``.

    Один Voyage-вызов на embed=[query] + [candidate.text...]. Voyage
    дедуплицирует одинаковые строки внутри батча, поэтому повторы среди
    candidates не удваивают cost (см. ``VoyageEmbeddingClient._dedup``).

    Если caller передал > ``MAX_CANDIDATES``, обрезаем (логируем warning):
    caller должен pre-фильтровать (country / tree_id / phonetic-bucket).
    """
    if not candidates:
        return []
    if len(candidates) > MAX_CANDIDATES:
        _logger.warning(
            "normalize: candidate list truncated",
            extra={"received": len(candidates), "kept": MAX_CANDIDATES},
        )
        candidates = candidates[:MAX_CANDIDATES]

    texts = [query_text] + [c.text for c in candidates]
    result = await voyage.embed(texts, input_type="document")
    # index_map[i] — индекс уникальной нормализованной строки в result.vectors;
    # query текст — это texts[0] → result.vectors[result.index_map[0]].
    query_vector = result.vectors[result.index_map[0]]
    candidate_vectors = [result.vectors[result.index_map[i + 1]] for i in range(len(candidates))]

    scored = [
        (cand, _cosine_similarity(query_vector, vec))
        for cand, vec in zip(candidates, candidate_vectors, strict=True)
    ]
    # Sort desc by score, stable on input order (для детерминированности тестов).
    scored.sort(key=lambda pair: -pair[1])

    matches: list[CandidateMatch] = []
    for rank, (cand, score) in enumerate(scored[:top_k], start=1):
        matches.append(
            CandidateMatch(
                candidate_id=cand.id,
                candidate_text=cand.text,
                score=score,
                rank=rank,
            )
        )
    return matches


def _fail_soft_place(reason: str) -> PlaceNormalization:
    """Заглушка-ответ при non-recoverable LLM-ошибке для place."""
    return PlaceNormalization(
        canonical_name="(unrecognized)",
        confidence=0.0,
        ethnicity_hint="unknown",
        notes=f"AI normalization failed: {reason}",
    )


def _fail_soft_name(reason: str) -> NameNormalization:
    """Заглушка-ответ при non-recoverable LLM-ошибке для name."""
    return NameNormalization(
        confidence=0.0,
        ethnicity_hint="unknown",
        tribe_marker="unknown",
        script_detected="unknown",
        transliteration_scheme="none",
        notes=f"AI normalization failed: {reason}",
    )


class PlaceNormalizer:
    """Use-case ``PlaceNormalizer`` (Phase 10.3).

    Args:
        anthropic: Клиент Claude API.
        voyage: Клиент Voyage AI; ``None`` отключает candidate-match.
        registry: Registry промптов (default — глобальный).
        env: Опциональный override для ``os.environ`` (для тестов dry-run).
    """

    def __init__(
        self,
        anthropic: AnthropicClient,
        *,
        voyage: VoyageEmbeddingClient | None = None,
        registry: type[PromptRegistry] = PromptRegistry,
        env: dict[str, str] | None = None,
    ) -> None:
        self._anthropic = anthropic
        self._voyage = voyage
        self._registry = registry
        self._env = env

    async def normalize(
        self,
        raw: str,
        *,
        locale_hint: LocaleHintLabel | None = None,
        context: str | None = None,
        candidates: list[CandidateRecord] | None = None,
        top_k: int = DEFAULT_TOP_K_CANDIDATES,
    ) -> NormalizationResult:
        """Нормализовать одну raw-строку места.

        Raises:
            EmptyInputError: ``raw`` пустая после strip.
            RawInputTooLargeError: ``raw`` длиннее ``MAX_RAW_LENGTH``.
        """
        cleaned = _validate_raw(raw)

        if _is_dry_run(self._env):
            return NormalizationResult(
                kind="place",
                place=_dry_run_place(),
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                model="dry-run",
                dry_run=True,
            )

        rendered = self._registry.PLACE_NORMALIZER_V1.render(
            raw=cleaned,
            locale_hint=locale_hint,
            context=context,
        )
        place, input_tokens, output_tokens, cost, model = await _call_with_retry(
            anthropic=self._anthropic,
            system=rendered.system,
            user=rendered.user,
            response_model=PlaceNormalization,
            fail_soft=_fail_soft_place,
        )

        matches: list[CandidateMatch] = []
        if candidates and self._voyage is not None and place.canonical_name:
            try:
                matches = await _rank_candidates(
                    voyage=self._voyage,
                    query_text=place.canonical_name,
                    candidates=candidates,
                    top_k=top_k,
                )
            except Exception:
                _logger.warning(
                    "normalize: Voyage candidate-match failed; returning empty matches",
                    exc_info=True,
                )

        return NormalizationResult(
            kind="place",
            place=place,
            candidates=matches,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=model,
            dry_run=False,
        )


class NameNormalizer:
    """Use-case ``NameNormalizer`` (Phase 10.3).

    Args:
        anthropic: Клиент Claude API.
        voyage: Клиент Voyage AI; ``None`` отключает candidate-match.
        registry: Registry промптов.
        env: Опциональный override для ``os.environ``.
    """

    def __init__(
        self,
        anthropic: AnthropicClient,
        *,
        voyage: VoyageEmbeddingClient | None = None,
        registry: type[PromptRegistry] = PromptRegistry,
        env: dict[str, str] | None = None,
    ) -> None:
        self._anthropic = anthropic
        self._voyage = voyage
        self._registry = registry
        self._env = env

    async def normalize(
        self,
        raw: str,
        *,
        script_hint: ScriptLabel | None = None,
        locale_hint: LocaleHintLabel | None = None,
        context: str | None = None,
        candidates: list[CandidateRecord] | None = None,
        top_k: int = DEFAULT_TOP_K_CANDIDATES,
    ) -> NormalizationResult:
        """Нормализовать одну raw-строку имени.

        Raises:
            EmptyInputError: ``raw`` пустая после strip.
            RawInputTooLargeError: ``raw`` длиннее ``MAX_RAW_LENGTH``.
        """
        cleaned = _validate_raw(raw)

        if _is_dry_run(self._env):
            return NormalizationResult(
                kind="name",
                name=_dry_run_name(),
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                model="dry-run",
                dry_run=True,
            )

        rendered = self._registry.NAME_NORMALIZER_V1.render(
            raw=cleaned,
            script_hint=script_hint,
            locale_hint=locale_hint,
            context=context,
        )
        name, input_tokens, output_tokens, cost, model = await _call_with_retry(
            anthropic=self._anthropic,
            system=rendered.system,
            user=rendered.user,
            response_model=NameNormalization,
            fail_soft=_fail_soft_name,
        )

        matches: list[CandidateMatch] = []
        if candidates and self._voyage is not None:
            # Для имён эмбеддим конкатенацию given+surname — это даёт
            # более информативный вектор, чем эмбеддинг одного surname.
            query_parts = [part for part in (name.given, name.surname) if part]
            query_text = " ".join(query_parts) if query_parts else cleaned
            try:
                matches = await _rank_candidates(
                    voyage=self._voyage,
                    query_text=query_text,
                    candidates=candidates,
                    top_k=top_k,
                )
            except Exception:
                _logger.warning(
                    "normalize: Voyage candidate-match failed; returning empty matches",
                    exc_info=True,
                )

        return NormalizationResult(
            kind="name",
            name=name,
            candidates=matches,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=model,
            dry_run=False,
        )


async def _call_with_retry[T: PlaceNormalization | NameNormalization](
    *,
    anthropic: AnthropicClient,
    system: str,
    user: str,
    response_model: type[T],
    fail_soft: Callable[[str], T],
) -> tuple[T, int, int, float, str]:
    """Один retry на ValidationError + fail-soft в третий шаг.

    Возвращает ``(parsed, input_tokens, output_tokens, cost_usd, model)``.
    На fail-soft cost фиксируется в 0 (caller-аналитика отделяет такие
    записи через ``model == "error"``).
    """
    for attempt in (1, 2):
        try:
            completion = await anthropic.complete_structured(
                system=system,
                user=user,
                response_model=response_model,
                max_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        except ValidationError:
            if attempt == 1:
                _logger.warning("normalize: malformed JSON; retrying once")
                continue
            soft = fail_soft("LLM returned malformed JSON twice in a row")
            return soft, 0, 0, 0.0, "error"
        except (ValueError, RuntimeError) as exc:
            # ValueError: пустой response.content; RuntimeError: SDK-level error.
            # Disabled / config errors сюда НЕ попадают (они subclass'ы RuntimeError,
            # но мы хотим, чтобы они пробрасывались — используем явный re-raise).
            from ai_layer.config import (  # noqa: PLC0415 — circular import guard
                AILayerConfigError,
                AILayerDisabledError,
            )

            if isinstance(exc, AILayerDisabledError | AILayerConfigError):
                raise
            if attempt == 1:
                _logger.warning("normalize: transient error; retrying once", exc_info=exc)
                continue
            soft = fail_soft(f"LLM call failed: {exc}")
            return soft, 0, 0, 0.0, "error"
        else:
            cost = estimate_cost_usd(
                completion.model,
                completion.input_tokens,
                completion.output_tokens,
            )
            return (
                completion.parsed,
                completion.input_tokens,
                completion.output_tokens,
                cost,
                completion.model,
            )
    msg = "unreachable: retry loop completed without return"
    raise AssertionError(msg)


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_TOP_K_CANDIDATES",
    "DRY_RUN_ENV_VAR",
    "MAX_CANDIDATES",
    "MAX_RAW_LENGTH",
    "CandidateRecord",
    "EmptyInputError",
    "NameNormalizer",
    "NormalizationError",
    "PlaceNormalizer",
    "RawInputTooLargeError",
]
