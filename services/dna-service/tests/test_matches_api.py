"""Тесты Phase 6.3 endpoints (ADR-0033).

Покрытие:
    list:
        - сортировка по убыванию total_cm + tie-break по created_at;
        - фильтр min_cm;
        - фильтр predicted (case-insensitive substring);
        - пагинация limit/offset;
        - kit not found / soft-deleted → 404.
    detail:
        - возвращает chromosome painting сегменты из provenance jsonb;
        - возвращает shared_ancestor_hint, если есть;
        - 404 на отсутствующий / soft-deleted match;
        - 404 если parent kit revoke'нут.
    link:
        - same-tree person → 200, matched_person_id выставлен;
        - cross-tree person → 409;
        - tree_id в payload != match.tree_id → 409;
        - person not found → 404;
        - soft-deleted person → 404.
    unlink:
        - очищает matched_person_id;
        - идемпотентен на already-null match.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from shared_models.orm import DnaKit, DnaMatch, Person, Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _seed_kit_with_matches(
    postgres_dsn: str,
    *,
    matches: list[dict[str, Any]],
    extra_person_in_other_tree: bool = False,
) -> dict[str, Any]:
    """Создаёт User + Tree + DnaKit + N matches; опционально — person в другом дереве.

    Возвращает: {user_id, tree_id, kit_id, person_id (same tree),
    other_person_id (если ``extra_person_in_other_tree``), match_ids}.
    """
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        user = User(
            email=f"matches-test-{suffix}@example.com",
            external_auth_id=f"auth0|matches-test-{suffix}",
            display_name="Matches Test User",
        )
        session.add(user)
        await session.flush()

        tree = Tree(owner_user_id=user.id, name=f"Matches Tree {suffix}")
        session.add(tree)
        await session.flush()

        kit = DnaKit(
            tree_id=tree.id,
            owner_user_id=user.id,
            source_platform="ancestry",
            external_kit_id=f"kit-{suffix}",
            display_name=f"Test Kit {suffix}",
        )
        session.add(kit)
        await session.flush()

        person = Person(tree_id=tree.id)
        session.add(person)
        await session.flush()

        match_ids: list[uuid.UUID] = []
        for entry in matches:
            match = DnaMatch(
                tree_id=tree.id,
                kit_id=kit.id,
                external_match_id=entry.get("external_match_id"),
                display_name=entry.get("display_name"),
                total_cm=entry.get("total_cm"),
                largest_segment_cm=entry.get("largest_segment_cm"),
                segment_count=entry.get("segment_count"),
                predicted_relationship=entry.get("predicted_relationship"),
                confidence=entry.get("confidence"),
                shared_match_count=entry.get("shared_match_count"),
                provenance=entry.get("provenance", {}),
            )
            session.add(match)
            await session.flush()
            match_ids.append(match.id)

        result: dict[str, Any] = {
            "user_id": user.id,
            "tree_id": tree.id,
            "kit_id": kit.id,
            "person_id": person.id,
            "match_ids": match_ids,
        }

        if extra_person_in_other_tree:
            other_tree = Tree(owner_user_id=user.id, name=f"Other Tree {suffix}")
            session.add(other_tree)
            await session.flush()
            other_person = Person(tree_id=other_tree.id)
            session.add(other_person)
            await session.flush()
            result["other_tree_id"] = other_tree.id
            result["other_person_id"] = other_person.id

    await engine.dispose()
    return result


async def _soft_delete_kit(postgres_dsn: str, kit_id: uuid.UUID) -> None:
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        kit = await session.get(DnaKit, kit_id)
        assert kit is not None
        kit.deleted_at = dt.datetime.now(dt.UTC)
    await engine.dispose()


async def _soft_delete_person(postgres_dsn: str, person_id: uuid.UUID) -> None:
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        person = await session.get(Person, person_id)
        assert person is not None
        person.deleted_at = dt.datetime.now(dt.UTC)
    await engine.dispose()


async def _read_match_link(postgres_dsn: str, match_id: uuid.UUID) -> uuid.UUID | None:
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        match = await session.get(DnaMatch, match_id)
        assert match is not None
        result = match.matched_person_id
    await engine.dispose()
    return result


# ---- list -----------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_list_matches_sorts_by_total_cm_desc(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[
            {"display_name": "Low cM", "total_cm": 25.0},
            {"display_name": "High cM", "total_cm": 800.0},
            {"display_name": "Mid cM", "total_cm": 200.0},
        ],
    )

    resp = await app_client.get(f"/dna-kits/{seed['kit_id']}/matches")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    names = [item["display_name"] for item in body["items"]]
    assert names == ["High cM", "Mid cM", "Low cM"]


@pytest.mark.db
@pytest.mark.integration
async def test_list_matches_filters_by_min_cm(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[
            {"display_name": "Big", "total_cm": 800.0},
            {"display_name": "Small", "total_cm": 5.0},
            {"display_name": "Null cM", "total_cm": None},
        ],
    )

    resp = await app_client.get(
        f"/dna-kits/{seed['kit_id']}/matches",
        params={"min_cm": 20},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["display_name"] == "Big"
    assert body["min_cm"] == 20.0


@pytest.mark.db
@pytest.mark.integration
async def test_list_matches_filters_predicted_case_insensitive(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[
            {"display_name": "A", "total_cm": 100.0, "predicted_relationship": "1st cousin"},
            {
                "display_name": "B",
                "total_cm": 50.0,
                "predicted_relationship": "3rd cousin once removed",
            },
            {"display_name": "C", "total_cm": 30.0, "predicted_relationship": "Parent / Child"},
        ],
    )

    resp = await app_client.get(
        f"/dna-kits/{seed['kit_id']}/matches",
        params={"predicted": "COUSIN"},
    )
    body = resp.json()
    assert body["total"] == 2
    names = sorted(item["display_name"] for item in body["items"])
    assert names == ["A", "B"]


@pytest.mark.db
@pytest.mark.integration
async def test_list_matches_paginates(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": f"M-{i:02d}", "total_cm": 100.0 - i} for i in range(5)],
    )
    resp = await app_client.get(
        f"/dna-kits/{seed['kit_id']}/matches",
        params={"limit": 2, "offset": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [item["display_name"] for item in body["items"]] == ["M-02", "M-03"]


@pytest.mark.db
@pytest.mark.integration
async def test_list_matches_kit_not_found(app_client) -> None:
    resp = await app_client.get(
        f"/dna-kits/{uuid.uuid4()}/matches",
    )
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_list_matches_soft_deleted_kit_returns_404(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "X", "total_cm": 100.0}],
    )
    await _soft_delete_kit(postgres_dsn, seed["kit_id"])
    resp = await app_client.get(f"/dna-kits/{seed['kit_id']}/matches")
    assert resp.status_code == 404


# ---- detail ---------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_detail_returns_segments_from_provenance(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[
            {
                "display_name": "Cousin",
                "total_cm": 100.0,
                "largest_segment_cm": 30.0,
                "segment_count": 2,
                "provenance": {
                    "segments": [
                        {
                            "chromosome": 1,
                            "start_bp": 1_000_000,
                            "end_bp": 5_000_000,
                            "cm": 7.5,
                            "num_snps": 1234,
                        },
                        {
                            "chromosome": 7,
                            "start_bp": 50_000_000,
                            "end_bp": 80_000_000,
                            "cm": 22.5,
                            "num_snps": 5000,
                        },
                    ],
                    "shared_ancestor_hint": {
                        "label": "Иванов И.И. (1850)",
                        "source": "user_note",
                    },
                },
            },
        ],
    )
    match_id = seed["match_ids"][0]

    resp = await app_client.get(f"/dna-matches/{match_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(match_id)
    assert body["segment_count"] == 2
    assert len(body["segments"]) == 2
    assert body["segments"][0]["chromosome"] == 1
    assert body["segments"][0]["cm"] == pytest.approx(7.5)
    assert body["shared_ancestor_hint"]["label"] == "Иванов И.И. (1850)"


@pytest.mark.db
@pytest.mark.integration
async def test_detail_returns_empty_segments_on_legacy_provenance(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[
            {
                "display_name": "Legacy",
                "total_cm": 50.0,
                "provenance": {"segments": "not-a-list"},  # legacy garbage shape
            },
        ],
    )
    resp = await app_client.get(f"/dna-matches/{seed['match_ids'][0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["segments"] == []
    assert body["shared_ancestor_hint"] is None


@pytest.mark.db
@pytest.mark.integration
async def test_detail_match_not_found(app_client) -> None:
    resp = await app_client.get(f"/dna-matches/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_detail_match_under_revoked_kit_returns_404(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "X", "total_cm": 100.0}],
    )
    await _soft_delete_kit(postgres_dsn, seed["kit_id"])
    resp = await app_client.get(f"/dna-matches/{seed['match_ids'][0]}")
    assert resp.status_code == 404


# ---- link / unlink --------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_link_match_to_person_same_tree_succeeds(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
    )
    match_id = seed["match_ids"][0]

    resp = await app_client.patch(
        f"/dna-matches/{match_id}/link",
        json={"tree_id": str(seed["tree_id"]), "person_id": str(seed["person_id"])},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched_person_id"] == str(seed["person_id"])

    persisted = await _read_match_link(postgres_dsn, match_id)
    assert persisted == seed["person_id"]


@pytest.mark.db
@pytest.mark.integration
async def test_link_match_cross_tree_refused(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
        extra_person_in_other_tree=True,
    )
    match_id = seed["match_ids"][0]

    resp = await app_client.patch(
        f"/dna-matches/{match_id}/link",
        # tree_id матча правильный, но person_id принадлежит другому дереву.
        json={"tree_id": str(seed["tree_id"]), "person_id": str(seed["other_person_id"])},
    )
    assert resp.status_code == 409
    assert "tree" in resp.json()["detail"].lower()
    persisted = await _read_match_link(postgres_dsn, match_id)
    assert persisted is None


@pytest.mark.db
@pytest.mark.integration
async def test_link_payload_tree_must_match_match_tree(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
    )
    match_id = seed["match_ids"][0]

    resp = await app_client.patch(
        f"/dna-matches/{match_id}/link",
        json={"tree_id": str(uuid.uuid4()), "person_id": str(seed["person_id"])},
    )
    assert resp.status_code == 409
    assert "tree" in resp.json()["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_link_person_not_found(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
    )
    resp = await app_client.patch(
        f"/dna-matches/{seed['match_ids'][0]}/link",
        json={"tree_id": str(seed["tree_id"]), "person_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert "person" in resp.json()["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_link_soft_deleted_person_returns_404(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
    )
    await _soft_delete_person(postgres_dsn, seed["person_id"])
    resp = await app_client.patch(
        f"/dna-matches/{seed['match_ids'][0]}/link",
        json={"tree_id": str(seed["tree_id"]), "person_id": str(seed["person_id"])},
    )
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_unlink_match_clears_person_id(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
    )
    match_id = seed["match_ids"][0]
    # link first
    await app_client.patch(
        f"/dna-matches/{match_id}/link",
        json={"tree_id": str(seed["tree_id"]), "person_id": str(seed["person_id"])},
    )
    resp = await app_client.delete(f"/dna-matches/{match_id}/link")
    assert resp.status_code == 200
    assert resp.json()["matched_person_id"] is None
    persisted = await _read_match_link(postgres_dsn, match_id)
    assert persisted is None


@pytest.mark.db
@pytest.mark.integration
async def test_unlink_match_is_idempotent(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(
        postgres_dsn,
        matches=[{"display_name": "Cousin", "total_cm": 100.0}],
    )
    match_id = seed["match_ids"][0]
    first = await app_client.delete(f"/dna-matches/{match_id}/link")
    second = await app_client.delete(f"/dna-matches/{match_id}/link")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["matched_person_id"] is None


@pytest.mark.db
@pytest.mark.integration
async def test_link_match_not_found(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(postgres_dsn, matches=[])
    resp = await app_client.patch(
        f"/dna-matches/{uuid.uuid4()}/link",
        json={"tree_id": str(seed["tree_id"]), "person_id": str(seed["person_id"])},
    )
    assert resp.status_code == 404


# ---- list kits ------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_list_kits_returns_user_kits(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(postgres_dsn, matches=[])
    resp = await app_client.get("/dna-kits", params={"owner_user_id": str(seed["user_id"])})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(seed["kit_id"])


@pytest.mark.db
@pytest.mark.integration
async def test_list_kits_filters_soft_deleted(app_client, postgres_dsn) -> None:
    seed = await _seed_kit_with_matches(postgres_dsn, matches=[])
    await _soft_delete_kit(postgres_dsn, seed["kit_id"])
    resp = await app_client.get("/dna-kits", params={"owner_user_id": str(seed["user_id"])})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
