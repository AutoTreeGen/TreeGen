"""Integration-тесты Phase 16.3 / ADR-0072 endpoints.

Покрытие:

* POST /dna/match-list/import — happy path, idempotent re-import,
  preserves matched_person_id between imports, 404 on missing kit.
* GET /dna/matches — фильтры kit_id, platform, min_cm, max_cm.
* DELETE /dna/matches — bulk delete by kit_id (+ optional platform).

Один testcontainers-postgres + один FastAPI client per session
(см. conftest.py). Каждый тест seedит своё дерево и kit чтобы не
зависеть от порядка выполнения.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models.orm import DnaKit, Person, Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


_ANCESTRY_FIVE_ROW_CSV = (
    "Match GUID,Name,Total cM,Longest cM,Predicted Relationship,Shared Matches,Notes\n"
    "abc-123,Alice Smith,3450,3450.0,Mother,0,\n"
    "def-456,Bob Cohen,420.5,52.1,2nd Cousin,3,\n"
    "ghi-789,Carol Levin,180.2,28.7,3rd Cousin,7,\n"
    "jkl-012,David Klein,72.0,18.5,4th Cousin,2,\n"
    "mno-345,Eve Goldberg,15.0,15.0,Distant Cousin,0,\n"
)


# Phase 16.3 anti-drift: Naum-Katz-style fixture для будущей CSV
# с паспортным mathч'ом — паспорт не относится к match-list, fixture
# здесь — generic AJ-style cluster (owner_dna_cluster_map.md).


@pytest_asyncio.fixture
async def seeded_kit(postgres_dsn: str) -> dict[str, uuid.UUID]:
    """Seed user + tree + dna_kit; вернуть ids."""
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        user = User(
            email=f"matchlist-{suffix}@example.com",
            external_auth_id=f"auth0|matchlist-{suffix}",
            display_name="Match List User",
        )
        session.add(user)
        await session.flush()

        tree = Tree(owner_user_id=user.id, name=f"Match List Tree {suffix}")
        session.add(tree)
        await session.flush()

        kit = DnaKit(
            tree_id=tree.id,
            owner_user_id=user.id,
            source_platform="ancestry",
            external_kit_id=f"kit-{suffix}",
            display_name="Owner kit",
        )
        session.add(kit)
        await session.flush()

        person = Person(tree_id=tree.id)
        session.add(person)
        await session.flush()

        ids = {
            "user_id": user.id,
            "tree_id": tree.id,
            "kit_id": kit.id,
            "person_id": person.id,
        }
    await engine.dispose()
    return ids


def _import_files(content: str) -> dict[str, Any]:
    """multipart-payload helper для httpx."""
    return {"file": ("matches.csv", content.encode("utf-8"), "text/csv")}


async def test_import_match_list_happy_path(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
) -> None:
    """5 строк → 5 imported, 0 updated, 0 skipped."""
    response = await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 5
    assert body["updated"] == 0
    assert body["skipped"] == 0
    assert body["platform"] == "ancestry"


async def test_import_match_list_idempotent_reimport(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
) -> None:
    """Повторный импорт того же CSV → 0 imported, 5 updated."""
    payload = {"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"}
    first = await app_client.post(
        "/dna/match-list/import",
        data=payload,
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )
    assert first.status_code == 200

    second = await app_client.post(
        "/dna/match-list/import",
        data=payload,
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["imported"] == 0
    assert body["updated"] == 5


async def test_import_match_list_404_on_missing_kit(app_client: Any) -> None:
    response = await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(uuid.uuid4()), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )
    assert response.status_code == 404


async def test_import_match_list_400_on_empty_payload(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
) -> None:
    response = await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert response.status_code == 400


async def test_list_matches_filters_by_min_cm(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
) -> None:
    """min_cm=100 → отфильтрует Eve (15) и David (72), оставит 3."""
    await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )

    response = await app_client.get(
        "/dna/matches",
        params={"kit_id": str(seeded_kit["kit_id"]), "min_cm": 100},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3  # Alice, Bob, Carol


async def test_list_matches_filters_by_platform(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
) -> None:
    """platform=myheritage → 0, потому что мы импортнули ancestry."""
    await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )

    ancestry_resp = await app_client.get(
        "/dna/matches",
        params={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
    )
    assert ancestry_resp.status_code == 200
    assert len(ancestry_resp.json()["items"]) == 5

    other_resp = await app_client.get(
        "/dna/matches",
        params={"kit_id": str(seeded_kit["kit_id"]), "platform": "myheritage"},
    )
    assert len(other_resp.json()["items"]) == 0


async def test_bulk_delete_matches(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
) -> None:
    """DELETE /dna/matches?kit_id=... убирает все, готовит к re-import."""
    await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )

    delete_resp = await app_client.delete(
        "/dna/matches",
        params={"kit_id": str(seeded_kit["kit_id"])},
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] == 5

    list_resp = await app_client.get(
        "/dna/matches",
        params={"kit_id": str(seeded_kit["kit_id"])},
    )
    assert len(list_resp.json()["items"]) == 0


async def test_bulk_delete_404_on_missing_kit(app_client: Any) -> None:
    response = await app_client.delete(
        "/dna/matches",
        params={"kit_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


async def test_import_preserves_matched_person_id_on_reimport(
    app_client: Any,
    seeded_kit: dict[str, uuid.UUID],
    postgres_dsn: str,
) -> None:
    """Re-import не должен затирать user-judgement (matched_person_id)."""
    from shared_models.orm import DnaMatch
    from sqlalchemy import select

    # Первый импорт.
    await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )

    # Привязать одного match'а к Person вручную.
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        match = (
            await session.execute(
                select(DnaMatch).where(DnaMatch.kit_id == seeded_kit["kit_id"]).limit(1)
            )
        ).scalar_one()
        match.matched_person_id = seeded_kit["person_id"]
        match_id = match.id

    # Re-import.
    await app_client.post(
        "/dna/match-list/import",
        data={"kit_id": str(seeded_kit["kit_id"]), "platform": "ancestry"},
        files=_import_files(_ANCESTRY_FIVE_ROW_CSV),
    )

    async with factory() as session:
        refreshed = await session.get(DnaMatch, match_id)
        assert refreshed is not None
        # Anti-drift: matched_person_id не затёрт re-import'ом.
        assert refreshed.matched_person_id == seeded_kit["person_id"]
    await engine.dispose()
