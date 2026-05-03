"""Shared bundle-test fixtures (Phase 24.4).

Сидит ``Tree + Owner + 4 Person + 4 Name + 2 Family + FamilyChild`` — даёт
3 валидных pair'а для bundle-tests:

* (parent_id, child_id_a) — parent_child via Family A
* (parent_id, child_id_b) — parent_child via Family A
* (child_id_a, child_id_b) — sibling via Family A

Plus ``broken_pair`` factory — пара с несуществующим person_b (для
individual-failure тестов).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from shared_models.orm import (
    Family,
    FamilyChild,
    Name,
    Person,
    Tree,
    TreeMembership,
    User,
)
from shared_models.storage import InMemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test async session factory bound к testcontainers postgres."""
    engine = create_async_engine(postgres_dsn)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield sf
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_tree(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, uuid.UUID]:
    """Семейная структура для bundle-tests.

    Family A: parent — wife = parent_id, children = [child_a_id, child_b_id].
    """
    user_id = uuid.uuid4()
    tree_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    child_a_id = uuid.uuid4()
    child_b_id = uuid.uuid4()

    async with session_factory() as session:
        session.add(
            User(
                id=user_id,
                external_auth_id=f"user_{user_id.hex[:12]}",
                clerk_user_id=f"user_{user_id.hex[:12]}",
                email=f"{user_id.hex[:8]}@test.local",
                display_name="Test Owner",
            )
        )
        session.add(Tree(id=tree_id, name="Bundle fixture tree", owner_user_id=user_id))
        await session.flush()

        session.add(
            TreeMembership(
                tree_id=tree_id,
                user_id=user_id,
                role="owner",
                invited_by=user_id,
            )
        )

        for pid, given, surname, sex in (
            (parent_id, "Alice", "Doe", "F"),
            (child_a_id, "Bob", "Doe", "M"),
            (child_b_id, "Cara", "Doe", "F"),
        ):
            session.add(Person(id=pid, tree_id=tree_id, sex=sex))
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
        session.add(FamilyChild(family_id=family_id, child_person_id=child_a_id))
        session.add(FamilyChild(family_id=family_id, child_person_id=child_b_id))
        await session.commit()

    return {
        "user_id": user_id,
        "tree_id": tree_id,
        "parent_id": parent_id,
        "child_a_id": child_a_id,
        "child_b_id": child_b_id,
    }


@pytest.fixture
def in_memory_storage() -> InMemoryStorage:
    """Per-test in-memory ObjectStorage (avoid env-config of MinIO/GCS)."""
    return InMemoryStorage()


@pytest.fixture
def now_factory_2026_05_02() -> Any:
    """Frozen-time helper for ttl tests."""
    fixed = dt.datetime(2026, 5, 2, 12, 0, 0, tzinfo=dt.UTC)

    def _now() -> dt.datetime:
        return fixed

    return _now
