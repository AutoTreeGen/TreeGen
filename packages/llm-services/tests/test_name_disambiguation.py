"""Тесты ``disambiguate_name_variants`` (Phase 10.0)."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from llm_services import NameCluster, disambiguate_name_variants


@pytest.mark.asyncio
async def test_groups_vladimir_diminutives(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """«Vladimir / Volodya / Володя» — один кластер."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"clusters": [{'
        '"canonical": "Vladimir", '
        '"variants": ["Vladimir", "Volodya", "Володя"], '
        '"confidence": 0.95}]}'
    )

    result = await disambiguate_name_variants(
        ["Vladimir", "Volodya", "Володя"],
        client=mock_anthropic_client,
    )

    assert len(result) == 1
    cluster = result[0]
    assert isinstance(cluster, NameCluster)
    assert cluster.canonical == "Vladimir"
    assert set(cluster.variants) == {"Vladimir", "Volodya", "Володя"}
    assert cluster.confidence == 0.95


@pytest.mark.asyncio
async def test_separates_distinct_names(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """«Vladimir» и «Vyacheslav» → разные кластеры."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"clusters": ['
        '{"canonical": "Vladimir", "variants": ["Vladimir", "Володя"], "confidence": 0.92},'
        '{"canonical": "Vyacheslav", "variants": ["Vyacheslav"], "confidence": 1.0}'
        "]}"
    )

    result = await disambiguate_name_variants(
        ["Vladimir", "Володя", "Vyacheslav"],
        client=mock_anthropic_client,
    )
    assert len(result) == 2
    canonicals = {c.canonical for c in result}
    assert canonicals == {"Vladimir", "Vyacheslav"}


@pytest.mark.asyncio
async def test_empty_variants_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        await disambiguate_name_variants([])


@pytest.mark.asyncio
async def test_variants_serialized_into_prompt(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """Все variants должны попасть в текст промпта (JSON-сериализация)."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"clusters": [{"canonical": "Yaakov", '
        '"variants": ["Yaakov", "Yankel"], "confidence": 0.9}]}'
    )

    await disambiguate_name_variants(
        ["Yaakov", "Yankel"],
        client=mock_anthropic_client,
    )
    user_text = mock_anthropic_client.messages.create.await_args.kwargs["messages"][0]["content"]
    assert "Yaakov" in user_text
    assert "Yankel" in user_text


@pytest.mark.asyncio
async def test_disambiguate_uses_json_schema(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"clusters": [{"canonical": "X", "variants": ["X"], "confidence": 0.9}]}'
    )
    await disambiguate_name_variants(["X"], client=mock_anthropic_client)
    fmt = mock_anthropic_client.messages.create.await_args.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "clusters" in fmt["schema"]["properties"]
