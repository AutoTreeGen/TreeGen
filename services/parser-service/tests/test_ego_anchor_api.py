"""Phase 10.7a — integration tests для self-anchor + ego-relationship API
(ADR-0068).

Покрытие:

* PATCH /trees/{id}/owner-person:
  - happy path (OWNER set'ит anchor)
  - permission boundary (EDITOR → 403, viewer → 403)
  - clear (person_id=null)
  - 422 если person_id не существует или из другого дерева
* GET /trees/{id}/relationships/{person_id}:
  - 409 пока anchor не set'нут (Self-anchor not set)
  - happy path: ego → spouse_brother → kind='wife.brother', label_ru='брат жены'
  - 404 для disconnected person в дереве
  - language параметр switches label

Auth — X-User-Id header (см. conftest ``_fake_current_user_override``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.enums import RelationType
from shared_models.orm import (
    Family,
    FamilyChild,
    Person,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"ego-{uuid.uuid4().hex[:8]}@example.com"
    async with factory() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name=e.split("@", 1)[0],
            locale="en",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_tree_with_owner(factory: Any, *, owner: User) -> Tree:
    """Tree + OWNER membership-row."""
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Ego Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        m = TreeMembership(
            tree_id=tree.id,
            user_id=owner.id,
            role=TreeRole.OWNER.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(m)
        await session.commit()
        await session.refresh(tree)
        return tree


async def _add_membership(factory: Any, *, tree: Tree, user: User, role: TreeRole) -> None:
    async with factory() as session:
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=user.id,
                role=role.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()


async def _add_person(factory: Any, *, tree: Tree, sex: str = "U") -> Person:
    async with factory() as session:
        p = Person(
            tree_id=tree.id,
            sex=sex,
            provenance={},
            version_id=1,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p


async def _add_family(
    factory: Any,
    *,
    tree: Tree,
    husband: Person | None = None,
    wife: Person | None = None,
    children: list[Person] | None = None,
) -> Family:
    async with factory() as session:
        family = Family(
            tree_id=tree.id,
            husband_id=husband.id if husband else None,
            wife_id=wife.id if wife else None,
            provenance={},
            version_id=1,
        )
        session.add(family)
        await session.flush()
        for idx, child in enumerate(children or [], start=1):
            session.add(
                FamilyChild(
                    family_id=family.id,
                    child_person_id=child.id,
                    relation_type=RelationType.BIOLOGICAL.value,
                    birth_order=idx,
                )
            )
        await session.commit()
        await session.refresh(family)
        return family


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# PATCH /trees/{id}/owner-person
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_owner_person_happy_path(app_client, session_factory: Any) -> None:
    """OWNER set'ит anchor на персону собственного дерева → 200."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _add_person(session_factory, tree=tree, sex="M")

    r = await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(person.id)},
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert body["owner_person_id"] == str(person.id)


@pytest.mark.asyncio
async def test_patch_owner_person_clears_anchor(app_client, session_factory: Any) -> None:
    """``person_id: null`` сбрасывает anchor."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _add_person(session_factory, tree=tree, sex="M")

    await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(person.id)},
        headers=_hdr(owner),
    )
    r = await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": None},
        headers=_hdr(owner),
    )
    assert r.status_code == 200
    assert r.json()["owner_person_id"] is None


@pytest.mark.asyncio
async def test_patch_owner_person_editor_forbidden(app_client, session_factory: Any) -> None:
    """EDITOR не может set'ить anchor — это OWNER-only решение."""
    owner = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_membership(session_factory, tree=tree, user=editor, role=TreeRole.EDITOR)
    person = await _add_person(session_factory, tree=tree)

    r = await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(person.id)},
        headers=_hdr(editor),
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_patch_owner_person_other_tree_rejected(app_client, session_factory: Any) -> None:
    """``person_id`` из другого дерева → 422 (не 404, чтобы не путать с
    несуществующим деревом)."""
    owner = await _make_user(session_factory)
    other_owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    other_tree = await _make_tree_with_owner(session_factory, owner=other_owner)
    foreign_person = await _add_person(session_factory, tree=other_tree)

    r = await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(foreign_person.id)},
        headers=_hdr(owner),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_owner_person_unknown_person_422(app_client, session_factory: Any) -> None:
    """Несуществующий ``person_id`` → 422."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    r = await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(uuid.uuid4())},
        headers=_hdr(owner),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /trees/{id}/relationships/{person_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relationships_409_when_anchor_not_set(app_client, session_factory: Any) -> None:
    """Без self-anchor — 409 Conflict (Self-anchor not set)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    target = await _add_person(session_factory, tree=tree, sex="M")

    r = await app_client.get(
        f"/trees/{tree.id}/relationships/{target.id}",
        headers=_hdr(owner),
    )
    assert r.status_code == 409, r.text
    assert "self-anchor" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_relationships_wife_brother_en_and_ru(app_client, session_factory: Any) -> None:
    """End-to-end: anchor + relate(ego, spouse_brother) → wife.brother / 'wife\\'s brother' / 'брат жены'."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    ego = await _add_person(session_factory, tree=tree, sex="M")
    spouse = await _add_person(session_factory, tree=tree, sex="F")
    spouse_father = await _add_person(session_factory, tree=tree, sex="M")
    spouse_mother = await _add_person(session_factory, tree=tree, sex="F")
    spouse_brother = await _add_person(session_factory, tree=tree, sex="M")

    # Семья родителей spouse: spouse + spouse_brother — siblings.
    await _add_family(
        session_factory,
        tree=tree,
        husband=spouse_father,
        wife=spouse_mother,
        children=[spouse, spouse_brother],
    )
    # Брак ego + spouse.
    await _add_family(session_factory, tree=tree, husband=ego, wife=spouse)

    # Set anchor.
    await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(ego.id)},
        headers=_hdr(owner),
    )

    # English label.
    r_en = await app_client.get(
        f"/trees/{tree.id}/relationships/{spouse_brother.id}?language=en",
        headers=_hdr(owner),
    )
    assert r_en.status_code == 200, r_en.text
    body_en = r_en.json()
    assert body_en["path"]["kind"] == "wife.brother"
    assert body_en["path"]["degree"] == 2
    assert body_en["path"]["blood_relation"] is False
    assert body_en["path"]["is_twin"] is False
    assert body_en["label"] == "wife's brother"

    # Russian label.
    r_ru = await app_client.get(
        f"/trees/{tree.id}/relationships/{spouse_brother.id}?language=ru",
        headers=_hdr(owner),
    )
    assert r_ru.status_code == 200
    assert r_ru.json()["label"] == "брат жены"


@pytest.mark.asyncio
async def test_relationships_self(app_client, session_factory: Any) -> None:
    """ego == target → kind='self', degree=0."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    ego = await _add_person(session_factory, tree=tree, sex="M")

    await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(ego.id)},
        headers=_hdr(owner),
    )
    r = await app_client.get(
        f"/trees/{tree.id}/relationships/{ego.id}",
        headers=_hdr(owner),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["path"]["kind"] == "self"
    assert body["path"]["degree"] == 0


@pytest.mark.asyncio
async def test_relationships_disconnected_404(app_client, session_factory: Any) -> None:
    """Persona в дереве, но без связи к ego → 404 (no path)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    ego = await _add_person(session_factory, tree=tree, sex="M")
    isolated = await _add_person(session_factory, tree=tree, sex="F")

    await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(ego.id)},
        headers=_hdr(owner),
    )
    r = await app_client.get(
        f"/trees/{tree.id}/relationships/{isolated.id}",
        headers=_hdr(owner),
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_relationships_target_not_in_tree_404(app_client, session_factory: Any) -> None:
    """``person_id`` принадлежит другому дереву — 404."""
    owner = await _make_user(session_factory)
    other_owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    other_tree = await _make_tree_with_owner(session_factory, owner=other_owner)
    ego = await _add_person(session_factory, tree=tree, sex="M")
    foreign = await _add_person(session_factory, tree=other_tree, sex="F")

    await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(ego.id)},
        headers=_hdr(owner),
    )
    r = await app_client.get(
        f"/trees/{tree.id}/relationships/{foreign.id}",
        headers=_hdr(owner),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_relationships_viewer_can_read(app_client, session_factory: Any) -> None:
    """VIEWER может читать relationships — это derived data."""
    owner = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_membership(session_factory, tree=tree, user=viewer, role=TreeRole.VIEWER)

    ego = await _add_person(session_factory, tree=tree, sex="M")
    await app_client.patch(
        f"/trees/{tree.id}/owner-person",
        json={"person_id": str(ego.id)},
        headers=_hdr(owner),
    )

    r = await app_client.get(
        f"/trees/{tree.id}/relationships/{ego.id}",
        headers=_hdr(viewer),
    )
    assert r.status_code == 200
