"""Юнит-тесты read-side helper'ов sealed-set (Phase 15.11c / ADR-0082).

Проверяют:

* ``is_scope_sealed`` возвращает True только для active sealed assertion'ов
  (revoked / soft-deleted / другой scope — False);
* ``sealed_scopes_for_person`` собирает все active sealed scope'ы одним SQL;
* `_scope_to_value` принимает enum или валидный str, отвергает мусор.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from shared_models.enums import CompletenessScope, EntityStatus, Sex
from shared_models.orm import CompletenessAssertion, Person, Tree, User
from shared_models.orm.completeness_assertion import (
    _scope_to_value,
    is_scope_sealed,
    sealed_scopes_for_person,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.db, pytest.mark.integration]


async def _seed(session: AsyncSession) -> tuple[User, Tree, Person]:
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
    person = Person(tree_id=tree.id, sex=Sex.MALE.value, status=EntityStatus.PROBABLE.value)
    session.add(person)
    await session.flush()
    return user, tree, person


def test_scope_to_value_accepts_enum_and_str() -> None:
    """``_scope_to_value`` нормализует enum/str и rejects мусор."""
    assert _scope_to_value(CompletenessScope.SIBLINGS) == "siblings"
    assert _scope_to_value("children") == "children"
    with pytest.raises(ValueError, match="not a valid CompletenessScope"):
        _scope_to_value("not-a-scope")


async def test_is_scope_sealed_true_when_active(db_session: AsyncSession) -> None:
    """Active is_sealed=True → helper возвращает True."""
    user, tree, person = await _seed(db_session)
    db_session.add(
        CompletenessAssertion(
            tree_id=tree.id,
            subject_person_id=person.id,
            scope=CompletenessScope.SIBLINGS.value,
            is_sealed=True,
            asserted_by=user.id,
        )
    )
    await db_session.flush()
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.SIBLINGS) is True


async def test_is_scope_sealed_false_for_revoked(db_session: AsyncSession) -> None:
    """Revoked (is_sealed=False) row остаётся для audit, но не считается sealed."""
    user, tree, person = await _seed(db_session)
    db_session.add(
        CompletenessAssertion(
            tree_id=tree.id,
            subject_person_id=person.id,
            scope=CompletenessScope.CHILDREN.value,
            is_sealed=False,
            asserted_by=user.id,
        )
    )
    await db_session.flush()
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.CHILDREN) is False


async def test_is_scope_sealed_false_for_soft_deleted(db_session: AsyncSession) -> None:
    """Soft-deleted (deleted_at != NULL) row не виден helper'у."""
    user, tree, person = await _seed(db_session)
    db_session.add(
        CompletenessAssertion(
            tree_id=tree.id,
            subject_person_id=person.id,
            scope=CompletenessScope.PARENTS.value,
            is_sealed=True,
            asserted_by=user.id,
            deleted_at=dt.datetime.now(dt.UTC),
        )
    )
    await db_session.flush()
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.PARENTS) is False


async def test_is_scope_sealed_isolated_per_scope(db_session: AsyncSession) -> None:
    """Sealed siblings не делает sealed children — scope query precise."""
    user, tree, person = await _seed(db_session)
    db_session.add(
        CompletenessAssertion(
            tree_id=tree.id,
            subject_person_id=person.id,
            scope=CompletenessScope.SIBLINGS.value,
            is_sealed=True,
            asserted_by=user.id,
        )
    )
    await db_session.flush()
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.SIBLINGS) is True
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.CHILDREN) is False
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.SPOUSES) is False
    assert await is_scope_sealed(db_session, person.id, CompletenessScope.PARENTS) is False


async def test_sealed_scopes_for_person_collects_active_only(db_session: AsyncSession) -> None:
    """``sealed_scopes_for_person`` фильтрует по active+sealed одним SQL."""
    user, tree, person = await _seed(db_session)
    # Three rows: siblings sealed-active, parents sealed-deleted, spouses revoked.
    db_session.add_all(
        [
            CompletenessAssertion(
                tree_id=tree.id,
                subject_person_id=person.id,
                scope=CompletenessScope.SIBLINGS.value,
                is_sealed=True,
                asserted_by=user.id,
            ),
            CompletenessAssertion(
                tree_id=tree.id,
                subject_person_id=person.id,
                scope=CompletenessScope.PARENTS.value,
                is_sealed=True,
                asserted_by=user.id,
                deleted_at=dt.datetime.now(dt.UTC),
            ),
            CompletenessAssertion(
                tree_id=tree.id,
                subject_person_id=person.id,
                scope=CompletenessScope.SPOUSES.value,
                is_sealed=False,
                asserted_by=user.id,
            ),
        ]
    )
    await db_session.flush()
    result = await sealed_scopes_for_person(db_session, person.id)
    assert result == frozenset({CompletenessScope.SIBLINGS})


async def test_sealed_scopes_empty_when_none(db_session: AsyncSession) -> None:
    """Person без assertions → пустой frozenset."""
    _user, _tree, person = await _seed(db_session)
    result = await sealed_scopes_for_person(db_session, person.id)
    assert result == frozenset()
