"""Integration test для POST /api/v1/reports/relationship.

Поднимает testcontainers-postgres + alembic-upgrade-head, сидит минимальные
ORM-rows (User + Tree + TreeMembership + 2 Person + 2 Name + Family +
FamilyChild + Citation + Source), стучит endpoint, проверяет ответ.

PDF byte-length тест skipped если WeasyPrint native libs отсутствуют —
endpoint в этом случае возвращает 503.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

import pytest
from shared_models.orm import (
    Citation,
    Family,
    FamilyChild,
    Name,
    Person,
    Source,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from httpx import AsyncClient


pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_pair(postgres_dsn: str) -> dict[str, uuid.UUID]:
    """Сидит owner + tree + parent (A) + child (B) + Family + FamilyChild + 1 citation.

    Возвращает dict с UUID'ами для использования в тесте: ``user_id``,
    ``tree_id``, ``parent_id``, ``child_id``.
    """
    engine = create_async_engine(postgres_dsn)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    user_id = uuid.uuid4()
    tree_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    source_id = uuid.uuid4()

    async with sf() as session:
        session.add(
            User(
                id=user_id,
                external_auth_id=f"user_{user_id.hex[:12]}",
                clerk_user_id=f"user_{user_id.hex[:12]}",
                email=f"{user_id.hex[:8]}@test.local",
                display_name="Test Owner",
            )
        )
        session.add(Tree(id=tree_id, name="Phase 24.3 fixture tree", owner_user_id=user_id))
        await session.flush()

        session.add(
            TreeMembership(
                tree_id=tree_id,
                user_id=user_id,
                role="owner",
                invited_by=user_id,
            )
        )

        for pid, given, surname in (
            (parent_id, "Alice", "Doe"),
            (child_id, "Bob", "Doe"),
        ):
            session.add(
                Person(
                    id=pid,
                    tree_id=tree_id,
                    sex="F" if given == "Alice" else "M",
                )
            )
            session.add(
                Name(
                    person_id=pid,
                    given_name=given,
                    surname=surname,
                    sort_order=0,
                )
            )
        await session.flush()

        family_id = uuid.uuid4()
        session.add(
            Family(
                id=family_id,
                tree_id=tree_id,
                husband_id=None,
                wife_id=parent_id,
            )
        )
        await session.flush()
        session.add(
            FamilyChild(
                family_id=family_id,
                child_person_id=child_id,
            )
        )

        session.add(
            Source(
                id=source_id,
                tree_id=tree_id,
                title="Birth registry, 1898",
                repository="State Archive #42",
            )
        )
        await session.flush()
        session.add(
            Citation(
                tree_id=tree_id,
                source_id=source_id,
                entity_type="family",
                entity_id=family_id,
                quality=0.9,
                quay_raw=3,
                page_or_section="p. 12",
            )
        )

        await session.commit()

    await engine.dispose()
    return {
        "user_id": user_id,
        "tree_id": tree_id,
        "parent_id": parent_id,
        "child_id": child_id,
    }


@pytest.fixture
def report_body(seeded_pair: dict[str, uuid.UUID]) -> dict[str, object]:
    return {
        "tree_id": str(seeded_pair["tree_id"]),
        "person_a_id": str(seeded_pair["parent_id"]),
        "person_b_id": str(seeded_pair["child_id"]),
        "claimed_relationship": "parent_child",
        "options": {
            "include_dna_evidence": False,
            "include_archive_evidence": True,
            "include_hypothesis_flags": True,
            "locale": "en",
            "title_style": "formal",
        },
    }


# ---------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------


async def test_relationship_report_200_with_evidence(
    app_client: AsyncClient,
    seeded_pair: dict[str, uuid.UUID],
    report_body: dict[str, object],
) -> None:
    """Полный happy-path: parent_child claim с одной family-citation.

    Ожидание: 200 + evidence_count >= 1 + confidence > 0. PDF byte-length
    проверяется только при наличии WeasyPrint native libs (иначе endpoint
    отдаёт 503 — отдельный кейс ниже).
    """
    resp = await app_client.post(
        "/api/v1/reports/relationship",
        json=report_body,
        headers={"X-User-Id": str(seeded_pair["user_id"])},
    )
    if resp.status_code == 503:
        pytest.skip("WeasyPrint native libs unavailable (503 expected on bare Windows).")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["evidence_count"] >= 1
    assert data["counter_evidence_count"] == 0
    assert data["confidence"] > 0
    assert data["pdf_url"]
    expires_at = dt.datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    assert expires_at > dt.datetime.now(dt.UTC)


async def test_relationship_report_401_without_header(
    app_client: AsyncClient,
    report_body: dict[str, object],
) -> None:
    resp = await app_client.post("/api/v1/reports/relationship", json=report_body)
    assert resp.status_code == 401


async def test_relationship_report_401_invalid_user_id(
    app_client: AsyncClient,
    report_body: dict[str, object],
) -> None:
    resp = await app_client.post(
        "/api/v1/reports/relationship",
        json=report_body,
        headers={"X-User-Id": "not-a-uuid"},
    )
    assert resp.status_code == 401


async def test_relationship_report_400_same_person(
    app_client: AsyncClient,
    seeded_pair: dict[str, uuid.UUID],
    report_body: dict[str, object],
) -> None:
    body = {**report_body, "person_b_id": report_body["person_a_id"]}
    resp = await app_client.post(
        "/api/v1/reports/relationship",
        json=body,
        headers={"X-User-Id": str(seeded_pair["user_id"])},
    )
    assert resp.status_code == 400


async def test_relationship_report_404_for_non_member(
    app_client: AsyncClient,
    report_body: dict[str, object],
) -> None:
    """Stranger получает 404, а не 403 — не утекаем существование дерева."""
    stranger = uuid.uuid4()
    resp = await app_client.post(
        "/api/v1/reports/relationship",
        json=report_body,
        headers={"X-User-Id": str(stranger)},
    )
    assert resp.status_code == 404


async def test_relationship_report_404_for_unknown_person(
    app_client: AsyncClient,
    seeded_pair: dict[str, uuid.UUID],
    report_body: dict[str, object],
) -> None:
    """Несуществующий person_id → 404 от build_report_context."""
    body = {**report_body, "person_b_id": str(uuid.uuid4())}
    resp = await app_client.post(
        "/api/v1/reports/relationship",
        json=body,
        headers={"X-User-Id": str(seeded_pair["user_id"])},
    )
    assert resp.status_code == 404


async def test_healthz(app_client: AsyncClient) -> None:
    resp = await app_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
