"""Интеграционные тесты ``GET /trees/{id}/persons/search``.

Проверяем:
- empty ``q`` → возвращает всех (с пагинацией)
- ``q=Zhit`` (case-insensitive) → находит Zhitnitzky
- ``birth_year_min`` / ``birth_year_max`` фильтр работает
- комбинация ``q`` + год — AND
- 404 на несуществующее дерево
- SQL-injection-попытка не валится и не возвращает лишнего
- ILIKE-метасимволы (``%`` / ``_``) escape'аются и не работают как wildcard
- pagination (``limit`` / ``offset``) корректна
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Дерево с разнообразными именами + годами для матрицы тестов:
# Vladimir Zhitnitzky 1945, Mary Smith 1850, John Smith 1920, Anna Kowalski (без года).
_GED_SEARCH_FIXTURE = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Vladimir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1945
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
1 BIRT
2 DATE 1850
0 @I3@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1920
0 @I4@ INDI
1 NAME Anna /Kowalski/
1 SEX F
0 TRLR
"""


async def _import_fixture(app_client) -> str:
    """Импортировать фикстуру и вернуть tree_id."""
    files = {"file": ("test.ged", _GED_SEARCH_FIXTURE, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code in (200, 201), created.text
    return created.json()["tree_id"]


@pytest.mark.asyncio
async def test_empty_query_returns_all_persons(app_client) -> None:
    """Без q / годов endpoint эквивалентен list-эндпоинту, возвращает всех 4."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert len(body["items"]) == 4
    assert body["limit"] == 50
    assert body["offset"] == 0


@pytest.mark.asyncio
async def test_query_finds_surname_case_insensitive(app_client) -> None:
    """``q=Zhit`` (lowercase prefix) → находит Zhitnitzky через ILIKE."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "Zhit"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Vladimir Zhitnitzky"


@pytest.mark.asyncio
async def test_query_lowercase_matches_uppercase_surname(app_client) -> None:
    """ILIKE: ``q=zhit`` должен матчить ``Zhitnitzky`` (case-insensitive)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "zhit"})
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_query_finds_given_name(app_client) -> None:
    """``q=Mary`` → ищет в given_name."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "Mary"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Mary Smith"


@pytest.mark.asyncio
async def test_query_matches_concatenated_full_name(app_client) -> None:
    """``q=Vladimir Zhit`` (with space) находит через concat(given, ' ', surname)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Vladimir Zhit"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_query_returns_multiple_matches(app_client) -> None:
    """``q=Smith`` → 2 человека (John + Mary)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "Smith"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    names = {item["primary_name"] for item in body["items"]}
    assert names == {"John Smith", "Mary Smith"}


@pytest.mark.asyncio
async def test_query_no_match_returns_empty(app_client) -> None:
    """``q=NonExistent`` → пустой items + total=0 (но 200, не 404)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "NonExistent"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_birth_year_range_filter(app_client) -> None:
    """``birth_year_min=1850&birth_year_max=1900`` → только Mary 1850."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"birth_year_min": 1850, "birth_year_max": 1900},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Mary Smith"


@pytest.mark.asyncio
async def test_birth_year_min_only(app_client) -> None:
    """``birth_year_min=1900`` → John 1920 + Vladimir 1945 (Mary 1850 отсечена,
    Anna без BIRT отсечена)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"birth_year_min": 1900},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    names = {item["primary_name"] for item in body["items"]}
    assert names == {"John Smith", "Vladimir Zhitnitzky"}


@pytest.mark.asyncio
async def test_birth_year_max_only(app_client) -> None:
    """``birth_year_max=1900`` → только Mary 1850 (Anna без BIRT отсечена)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"birth_year_max": 1900},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Mary Smith"


@pytest.mark.asyncio
async def test_query_and_birth_year_combined_anded(app_client) -> None:
    """``q=Smith&birth_year_min=1900`` → AND-фильтр: только John Smith 1920."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Smith", "birth_year_min": 1900},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "John Smith"


@pytest.mark.asyncio
async def test_unknown_tree_returns_404(app_client) -> None:
    """Несуществующее ``tree_id`` → 404 (а не пустой результат)."""
    response = await app_client.get("/trees/00000000-0000-0000-0000-000000000000/persons/search")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_sql_injection_attempt_does_not_break_query(app_client) -> None:
    """``q="' OR 1=1--"`` → не падает, не возвращает лишнего.

    SQLAlchemy parameterizes binds, поэтому payload попадает внутрь LIKE
    как литерал (никто не матчится). Тест проверяет, что endpoint не
    падает на 500 и возвращает корректный пустой результат.
    """
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "' OR 1=1--"},
    )
    assert response.status_code == 200
    body = response.json()
    # Ничьё имя не содержит литерала "' OR 1=1--", поэтому total = 0.
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_ilike_metacharacters_are_escaped(app_client) -> None:
    """``q=%`` не должен работать как wildcard и возвращать всех персон."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "%"})
    assert response.status_code == 200
    # Никто не имеет литеральный "%" в имени, поэтому 0 результатов.
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_underscore_metacharacter_is_escaped(app_client) -> None:
    """``q=_`` не должен работать как single-char wildcard."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search", params={"q": "_"})
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_pagination_limit_and_offset(app_client) -> None:
    """``limit=2&offset=2`` → возвращает 2 элемента, total остаётся 4."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"limit": 2, "offset": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 2


@pytest.mark.asyncio
async def test_limit_max_200_enforced(app_client) -> None:
    """``limit=999`` → 422 (превышает Query(le=200))."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"limit": 999},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_negative_offset_rejected(app_client) -> None:
    """``offset=-1`` → 422."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"offset": -1},
    )
    assert response.status_code == 422
