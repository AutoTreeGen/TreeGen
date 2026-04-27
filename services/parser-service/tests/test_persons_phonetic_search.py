"""Интеграционные тесты ``GET /trees/{id}/persons/search?phonetic=true`` (Phase 4.4.1).

Daitch-Mokotoff bucket overlap (operator ``&&`` в Postgres) находит варианты
spelling: Zhitnitzky / Zhytnicki / Жытницкий / Schitnitzky → один bucket-set.
Substring (phonetic=false) НЕ находит кириллицу для латинского запроса —
доказывает разницу между режимами.

Все тесты используют синтетический GED с разнообразными вариантами
spelling одной фамилии, плюс контрольные имена (Smith, Cohen) для проверки
DM-эквивалентности.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Дерево с вариантами одной фамилии в разных транслитерациях/орфографиях.
# Все они должны давать пересекающиеся DM-buckets с base "Zhitnitzky".
# Контрольная группа: Smith и Cohen / Kohen для DM-эквивалентности
# классических ambivalence-кейсов из DM-таблицы.
# str + .encode("utf-8"), потому что Python b"..." не принимает не-ASCII.
_GED_PHONETIC_FIXTURE = """\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Vladimir /Zhitnitzky/
1 SEX M
0 @I2@ INDI
1 NAME Volodya /Zhytnicki/
1 SEX M
0 @I3@ INDI
1 NAME Володимир /Жытницкий/
1 SEX M
0 @I4@ INDI
1 NAME Hans /Schitnitzky/
1 SEX M
0 @I5@ INDI
1 NAME John /Smith/
1 SEX M
0 @I6@ INDI
1 NAME Ari /Cohen/
1 SEX M
0 @I7@ INDI
1 NAME Mosheh /Kohen/
1 SEX M
0 @I8@ INDI
1 NAME Aron /Cohn/
1 SEX M
0 @I9@ INDI
1 NAME Анна /Кохен/
1 SEX F
0 TRLR
""".encode()


async def _import_fixture(app_client) -> str:
    files = {"file": ("test.ged", _GED_PHONETIC_FIXTURE, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code in (200, 201), created.text
    return created.json()["tree_id"]


@pytest.mark.asyncio
async def test_phonetic_finds_zhitnitzky_variants(app_client) -> None:
    """``q=Zhitnitzky&phonetic=true`` находит латинские, кириллические и немецкие
    транслитерации в один bucket-overlap."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Zhitnitzky", "phonetic": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    surnames = {item["primary_name"] for item in body["items"]}
    # Все 4 варианта Zhitnitzky должны вернуться. Schitnitzky в DM
    # начинается с SH→4 vs ZH→4 → совпадает с Zhitnitzky.
    assert "Vladimir Zhitnitzky" in surnames
    assert "Volodya Zhytnicki" in surnames
    assert "Володимир Жытницкий" in surnames
    assert "Hans Schitnitzky" in surnames
    # Контроль: Smith/Cohen НЕ должны попасть.
    assert "John Smith" not in surnames
    assert all(item["match_type"] == "phonetic" for item in body["items"])


@pytest.mark.asyncio
async def test_substring_does_not_find_cyrillic_for_latin_query(app_client) -> None:
    """``q=Zhit`` без phonetic — substring ILIKE — не найдёт «Жытницкий»."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Zhit"},
    )
    assert response.status_code == 200
    body = response.json()
    surnames = {item["primary_name"] for item in body["items"]}
    # Латинский Zhitnitzky содержит подстроку "Zhit" → матчится.
    assert "Vladimir Zhitnitzky" in surnames
    # Zhytnicki содержит "Zhyt", не "Zhit" — substring не находит,
    # хотя phonetic нашёл бы (DM bucket совпадает).
    assert "Volodya Zhytnicki" not in surnames
    # Кириллические — нет (это и есть гэп, который phonetic закрывает).
    assert "Володимир Жытницкий" not in surnames
    assert all(item["match_type"] == "substring" for item in body["items"])


@pytest.mark.asyncio
async def test_phonetic_finds_cohen_variants(app_client) -> None:
    """``q=Cohen&phonetic=true`` → Kohen, Cohn, Кохен."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Cohen", "phonetic": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    surnames = {item["primary_name"] for item in body["items"]}
    assert "Ari Cohen" in surnames
    assert "Mosheh Kohen" in surnames
    assert "Aron Cohn" in surnames
    assert "Анна Кохен" in surnames


@pytest.mark.asyncio
async def test_phonetic_finds_via_cyrillic_query(app_client) -> None:
    """``q=Жытницкий&phonetic=true`` — кириллический запрос находит латинские варианты."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Жытницкий", "phonetic": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    surnames = {item["primary_name"] for item in body["items"]}
    assert "Vladimir Zhitnitzky" in surnames
    assert "Володимир Жытницкий" in surnames


@pytest.mark.asyncio
async def test_phonetic_with_empty_dm_codes_returns_empty(app_client) -> None:
    """``q=---&phonetic=true`` — non-alphabetic input → 0 DM кодов → пустой items.

    Без fallback в substring (иначе UI получает substring matches при
    включённой Phonetic-галочке, что путает).
    """
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "---", "phonetic": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_phonetic_combined_with_birth_year_filter(app_client) -> None:
    """Phonetic + birth_year_min: AND-фильтр работает (никто не родился, поэтому пусто)."""
    tree_id = await _import_fixture(app_client)
    # Никто в фикстуре не имеет BIRT-event с date_start, поэтому
    # birth_year_min отсекает всех — даже когда phonetic находит совпадения.
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Cohen", "phonetic": "true", "birth_year_min": 1850},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_phonetic_default_false(app_client) -> None:
    """Без ``phonetic`` параметра — substring (back-compat)."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Smith"},
    )
    assert response.status_code == 200
    body = response.json()
    assert all(item["match_type"] == "substring" for item in body["items"])


@pytest.mark.asyncio
async def test_phonetic_false_explicit(app_client) -> None:
    """``phonetic=false`` явно — substring."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Smith", "phonetic": "false"},
    )
    assert response.status_code == 200
    body = response.json()
    assert all(item["match_type"] == "substring" for item in body["items"])


@pytest.mark.asyncio
async def test_match_type_is_none_when_no_query(app_client) -> None:
    """Без ``q`` — list, ``match_type`` = None."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(f"/trees/{tree_id}/persons/search")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["match_type"] is None for item in body["items"])


@pytest.mark.asyncio
async def test_phonetic_unknown_tree_returns_404(app_client) -> None:
    """Phonetic-режим тоже даёт 404 на неизвестное дерево."""
    response = await app_client.get(
        "/trees/00000000-0000-0000-0000-000000000000/persons/search",
        params={"q": "Cohen", "phonetic": "true"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_phonetic_no_matches_returns_empty(app_client) -> None:
    """Phonetic с уникальной фамилией → пустой результат."""
    tree_id = await _import_fixture(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/persons/search",
        params={"q": "Pakistanovich", "phonetic": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    # DM хорошо разделяет ни на что не похожие фамилии.
    assert body["total"] == 0
