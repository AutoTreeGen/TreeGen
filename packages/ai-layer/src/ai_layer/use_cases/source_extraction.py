"""SourceExtractor — Phase 10.2 use case.

Claude читает текст одного источника (письмо, метрика, перепись,
надгробная надпись) и возвращает структурированные генеалогические
факты — :class:`ExtractionResult`. См. ADR-0059.

Дизайн:

- **Single-pass structured output.** Один ``messages.create`` на источник;
  prompt просит модель пройти стадии (структура → entities →
  relationships → confidence) внутренне, но JSON выдать целиком.
  Multi-pass — Phase 10.4+, см. ADR-0059 §«Multi-pass strategy».
- **Vision optional.** Caller (parser-service) пробует pypdf, при
  низкокачественном extract'е переключается на vision через
  :meth:`extract_facts_from_image`. Сам use-case не знает о PDF.
- **Quote-grounded validation.** ``raw_quote`` каждого экстракта должен
  встречаться в ``document_text`` (substring-check, case-insensitive).
  Если нет — `FabricatedQuoteError`. Это структурный аналог
  `FabricatedEvidenceError` из hypothesis suggester.
- **Caller-driven persistence.** Use-case возвращает чистый Pydantic-объект;
  parser-service сам пишет ``SourceExtraction`` row + ``ExtractedFact``
  rows. Это держит ai-layer без зависимости на sqlalchemy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_layer.clients.anthropic_client import (
    AnthropicClient,
    AnthropicCompletion,
    ImageInput,
)
from ai_layer.prompts.registry import PromptRegistry
from ai_layer.types import ExtractionResult

# Минимальная длина текста, на которой ещё имеет смысл вызывать LLM.
# Меньше — почти наверняка пустая страница / OCR-failure / случайный
# мусор; не тратим cost. Caller получит `EmptyDocumentError`.
_MIN_DOCUMENT_TEXT_LENGTH = 16

# Максимум символов одного входного документа. Sonnet 4.6 справляется и
# с большим, но 200k символов = ~50k tokens — outlier для нашего корпуса.
# Caller дробит на главы/страницы если нужно.
_MAX_DOCUMENT_TEXT_LENGTH = 200_000


class SourceExtractionError(RuntimeError):
    """Базовый класс ошибок use-case'а — для catch-all в caller'ах."""


class EmptyDocumentError(SourceExtractionError):
    """Документ слишком короткий, чтобы стоило вызывать LLM."""


class DocumentTooLargeError(SourceExtractionError):
    """Документ превышает лимит ``_MAX_DOCUMENT_TEXT_LENGTH``."""


class FabricatedQuoteError(SourceExtractionError):
    """LLM вернул ``raw_quote``, которого нет в исходном тексте.

    Атрибут ``offending_quotes`` хранит все нарушающие цитаты — caller
    может залогировать их и/или показать в UI как «AI hallucinated».
    """

    def __init__(self, offending_quotes: list[str]) -> None:
        self.offending_quotes = offending_quotes
        super().__init__(
            f"LLM returned {len(offending_quotes)} quote(s) not in source text "
            "(see ADR-0059 §'Defense against fabricated extractions').",
        )


@dataclass(frozen=True)
class SourceMetadata:
    """Metadata источника, передаваемая в prompt как «контекст, не извлекать».

    Attributes:
        title: ``Source.title``.
        author: ``Source.author`` или ``"Unknown"``.
        source_type: ``Source.source_type`` строкой.
        date: Опциональная дата публикации/создания источника.
        place: Опциональное место (для контекста — например, помогает
            LLM правильно интерпретировать топонимы).
    """

    title: str
    author: str | None
    source_type: str
    date: str | None = None
    place: str | None = None


class SourceExtractor:
    """Use-case ``SourceExtractor``.

    Args:
        anthropic: Клиент Claude (в тестах — со stub'ом
            ``anthropic.AsyncAnthropic``).
        registry: Registry промптов (default — глобальный).
        max_tokens: Лимит на ответ. Sonnet'у нужно много места:
            длинные документы дают много persons + events. 4096 — safe
            default; caller override'ит для больших корпусов.
    """

    def __init__(
        self,
        anthropic: AnthropicClient,
        registry: type[PromptRegistry] = PromptRegistry,
        *,
        max_tokens: int = 4096,
    ) -> None:
        self._anthropic = anthropic
        self._registry = registry
        self._max_tokens = max_tokens

    async def extract_from_text(
        self,
        document_text: str,
        metadata: SourceMetadata,
    ) -> AnthropicCompletion[ExtractionResult]:
        """Извлечь факты из plain-text документа.

        Raises:
            EmptyDocumentError: Текст слишком короткий.
            DocumentTooLargeError: Текст превышает лимит.
            FabricatedQuoteError: LLM сослался на цитату, которой нет
                в исходнике.
            pydantic.ValidationError: LLM вернул JSON, не подходящий
                под :class:`ExtractionResult`.
        """
        normalized = document_text.strip()
        if len(normalized) < _MIN_DOCUMENT_TEXT_LENGTH:
            msg = (
                f"Document text too short ({len(normalized)} chars); "
                f"need at least {_MIN_DOCUMENT_TEXT_LENGTH}."
            )
            raise EmptyDocumentError(msg)
        if len(normalized) > _MAX_DOCUMENT_TEXT_LENGTH:
            msg = (
                f"Document text too large ({len(normalized)} chars); "
                f"limit is {_MAX_DOCUMENT_TEXT_LENGTH}. Caller should chunk."
            )
            raise DocumentTooLargeError(msg)

        completion = await self._render_and_call(
            document_text=normalized,
            metadata=metadata,
            image=None,
        )
        _validate_quotes_in_text(completion.parsed, normalized)
        return completion

    async def extract_from_image(
        self,
        image: ImageInput,
        metadata: SourceMetadata,
        *,
        ocr_text_hint: str | None = None,
    ) -> AnthropicCompletion[ExtractionResult]:
        """Извлечь факты из изображения (скан, фото) через vision API.

        Args:
            image: Base64-кодированный image-blob.
            metadata: Metadata источника (см. :class:`SourceMetadata`).
            ocr_text_hint: Опциональный OCR-результат низкого качества —
                LLM использует как ориентир. ``None`` — модель работает
                чисто с изображением.

        Note:
            ``raw_quote`` валидация при vision-режиме ослаблена: если
            ``ocr_text_hint`` задан, проверяем substring в нём; иначе
            пропускаем (нет «исходного текста» для substring-match'а).
            Caller сам видит quote в UI и может оценить достоверность.
        """
        document_text = ocr_text_hint or "[image-only document, no OCR available]"
        completion = await self._render_and_call(
            document_text=document_text,
            metadata=metadata,
            image=image,
        )
        if ocr_text_hint:
            _validate_quotes_in_text(completion.parsed, ocr_text_hint)
        return completion

    async def _render_and_call(
        self,
        *,
        document_text: str,
        metadata: SourceMetadata,
        image: ImageInput | None,
    ) -> AnthropicCompletion[ExtractionResult]:
        """Отрендерить prompt и сделать structured call.

        Не валидирует quotes (это делает ``extract_from_*``-метод).
        """
        template = self._registry.SOURCE_EXTRACTOR_V1
        rendered = template.render(
            document_text=document_text,
            source_title=metadata.title,
            source_author=metadata.author or "Unknown",
            source_type=metadata.source_type,
            source_date=metadata.date or "",
            source_place=metadata.place or "",
        )
        completion: AnthropicCompletion[
            ExtractionResult
        ] = await self._anthropic.complete_structured(
            system=rendered.system,
            user=rendered.user,
            response_model=ExtractionResult,
            max_tokens=self._max_tokens,
            image=image,
        )
        return completion


def _validate_quotes_in_text(result: ExtractionResult, document_text: str) -> None:
    """Проверить, что все ``raw_quote`` встречаются в ``document_text``.

    Сравнение нечувствительное к whitespace и регистру: LLM может
    пересжать пробелы или нормализовать кавычки. Достаточно, чтобы
    «cleaned» вариант quote был substring «cleaned» документа.
    """
    cleaned_doc = _clean_for_match(document_text)
    offenders: list[str] = []
    for person in result.persons:
        if not _quote_matches(person.raw_quote, cleaned_doc):
            offenders.append(person.raw_quote)
    for event in result.events:
        if not _quote_matches(event.raw_quote, cleaned_doc):
            offenders.append(event.raw_quote)
    for relationship in result.relationships:
        if not _quote_matches(relationship.raw_quote, cleaned_doc):
            offenders.append(relationship.raw_quote)
    if offenders:
        raise FabricatedQuoteError(offenders)


def _clean_for_match(value: str) -> str:
    """Нормализовать строку для substring-сравнения.

    Сжимает whitespace, опускает регистр. Не делает unicode-нормализацию
    (NFC/NFD): для кириллицы и иврита это часто меняет семантику. Если
    в будущем появятся false-positive — добавим.
    """
    return re.sub(r"\s+", " ", value).strip().lower()


def _quote_matches(quote: str, cleaned_doc: str) -> bool:
    """Истинно, если cleaned-quote — substring cleaned-doc'а."""
    cleaned_quote = _clean_for_match(quote)
    if not cleaned_quote:
        return False
    return cleaned_quote in cleaned_doc


__all__ = [
    "DocumentTooLargeError",
    "EmptyDocumentError",
    "FabricatedQuoteError",
    "SourceExtractionError",
    "SourceExtractor",
    "SourceMetadata",
]
