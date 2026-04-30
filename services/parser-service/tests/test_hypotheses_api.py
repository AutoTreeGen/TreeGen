"""HTTP тесты hypotheses-router (Phase 7.2 Task 4)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


_GED_DEDUP = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Meir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
0 @I2@ INDI
1 NAME Meir /Zhytnicki/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 TRLR
"""


async def _import_and_get_persons(app_client) -> tuple[uuid.UUID, str, str]:
    """Импортировать GED, вернуть (tree_id, person_i1_id, person_i2_id)."""
    files = {"file": ("test.ged", _GED_DEDUP, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201
    tree_id = created.json()["tree_id"]
    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    i1 = next(p for p in items if p["gedcom_xref"] == "I1")["id"]
    i2 = next(p for p in items if p["gedcom_xref"] == "I2")["id"]
    return uuid.UUID(tree_id), i1, i2


@pytest.mark.asyncio
async def test_post_create_hypothesis_returns_201(app_client) -> None:
    """POST /trees/{id}/hypotheses создаёт row и возвращает full response."""
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["hypothesis_type"] == "same_person"
    # Phase 7.5 (ADR-0057): Bayesian fusion threshold снижен с 0.85 до 0.75.
    assert body["composite_score"] >= 0.75
    assert body["reviewed_status"] == "pending"
    assert body["evidences"], "expected at least one evidence"
    rule_ids = {ev["rule_id"] for ev in body["evidences"]}
    assert "surname_dm_match" in rule_ids


@pytest.mark.asyncio
async def test_post_create_hypothesis_404_on_unknown_subject(app_client) -> None:
    """Несуществующий subject_id → 404."""
    tree_id, i1, _ = await _import_and_get_persons(app_client)
    ghost = uuid.uuid4()
    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": str(ghost),
            "hypothesis_type": "same_person",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_invalid_hypothesis_type_returns_422(app_client) -> None:
    """Literal валидация — неизвестный type → 422."""
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "garbage",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_list_hypotheses_returns_paginated(app_client) -> None:
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    # Создать гипотезу.
    await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    # Прочитать list.
    response = await app_client.get(f"/trees/{tree_id}/hypotheses")
    assert response.status_code == 200
    body = response.json()
    assert body["tree_id"] == str(tree_id)
    assert body["total"] >= 1
    assert body["items"]
    item = body["items"][0]
    assert item["hypothesis_type"] == "same_person"
    # Summary не несёт evidences[] — это full GET /hypotheses/{id}.
    assert "evidences" not in item


@pytest.mark.asyncio
async def test_get_list_filter_by_subject_id(app_client) -> None:
    """?subject_id=<uuid> возвращает только гипотезы с этим subject."""
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    response = await app_client.get(
        f"/trees/{tree_id}/hypotheses",
        params={"subject_id": i1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert i1 in (item["subject_a_id"], item["subject_b_id"])


@pytest.mark.asyncio
async def test_get_list_filter_by_min_confidence(app_client) -> None:
    """Высокий min_confidence отсекает слабые гипотезы."""
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    high = await app_client.get(
        f"/trees/{tree_id}/hypotheses",
        params={"min_confidence": 0.99},
    )
    low = await app_client.get(
        f"/trees/{tree_id}/hypotheses",
        params={"min_confidence": 0.50},
    )
    assert high.status_code == 200
    assert low.status_code == 200
    assert high.json()["total"] <= low.json()["total"]


@pytest.mark.asyncio
async def test_get_single_hypothesis_includes_evidences(app_client) -> None:
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]

    response = await app_client.get(f"/hypotheses/{hyp_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == hyp_id
    assert body["evidences"]
    for ev in body["evidences"]:
        assert ev["rule_id"]
        assert ev["direction"] in ("supports", "contradicts", "neutral")
        assert 0.0 <= ev["weight"] <= 1.0


@pytest.mark.asyncio
async def test_get_single_hypothesis_404(app_client) -> None:
    response = await app_client.get(f"/hypotheses/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_review_marks_confirmed(app_client) -> None:
    """PATCH /hypotheses/{id}/review сохраняет user judgment.

    CLAUDE.md §5: НЕ должно изменять persons / sources / places counts —
    только reviewed_status / reviewed_at / review_note.
    """
    from shared_models.orm import Person
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]

    # Counts persons до review.
    import os

    dsn = os.environ["PARSER_SERVICE_DATABASE_URL"]
    engine = create_async_engine(dsn, future=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
    try:
        async with SessionMaker() as session:
            persons_before = await session.scalar(
                select(func.count(Person.id)).where(Person.tree_id == tree_id)
            )

        # PATCH review.
        response = await app_client.patch(
            f"/hypotheses/{hyp_id}/review",
            json={"status": "confirmed", "note": "это точно один человек"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reviewed_status"] == "confirmed"
        assert body["review_note"] == "это точно один человек"
        assert body["reviewed_at"] is not None

        # Counts persons после — НЕ должно меняться (no auto-merge).
        async with SessionMaker() as session:
            persons_after = await session.scalar(
                select(func.count(Person.id)).where(Person.tree_id == tree_id)
            )
        assert persons_before == persons_after, (
            "PATCH review must NOT trigger auto-merge of domain entities (CLAUDE.md §5)"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_patch_review_rejected(app_client) -> None:
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]

    response = await app_client.patch(
        f"/hypotheses/{hyp_id}/review",
        json={"status": "rejected"},
    )
    assert response.status_code == 200
    assert response.json()["reviewed_status"] == "rejected"


@pytest.mark.asyncio
async def test_patch_review_invalid_status_returns_422(app_client) -> None:
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]

    response = await app_client.patch(
        f"/hypotheses/{hyp_id}/review",
        json={"status": "garbage"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_review_404_on_unknown_id(app_client) -> None:
    response = await app_client.patch(
        f"/hypotheses/{uuid.uuid4()}/review",
        json={"status": "confirmed"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_review_deferred(app_client) -> None:
    """Phase 4.9: ``status='deferred'`` сохраняется как 4-й валидный статус."""
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]

    response = await app_client.patch(
        f"/hypotheses/{hyp_id}/review",
        json={"status": "deferred", "note": "Нужно дождаться ДНК-сегментов"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reviewed_status"] == "deferred"
    assert body["review_note"] == "Нужно дождаться ДНК-сегментов"


@pytest.mark.asyncio
async def test_list_filters_by_deferred_status(app_client) -> None:
    """``GET /trees/{id}/hypotheses?review_status=deferred`` возвращает только deferred."""
    tree_id, i1, i2 = await _import_and_get_persons(app_client)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]
    await app_client.patch(f"/hypotheses/{hyp_id}/review", json={"status": "deferred"})

    listing = await app_client.get(
        f"/trees/{tree_id}/hypotheses",
        params={"review_status": "deferred", "min_confidence": 0.0},
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == hyp_id
    assert body["items"][0]["reviewed_status"] == "deferred"

    # И обратное: pending — пусто.
    pending_listing = await app_client.get(
        f"/trees/{tree_id}/hypotheses",
        params={"review_status": "pending", "min_confidence": 0.0},
    )
    assert pending_listing.json()["total"] == 0
