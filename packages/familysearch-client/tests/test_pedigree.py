"""Тесты для FamilySearchClient.get_pedigree (Phase 5.1).

Sample JSON ниже — упрощённая копия GEDCOM-X ancestry response. На каждой
персоне обязателен ``display.ascendancyNumber`` (Ahnentafel) — root=1,
father=2, mother=3, paternal grandfather=4, paternal grandmother=5,
maternal grandfather=6, maternal grandmother=7, и т.д.

Реальные API-вызовы помечаются ``@pytest.mark.familysearch_real`` и
не входят в Phase 5.1 (нужен sandbox key).
"""

from __future__ import annotations

import pytest
from familysearch_client import (
    FamilySearchClient,
    FamilySearchConfig,
    FsGender,
    FsPedigreeNode,
)
from pytest_httpx import HTTPXMock


def _ancestry_url(config: FamilySearchConfig, person_id: str) -> str:
    return f"{config.api_base_url}/platform/tree/persons/{person_id}/ancestry"


def _person_payload(person_id: str, ancestry_number: int, full_text: str) -> dict[str, object]:
    """Минимальная FS-person структура с Ahnentafel-номером."""
    return {
        "id": person_id,
        "display": {"ascendancyNumber": str(ancestry_number)},
        "gender": {"type": "http://gedcomx.org/Male"},
        "names": [
            {
                "preferred": True,
                "nameForms": [{"fullText": full_text}],
            }
        ],
    }


# Полное 3-поколенное дерево: 1 root + 2 родителей + 4 деда/бабушки = 7.
SAMPLE_PEDIGREE_3GEN: dict[str, object] = {
    "persons": [
        _person_payload("ROOT", 1, "Root Person"),
        _person_payload("FATHER", 2, "Father Person"),
        _person_payload("MOTHER", 3, "Mother Person"),
        _person_payload("PGF", 4, "Paternal Grandfather"),
        _person_payload("PGM", 5, "Paternal Grandmother"),
        _person_payload("MGF", 6, "Maternal Grandfather"),
        _person_payload("MGM", 7, "Maternal Grandmother"),
    ]
}


# Дерево с дырками: только root + отец + дед по отцу. Мать и её родители
# отсутствуют — типичный случай при импорте неполной FS-генеалогии.
SAMPLE_PEDIGREE_PARTIAL: dict[str, object] = {
    "persons": [
        _person_payload("ROOT", 1, "Root Person"),
        _person_payload("FATHER", 2, "Father Person"),
        _person_payload("PGF", 4, "Paternal Grandfather"),
    ]
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generations", [0, -1, 9, 100])
@pytest.mark.asyncio
async def test_get_pedigree_rejects_out_of_range_generations(generations: int) -> None:
    """generations вне [1, 8] — ValueError, без HTTP-запроса."""
    config = FamilySearchConfig.sandbox()
    async with FamilySearchClient(access_token="t", config=config) as fs:
        with pytest.raises(ValueError, match="generations must be in"):
            await fs.get_pedigree("ROOT", generations=generations)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pedigree_builds_three_generation_tree(httpx_mock: HTTPXMock) -> None:
    """Полное 3-поколенное дерево: 7 узлов, все связи set."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'ROOT')}?generations=3",
        json=SAMPLE_PEDIGREE_3GEN,
        status_code=200,
    )

    async with FamilySearchClient(access_token="bearer", config=config) as fs:
        tree = await fs.get_pedigree("ROOT", generations=3)

    assert isinstance(tree, FsPedigreeNode)
    assert tree.person.id == "ROOT"
    assert tree.father is not None
    assert tree.father.person.id == "FATHER"
    assert tree.mother is not None
    assert tree.mother.person.id == "MOTHER"

    # Generation 2 (grandparents)
    assert tree.father.father is not None
    assert tree.father.father.person.id == "PGF"
    assert tree.father.mother is not None
    assert tree.father.mother.person.id == "PGM"
    assert tree.mother.father is not None
    assert tree.mother.father.person.id == "MGF"
    assert tree.mother.mother is not None
    assert tree.mother.mother.person.id == "MGM"

    # Generation 3 — отсутствует (мы запросили только 3 поколения).
    assert tree.father.father.father is None


@pytest.mark.asyncio
async def test_get_pedigree_handles_partial_tree(httpx_mock: HTTPXMock) -> None:
    """Если у части ancestors неизвестны — соответствующие узлы = None."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'ROOT')}?generations=2",
        json=SAMPLE_PEDIGREE_PARTIAL,
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        tree = await fs.get_pedigree("ROOT", generations=2)

    assert tree.person.id == "ROOT"
    assert tree.father is not None
    assert tree.father.person.id == "FATHER"
    assert tree.mother is None  # Не вернулся в response
    assert tree.father.father is not None
    assert tree.father.father.person.id == "PGF"
    assert tree.father.mother is None


@pytest.mark.asyncio
async def test_get_pedigree_walk_returns_all_persons_preorder(
    httpx_mock: HTTPXMock,
) -> None:
    """walk() даёт persons в pre-order: root → father subtree → mother subtree."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'ROOT')}?generations=3",
        json=SAMPLE_PEDIGREE_3GEN,
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        tree = await fs.get_pedigree("ROOT", generations=3)

    ids = [p.id for p in tree.walk()]
    assert ids == ["ROOT", "FATHER", "PGF", "PGM", "MOTHER", "MGF", "MGM"]


@pytest.mark.asyncio
async def test_get_pedigree_passes_generations_query_param(httpx_mock: HTTPXMock) -> None:
    """Запрос несёт ?generations=N в URL."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'ROOT')}?generations=4",
        json=SAMPLE_PEDIGREE_PARTIAL,
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        await fs.get_pedigree("ROOT", generations=4)

    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.url.params["generations"] == "4"


@pytest.mark.asyncio
async def test_get_pedigree_normalises_gender_via_existing_mapper(
    httpx_mock: HTTPXMock,
) -> None:
    """FsPerson внутри FsPedigreeNode проходит через тот же mapper, что
    и get_person — значит gender URI уже нормализован в FsGender enum."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'ROOT')}?generations=1",
        json={
            "persons": [
                _person_payload("ROOT", 1, "Root"),
                _person_payload("FATHER", 2, "Father"),
                _person_payload("MOTHER", 3, "Mother"),
            ]
        },
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        tree = await fs.get_pedigree("ROOT", generations=1)

    assert tree.person.gender == FsGender.MALE


# ---------------------------------------------------------------------------
# Edge cases / errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pedigree_empty_response_raises_value_error(httpx_mock: HTTPXMock) -> None:
    """Без root persona в response — ValueError (нечего корнем брать)."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'GHOST')}?generations=1",
        json={"persons": []},
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        with pytest.raises(ValueError, match="no root person"):
            await fs.get_pedigree("GHOST", generations=1)


@pytest.mark.asyncio
async def test_get_pedigree_skips_persons_without_ascendancy_number(
    httpx_mock: HTTPXMock,
) -> None:
    """Persons без display.ascendancyNumber (или с непарсимым) пропускаются."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'ROOT')}?generations=1",
        json={
            "persons": [
                _person_payload("ROOT", 1, "Root"),
                # mother без display
                {
                    "id": "ORPHAN",
                    "names": [{"preferred": True, "nameForms": [{"fullText": "Orphan"}]}],
                },
                _person_payload("FATHER", 2, "Father"),
            ]
        },
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        tree = await fs.get_pedigree("ROOT", generations=1)

    # ORPHAN не попал в дерево, FATHER попал.
    walked_ids = {p.id for p in tree.walk()}
    assert walked_ids == {"ROOT", "FATHER"}


@pytest.mark.asyncio
async def test_get_pedigree_404_raises_not_found_error(httpx_mock: HTTPXMock) -> None:
    """404 → NotFoundError, без retry (ловим из общего pipeline)."""
    from familysearch_client import NotFoundError

    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=f"{_ancestry_url(config, 'GHOST')}?generations=1",
        status_code=404,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        with pytest.raises(NotFoundError):
            await fs.get_pedigree("GHOST", generations=1)
