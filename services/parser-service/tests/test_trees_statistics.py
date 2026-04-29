"""Тесты для ``GET /trees/{id}/statistics`` (Phase 6.5, ADR-0051).

Покрывают:
* unknown tree → 404;
* импортированный 3-поколенческий GEDCOM: все counts корректны, top_surnames
  агрегирован, oldest_birth_year = MIN(BIRT.date), pedigree_max_depth = 3.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


_GED_3_GENERATIONS = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Alpha /First/
1 SEX M
1 BIRT
2 DATE 1800
0 @I2@ INDI
1 NAME Bravo /First/
1 SEX F
1 BIRT
2 DATE 1805
0 @I3@ INDI
1 NAME Charlie /Second/
1 SEX M
1 BIRT
2 DATE 1802
0 @I4@ INDI
1 NAME Delta /Second/
1 SEX F
1 BIRT
2 DATE 1808
0 @I5@ INDI
1 NAME Echo /First/
1 SEX M
1 BIRT
2 DATE 1830
0 @I6@ INDI
1 NAME Foxtrot /Second/
1 SEX F
1 BIRT
2 DATE 1832
0 @I7@ INDI
1 NAME Golf /First/
1 SEX M
1 BIRT
2 DATE 1860
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I5@
0 @F2@ FAM
1 HUSB @I3@
1 WIFE @I4@
1 CHIL @I6@
0 @F3@ FAM
1 HUSB @I5@
1 WIFE @I6@
1 CHIL @I7@
0 TRLR
"""


@pytest.mark.asyncio
async def test_statistics_returns_404_for_unknown_tree(app_client) -> None:
    """Несуществующий tree_id → 404."""
    response = await app_client.get(f"/trees/{uuid.uuid4()}/statistics")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_statistics_for_imported_tree(app_client) -> None:
    """3-поколенческое дерево: counts корректны, surnames агрегированы, depth=3."""
    files = {"file": ("test.ged", _GED_3_GENERATIONS, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    response = await app_client.get(f"/trees/{tree_id}/statistics")
    assert response.status_code == 200
    body = response.json()

    assert body["tree_id"] == tree_id
    assert body["persons_count"] == 7
    assert body["families_count"] == 3
    # 7 BIRT-events (по одному на персону).
    assert body["events_count"] == 7
    # GEDCOM не содержит SOUR/HYPOTHESIS/DNA — нули.
    assert body["sources_count"] == 0
    assert body["hypotheses_count"] == 0
    assert body["dna_matches_count"] == 0

    # Самый старый BIRT — 1800 у I1.
    assert body["oldest_birth_year"] == 1800

    # Top surnames: First (4 персоны: I1, I2, I5, I7) > Second (3: I3, I4, I6).
    surnames = {row["surname"]: row["person_count"] for row in body["top_surnames"]}
    assert surnames == {"First": 4, "Second": 3}

    # Pedigree depth: I1/I2 → I5 → I7 = три поколения.
    assert body["pedigree_max_depth"] == 3
