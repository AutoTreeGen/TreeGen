"""Тесты use-case ``SourceExtractor`` (Phase 10.2 / ADR-0059).

Покрывают:

* Happy path с clean ru/en/he текстом → корректный ExtractionResult.
* Dirty OCR (низкое качество) → low-confidence + overall_confidence < 0.5.
* Multi-language detection.
* Fabricated quote → FabricatedQuoteError.
* Empty / too-large document → соответствующие ошибки.
* Vision path с image input.
"""

from __future__ import annotations

import json

import pytest
from _fakes import FakeMessage, FakeTextBlock, FakeUsage
from ai_layer.clients.anthropic_client import AnthropicClient, ImageInput
from ai_layer.config import AILayerConfig
from ai_layer.use_cases.source_extraction import (
    DocumentTooLargeError,
    EmptyDocumentError,
    FabricatedQuoteError,
    SourceExtractor,
    SourceMetadata,
)


def _metadata() -> SourceMetadata:
    return SourceMetadata(
        title="Slonim parish register 1850",
        author="Russian Orthodox Church",
        source_type="metric_record",
        date="1850",
        place="Slonim, Grodno",
    )


def _ok_result_payload(*, person_quote: str, event_quote: str | None = None) -> dict:
    persons = [
        {
            "full_name": "Ivan Petrov",
            "given_name": "Ivan",
            "surname": "Petrov",
            "sex": "M",
            "birth_date_raw": "1850",
            "birth_place_raw": "Slonim",
            "death_date_raw": None,
            "death_place_raw": None,
            "relationship_hints": [],
            "raw_quote": person_quote,
            "confidence": 0.8,
        }
    ]
    events: list[dict] = []
    if event_quote:
        events.append(
            {
                "event_type": "BIRT",
                "date_raw": "1850",
                "place_raw": "Slonim",
                "participants_hints": ["Ivan Petrov"],
                "description": None,
                "raw_quote": event_quote,
                "confidence": 0.85,
            }
        )
    return {
        "persons": persons,
        "events": events,
        "relationships": [],
        "document_summary": "1850 birth record from Slonim parish.",
        "overall_confidence": 0.8,
        "language_detected": "ru",
    }


@pytest.mark.asyncio
async def test_extract_from_text_happy_path(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    document = (
        "В метрической книге православной церкви г. Слоним за 1850 год "
        "записан Иван Петров, родившийся в Слониме."
    )
    payload = _ok_result_payload(person_quote="Иван Петров", event_quote="за 1850 год")

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text=json.dumps(payload, ensure_ascii=False))],
            usage=FakeUsage(input_tokens=300, output_tokens=120),
        )

    fake = make_fake_anthropic(responder)
    extractor = SourceExtractor(AnthropicClient(enabled_config, client=fake))
    completion = await extractor.extract_from_text(document, _metadata())

    assert len(completion.parsed.persons) == 1
    assert completion.parsed.persons[0].full_name == "Ivan Petrov"
    assert completion.parsed.language_detected == "ru"
    assert completion.input_tokens == 300
    assert completion.output_tokens == 120
    # Метаданные просочились в prompt.
    sent_user = fake.messages.calls[0]["messages"][0]["content"]
    assert "Slonim parish register 1850" in sent_user
    # Документ передан целиком.
    assert "Иван Петров" in sent_user


@pytest.mark.asyncio
async def test_extract_from_text_fabricated_quote_raises(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    """LLM вернул quote, которой нет в исходнике → FabricatedQuoteError."""
    document = "Real text content that does not contain the fabricated phrase."
    payload = _ok_result_payload(person_quote="Сергей Волков")

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload, ensure_ascii=False))])

    extractor = SourceExtractor(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    with pytest.raises(FabricatedQuoteError) as exc_info:
        await extractor.extract_from_text(document, _metadata())
    assert "Сергей Волков" in exc_info.value.offending_quotes


@pytest.mark.asyncio
async def test_extract_from_text_empty_document_raises(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    extractor = SourceExtractor(
        AnthropicClient(enabled_config, client=make_fake_anthropic(lambda **_: FakeMessage([])))
    )
    with pytest.raises(EmptyDocumentError):
        await extractor.extract_from_text("hi", _metadata())


@pytest.mark.asyncio
async def test_extract_from_text_too_large_document_raises(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    extractor = SourceExtractor(
        AnthropicClient(enabled_config, client=make_fake_anthropic(lambda **_: FakeMessage([])))
    )
    huge = "x" * 200_001
    with pytest.raises(DocumentTooLargeError):
        await extractor.extract_from_text(huge, _metadata())


@pytest.mark.asyncio
async def test_extract_dirty_ocr_low_confidence_passes(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    """OCR-мусорный текст: LLM отвечает с низким confidence, не хайпит."""
    document = "M et r i c a l  r e c o rd:  Iv@n  Pe!rov  born ~1850 i#  Slon-im"
    payload = {
        "persons": [
            {
                "full_name": "Iv@n Pe!rov",
                "given_name": None,
                "surname": None,
                "sex": "U",
                "birth_date_raw": "~1850",
                "birth_place_raw": "Slon-im",
                "death_date_raw": None,
                "death_place_raw": None,
                "relationship_hints": [],
                "raw_quote": "Iv@n  Pe!rov",
                "confidence": 0.25,
            }
        ],
        "events": [],
        "relationships": [],
        "document_summary": "Heavily OCR'd record, low confidence.",
        "overall_confidence": 0.3,
        "language_detected": "en",
    }

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload))])

    extractor = SourceExtractor(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    completion = await extractor.extract_from_text(document, _metadata())
    assert completion.parsed.overall_confidence < 0.5
    assert completion.parsed.persons[0].confidence < 0.5


@pytest.mark.asyncio
async def test_extract_multi_language_mixed(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    document = (
        "Moshe ben Avraham (Михаил Абрамович), born 5610 (1850 CE) in Vilna. "
        "משה בן אברהם נולד בוילנא."
    )
    payload = {
        "persons": [
            {
                "full_name": "Moshe ben Avraham",
                "given_name": "Moshe",
                "surname": None,
                "sex": "M",
                "birth_date_raw": "5610 (1850 CE)",
                "birth_place_raw": "Vilna",
                "death_date_raw": None,
                "death_place_raw": None,
                "relationship_hints": ["civil name: Михаил Абрамович"],
                "raw_quote": "Moshe ben Avraham",
                "confidence": 0.9,
            }
        ],
        "events": [],
        "relationships": [],
        "document_summary": "Birth record in three languages.",
        "overall_confidence": 0.85,
        "language_detected": "mixed",
    }

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload, ensure_ascii=False))])

    extractor = SourceExtractor(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    completion = await extractor.extract_from_text(document, _metadata())
    assert completion.parsed.language_detected == "mixed"
    assert "civil name: Михаил Абрамович" in completion.parsed.persons[0].relationship_hints


@pytest.mark.asyncio
async def test_extract_kill_switch_raises(disabled_config, make_fake_anthropic) -> None:
    from ai_layer.config import AILayerDisabledError

    extractor = SourceExtractor(
        AnthropicClient(
            disabled_config,
            client=make_fake_anthropic(lambda **_: FakeMessage([])),
        )
    )
    with pytest.raises(AILayerDisabledError):
        await extractor.extract_from_text(
            "Something long enough to bypass the empty check.", _metadata()
        )


@pytest.mark.asyncio
async def test_extract_from_image_passes_image_block(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    """Vision-режим: SDK получает image-блок в content-list."""
    payload = _ok_result_payload(person_quote="John Smith")

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload))])

    fake = make_fake_anthropic(responder)
    extractor = SourceExtractor(AnthropicClient(enabled_config, client=fake))
    image = ImageInput(data_b64="aGVsbG8=", media_type="image/jpeg")
    completion = await extractor.extract_from_image(
        image,
        _metadata(),
        ocr_text_hint="John Smith born 1900",
    )
    assert completion.parsed.persons[0].full_name == "Ivan Petrov"
    # Проверяем shape посланного content-list'а.
    sent_content = fake.messages.calls[0]["messages"][0]["content"]
    assert isinstance(sent_content, list)
    assert sent_content[0]["type"] == "image"
    assert sent_content[0]["source"]["media_type"] == "image/jpeg"
    assert sent_content[0]["source"]["data"] == "aGVsbG8="
    assert sent_content[1]["type"] == "text"


@pytest.mark.asyncio
async def test_extract_from_image_without_ocr_skips_quote_validation(
    enabled_config: AILayerConfig, make_fake_anthropic
) -> None:
    """Без ``ocr_text_hint`` quote-validation пропускается (нет исходного текста)."""
    payload = _ok_result_payload(person_quote="Имя из изображения")

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload, ensure_ascii=False))])

    extractor = SourceExtractor(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    image = ImageInput(data_b64="x", media_type="image/png")
    # Должно пройти без FabricatedQuoteError.
    completion = await extractor.extract_from_image(image, _metadata())
    assert completion.parsed.persons[0].full_name == "Ivan Petrov"
