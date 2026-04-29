"""Тесты ``VoyageEmbeddingClient`` со stub'ом SDK."""

from __future__ import annotations

import pytest
from _fakes import FakeVoyageResult
from ai_layer.clients.voyage_client import VoyageEmbeddingClient, _dedup
from ai_layer.config import AILayerConfig, AILayerConfigError, AILayerDisabledError


@pytest.mark.asyncio
async def test_embed_happy_path(
    enabled_config: AILayerConfig,
    make_fake_voyage,
) -> None:
    """Уникальные тексты → векторы возвращаются 1:1, model_version проброшен."""

    def responder(**_: object) -> FakeVoyageResult:
        return FakeVoyageResult(embeddings=[[0.1, 0.2], [0.3, 0.4]])

    fake = make_fake_voyage(responder)
    client = VoyageEmbeddingClient(enabled_config, client=fake)

    result = await client.embed(["a", "b"])

    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert result.index_map == [0, 1]
    assert result.model_version == "voyage-3"
    assert fake.calls[0]["texts"] == ["a", "b"]
    assert fake.calls[0]["input_type"] == "document"


@pytest.mark.asyncio
async def test_embed_dedups_duplicates(
    enabled_config: AILayerConfig,
    make_fake_voyage,
) -> None:
    """Дубликаты в input склеиваются перед запросом, разворачиваются через index_map."""

    def responder(*, texts: list[str], **_: object) -> FakeVoyageResult:
        # Voyage увидит только уникальные строки.
        assert texts == ["a", "b"]
        return FakeVoyageResult(embeddings=[[1.0], [2.0]])

    fake = make_fake_voyage(responder)
    client = VoyageEmbeddingClient(enabled_config, client=fake)

    result = await client.embed(["a", "b", "a", "a"])

    assert result.vectors == [[1.0], [2.0]]
    assert result.index_map == [0, 1, 0, 0]


@pytest.mark.asyncio
async def test_embed_normalizes_unicode_and_whitespace(
    enabled_config: AILayerConfig,
    make_fake_voyage,
) -> None:
    """NFKC + strip — варианты «  Иосиф » сворачиваются в один embedding."""

    def responder(*, texts: list[str], **_: object) -> FakeVoyageResult:
        assert texts == ["Иосиф"]
        return FakeVoyageResult(embeddings=[[0.5]])

    client = VoyageEmbeddingClient(enabled_config, client=make_fake_voyage(responder))
    result = await client.embed(["  Иосиф ", "Иосиф", "Иосиф  "])
    assert result.index_map == [0, 0, 0]


@pytest.mark.asyncio
async def test_embed_empty_input_raises(
    enabled_config: AILayerConfig,
    make_fake_voyage,
) -> None:
    def responder(**_: object) -> FakeVoyageResult:
        return FakeVoyageResult(embeddings=[])

    client = VoyageEmbeddingClient(enabled_config, client=make_fake_voyage(responder))
    with pytest.raises(ValueError, match="non-empty"):
        await client.embed([])


@pytest.mark.asyncio
async def test_embed_disabled_blocks_call(
    disabled_config: AILayerConfig,
    make_fake_voyage,
) -> None:
    def responder(**_: object) -> FakeVoyageResult:
        msg = "should not be called"
        raise AssertionError(msg)

    fake = make_fake_voyage(responder)
    client = VoyageEmbeddingClient(disabled_config, client=fake)
    with pytest.raises(AILayerDisabledError):
        await client.embed(["a"])
    assert fake.calls == []


@pytest.mark.asyncio
async def test_embed_no_api_key() -> None:
    config = AILayerConfig(enabled=True, voyage_api_key=None)
    client = VoyageEmbeddingClient(config, client=None)
    with pytest.raises(AILayerConfigError):
        await client.embed(["a"])


@pytest.mark.asyncio
async def test_embed_dict_response_supported(
    enabled_config: AILayerConfig,
) -> None:
    """SDK-стаб иногда возвращает dict — поддерживаем оба варианта."""

    class DictResponder:
        async def embed(self, **_: object) -> dict[str, list[list[float]]]:
            return {"embeddings": [[0.1]]}

    client = VoyageEmbeddingClient(enabled_config, client=DictResponder())
    result = await client.embed(["a"])
    assert result.vectors == [[0.1]]


def test_dedup_pure() -> None:
    """``_dedup`` — pure: без I/O, детерминированный."""
    unique, idx = _dedup(["alpha", "Beta", "alpha", "  Beta "])
    assert unique == ["alpha", "Beta"]
    assert idx == [0, 1, 0, 1]
