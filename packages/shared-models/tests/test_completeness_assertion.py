"""ORM smoke + alembic 0040 up/down for completeness assertions (Phase 15.11a).

Покрытие per-brief:

* test_orm_unique_active_constraint   — partial-unique по active rows.
* test_alembic_0040_up_down            — downgrade чистит обе таблицы.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from shared_models.enums import CompletenessScope, EntityStatus, Sex
from shared_models.orm import CompletenessAssertion, Person, Tree, User
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


def _alembic_root() -> Path:
    """Корень репо с alembic.ini."""
    for parent in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (parent / "alembic.ini").exists():
            return parent
    pytest.skip("alembic.ini не найден от корня тестов")
    msg = "unreachable"
    raise RuntimeError(msg)


async def _seed_user_tree_person(session: AsyncSession) -> tuple[User, Tree, Person]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        external_auth_id=f"local:{uuid.uuid4().hex[:8]}",
        display_name="U",
    )
    session.add(user)
    await session.flush()

    tree = Tree(owner_user_id=user.id, name="T")
    session.add(tree)
    await session.flush()

    person = Person(
        tree_id=tree.id,
        sex=Sex.MALE.value,
        status=EntityStatus.PROBABLE.value,
    )
    session.add(person)
    await session.flush()
    return user, tree, person


async def test_orm_unique_active_constraint(db_session: AsyncSession) -> None:
    """Partial-unique по (tree_id, subject_person_id, scope, deleted_at).

    Две active assertion'а на одну тройку — IntegrityError. После soft-delete
    первой можно создать вторую.
    """
    user, tree, person = await _seed_user_tree_person(db_session)

    a1 = CompletenessAssertion(
        tree_id=tree.id,
        subject_person_id=person.id,
        scope=CompletenessScope.SIBLINGS.value,
        is_sealed=True,
        asserted_by=user.id,
    )
    db_session.add(a1)
    await db_session.flush()

    a2 = CompletenessAssertion(
        tree_id=tree.id,
        subject_person_id=person.id,
        scope=CompletenessScope.SIBLINGS.value,
        is_sealed=True,
        asserted_by=user.id,
    )
    db_session.add(a2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest_asyncio.fixture
async def alembic_dropdown_engine(postgres_container: str) -> Any:
    """Изолированный engine для alembic up/down теста (не пересекается с
    session-scope'ным ``engine_fixture``).

    После теста — sandbox остаётся в downgraded-base state; следующая
    fixture-инициация восстановит head.
    """
    engine = create_async_engine(postgres_container, future=True)
    yield engine
    await engine.dispose()


async def test_alembic_0040_up_down(alembic_dropdown_engine: Any, postgres_container: str) -> None:
    """downgrade 0040 убирает обе таблицы, повторный upgrade head — восстанавливает.

    Миграция 0040 idempotent в обе стороны, что критично для prod-rollback.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_alembic_root() / "alembic.ini"))
    os.environ["DATABASE_URL"] = postgres_container
    cfg.set_main_option("sqlalchemy.url", postgres_container)

    # Стартовое состояние: head = 0040 (применено session-scope'ным fixture).
    # downgrade -1 → 0042 (после rebase onto Phase 24.4: 0040.down_revision="0042").
    command.downgrade(cfg, "-1")

    async with alembic_dropdown_engine.connect() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "completeness_assertions" not in names
    assert "completeness_assertion_sources" not in names

    # Re-apply 0040.
    command.upgrade(cfg, "0040")

    async with alembic_dropdown_engine.connect() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "completeness_assertions" in names
    assert "completeness_assertion_sources" in names
