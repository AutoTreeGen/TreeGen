"""Tree change proposals — CRUD endpoint tests (Phase 15.4a).

Каждый тест создаёт изолированного User+Tree через прямой ORM-insert
(паттерн test_digest_summary.py): parser-service shared session
showed how same fake-user accumulating data across tests breaks
count-based assertions.

Проверяем:

* POST happy path — open proposal с пустым evidence_required (default).
* Protected mode + policy.require_evidence_for: ['parent_child']
  auto-populates evidence_required по relationship-changes из diff.
* GET list filters by status.
* GET by id returns full payload.
* 404 на чужое дерево (privacy-by-obscurity, не 403).
* 404 на не-существующий proposal.
"""

from __future__ import annotations

import os
import uuid

import pytest
from shared_models.orm import Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


def _engine():
    sync_url = os.environ["DATABASE_URL"]
    async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return create_async_engine(async_url)


async def _create_user_and_tree(
    *,
    protected: bool = False,
    policy: dict | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """ORM-insert свежих User+Tree, вернуть ``(user_id, tree_id)``."""
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            unique = uuid.uuid4().hex[:12]
            user = User(
                email=f"proposal-{unique}@test.local",
                external_auth_id=f"local:proposal-{unique}",
                clerk_user_id=None,
                display_name="Proposal Test",
                locale="en",
            )
            session.add(user)
            await session.flush()

            tree = Tree(
                owner_user_id=user.id,
                name=f"proposal-test-tree-{unique}",
                protected=protected,
                protection_policy=policy or {},
            )
            session.add(tree)
            await session.flush()
            await session.commit()
            return user.id, tree.id
    finally:
        await engine.dispose()


def _auth(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _simple_diff() -> dict:
    return {
        "creates": [
            {
                "entity_type": "person",
                "id": "person-1",
                "fields": {"given_name": "Anna", "surname": "Petrova"},
            },
        ],
        "updates": [],
        "deletes": [],
    }


def _diff_with_relationship() -> dict:
    return {
        "creates": [
            {
                "entity_type": "relationship",
                "id": "rel-1",
                "kind": "parent_child",
                "relationship_id": "rel-1",
                "parent_id": "p-1",
                "child_id": "p-2",
            },
            {
                "entity_type": "person",
                "id": "p-3",
                "fields": {"given_name": "Boris"},
            },
        ],
        "updates": [],
        "deletes": [],
    }


@pytest.mark.asyncio
async def test_create_proposal_happy_path(app_client) -> None:
    """POST → 201 c open proposal без evidence (unprotected tree)."""
    user_id, tree_id = await _create_user_and_tree(protected=False)
    response = await app_client.post(
        f"/trees/{tree_id}/proposals",
        json={"title": "Add Anna", "summary": "She's my GG-grandmother", "diff": _simple_diff()},
        headers=_auth(user_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Add Anna"
    assert body["status"] == "open"
    assert body["author_user_id"] == str(user_id)
    assert body["evidence_required"] == []
    assert body["merged_at"] is None
    assert body["merge_commit_id"] is None


@pytest.mark.asyncio
async def test_create_proposal_protected_auto_populates_evidence(app_client) -> None:
    """В protected дереве с policy ['parent_child'] — auto-fill evidence_required."""
    user_id, tree_id = await _create_user_and_tree(
        protected=True,
        policy={"require_evidence_for": ["parent_child"], "min_reviewers": 1},
    )
    response = await app_client.post(
        f"/trees/{tree_id}/proposals",
        json={"title": "Add parent_child", "summary": None, "diff": _diff_with_relationship()},
        headers=_auth(user_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    requirements = body["evidence_required"]
    assert len(requirements) == 1
    assert requirements[0] == {"relationship_id": "rel-1", "kind": "parent_child"}


@pytest.mark.asyncio
async def test_create_proposal_protected_without_policy_no_evidence(app_client) -> None:
    """Protected без require_evidence_for → evidence_required пуст."""
    user_id, tree_id = await _create_user_and_tree(protected=True, policy={})
    response = await app_client.post(
        f"/trees/{tree_id}/proposals",
        json={"title": "x", "summary": None, "diff": _diff_with_relationship()},
        headers=_auth(user_id),
    )
    assert response.status_code == 201
    assert response.json()["evidence_required"] == []


@pytest.mark.asyncio
async def test_create_proposal_validates_diff_shape(app_client) -> None:
    """Bad diff shape → 422."""
    user_id, tree_id = await _create_user_and_tree()
    response = await app_client.post(
        f"/trees/{tree_id}/proposals",
        json={"title": "x", "summary": None, "diff": "not-a-dict"},
        headers=_auth(user_id),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_proposals_filters_by_status(app_client) -> None:
    user_id, tree_id = await _create_user_and_tree()
    # Создадим два open
    for i in range(2):
        await app_client.post(
            f"/trees/{tree_id}/proposals",
            json={"title": f"x{i}", "summary": None, "diff": _simple_diff()},
            headers=_auth(user_id),
        )
    response = await app_client.get(
        f"/trees/{tree_id}/proposals",
        headers=_auth(user_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    # filter=merged → 0
    response = await app_client.get(
        f"/trees/{tree_id}/proposals?status=merged",
        headers=_auth(user_id),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_proposal_by_id_returns_full_payload(app_client) -> None:
    user_id, tree_id = await _create_user_and_tree()
    created = await app_client.post(
        f"/trees/{tree_id}/proposals",
        json={"title": "y", "summary": "details", "diff": _simple_diff()},
        headers=_auth(user_id),
    )
    proposal_id = created.json()["id"]
    response = await app_client.get(
        f"/proposals/{proposal_id}",
        headers=_auth(user_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == proposal_id
    assert body["summary"] == "details"
    assert "creates" in body["diff"]


@pytest.mark.asyncio
async def test_create_proposal_rejects_unknown_tree(app_client) -> None:
    """Tree не существует → 404 (даже с валидной auth)."""
    user_id, _ = await _create_user_and_tree()
    response = await app_client.post(
        f"/trees/{uuid.uuid4()}/proposals",
        json={"title": "x", "summary": None, "diff": _simple_diff()},
        headers=_auth(user_id),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_other_user_cannot_access_tree(app_client) -> None:
    """User-A создаёт дерево, user-B пытается POST proposal → 404."""
    _, tree_id = await _create_user_and_tree()
    intruder_id, _ = await _create_user_and_tree()
    response = await app_client.post(
        f"/trees/{tree_id}/proposals",
        json={"title": "x", "summary": None, "diff": _simple_diff()},
        headers=_auth(intruder_id),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_proposal_unknown_id_returns_404(app_client) -> None:
    user_id, _ = await _create_user_and_tree()
    response = await app_client.get(
        f"/proposals/{uuid.uuid4()}",
        headers=_auth(user_id),
    )
    assert response.status_code == 404
