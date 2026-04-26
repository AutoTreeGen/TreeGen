"""Интеграционные smoke-тесты ORM поверх реального Postgres.

Маркер ``db`` — пропускаются если testcontainers/Docker недоступен.
"""

from __future__ import annotations

import pytest
from shared_models.enums import EntityStatus, Sex
from shared_models.orm import AuditLog, Name, Person, Tree, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.db, pytest.mark.integration]


async def _seed_user_and_tree(session: AsyncSession) -> tuple[User, Tree]:
    """Создать пользователя и дерево для теста."""
    user = User(
        email="ivan@example.com",
        external_auth_id="auth0|test-1",
        display_name="Ivan",
    )
    session.add(user)
    await session.flush()

    tree = Tree(owner_user_id=user.id, name="Test Tree")
    session.add(tree)
    await session.flush()
    return user, tree


async def test_create_person_with_name(db_session: AsyncSession) -> None:
    """Создание персоны с именем + проверка selectin-загрузки names."""
    _, tree = await _seed_user_and_tree(db_session)

    person = Person(
        tree_id=tree.id,
        sex=Sex.MALE.value,
        status=EntityStatus.CONFIRMED.value,
        confidence_score=0.95,
        names=[Name(given_name="Ivan", surname="Ivanov", romanized="ivan ivanov")],
    )
    db_session.add(person)
    await db_session.flush()

    fetched = (await db_session.execute(select(Person).where(Person.id == person.id))).scalar_one()
    assert fetched.sex == Sex.MALE.value
    assert len(fetched.names) == 1
    assert fetched.names[0].surname == "Ivanov"


async def test_audit_log_records_insert(db_session: AsyncSession) -> None:
    """INSERT персоны рождает запись в audit_log с action=insert."""
    _, tree = await _seed_user_and_tree(db_session)
    person = Person(tree_id=tree.id, sex=Sex.FEMALE.value)
    db_session.add(person)
    await db_session.flush()

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "persons",
                    AuditLog.entity_id == person.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(a.action == "insert" for a in audit)


async def test_soft_delete_sets_deleted_at(db_session: AsyncSession) -> None:
    """Soft delete: установка deleted_at рождает audit-запись action=delete."""
    import datetime as dt

    _, tree = await _seed_user_and_tree(db_session)
    person = Person(tree_id=tree.id, sex=Sex.UNKNOWN.value)
    db_session.add(person)
    await db_session.flush()

    person.deleted_at = dt.datetime.now(dt.UTC)
    await db_session.flush()

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "persons",
                    AuditLog.entity_id == person.id,
                    AuditLog.action == "delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert audit, "expected delete audit entry after soft delete"
    assert person.is_deleted is True


async def test_version_id_increments_on_update(db_session: AsyncSession) -> None:
    """version_id растёт на каждый UPDATE."""
    _, tree = await _seed_user_and_tree(db_session)
    person = Person(tree_id=tree.id, sex=Sex.UNKNOWN.value)
    db_session.add(person)
    await db_session.flush()
    initial = person.version_id

    person.sex = Sex.MALE.value
    await db_session.flush()
    assert person.version_id == initial + 1
