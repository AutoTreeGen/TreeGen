"""Тесты PATCH /dna-kits/{kit_id}/link-person (Phase 7.3 / ADR-0023).

Покрытие:
    - link: kit + person в одном дереве → 200, person_id выставлен.
    - unlink: payload `{person_id: null}` → 200, person_id очищен.
    - kit not found → 404.
    - person not found / soft-deleted → 404.
    - cross-tree refusal (person.tree_id != kit.tree_id) → 409.
    - idempotent unlink (повторный null без изменений) → 200.
"""

from __future__ import annotations

import uuid

import pytest
from shared_models.orm import DnaKit, Person, Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _seed_kit_and_person(
    postgres_dsn: str,
    *,
    same_tree: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Создать DnaKit + Person; вернуть (kit_id, person_id).

    Если ``same_tree=True`` — оба в одном дереве, разрешено линковаться.
    Если ``False`` — разные деревья (проверка cross-tree refusal).
    """
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        user = User(
            email=f"kit-test-{suffix}@example.com",
            external_auth_id=f"auth0|kit-test-{suffix}",
            display_name="Kit Test User",
        )
        session.add(user)
        await session.flush()

        kit_tree = Tree(owner_user_id=user.id, name=f"Kit Tree {suffix}")
        session.add(kit_tree)
        await session.flush()

        if same_tree:
            person_tree_id = kit_tree.id
        else:
            other_tree = Tree(owner_user_id=user.id, name=f"Other Tree {suffix}")
            session.add(other_tree)
            await session.flush()
            person_tree_id = other_tree.id

        kit = DnaKit(
            tree_id=kit_tree.id,
            owner_user_id=user.id,
            source_platform="ancestry",
            external_kit_id=f"ext-{suffix}",
            display_name=f"Test Kit {suffix}",
        )
        session.add(kit)

        person = Person(tree_id=person_tree_id)
        session.add(person)
        await session.flush()

        result = (kit.id, person.id)
    await engine.dispose()
    return result


async def _soft_delete_person(postgres_dsn: str, person_id: uuid.UUID) -> None:
    """Помечает person как soft-deleted для теста not-found."""
    import datetime as dt

    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        person = await session.get(Person, person_id)
        assert person is not None
        person.deleted_at = dt.datetime.now(dt.UTC)
    await engine.dispose()


async def _read_kit_person_link(postgres_dsn: str, kit_id: uuid.UUID) -> uuid.UUID | None:
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        kit = await session.get(DnaKit, kit_id)
        assert kit is not None
        result = kit.person_id
    await engine.dispose()
    return result


@pytest.mark.db
@pytest.mark.integration
async def test_link_kit_to_person_same_tree_succeeds(app_client, postgres_dsn) -> None:
    kit_id, person_id = await _seed_kit_and_person(postgres_dsn, same_tree=True)

    resp = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": str(person_id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(kit_id)
    assert body["person_id"] == str(person_id)

    persisted = await _read_kit_person_link(postgres_dsn, kit_id)
    assert persisted == person_id


@pytest.mark.db
@pytest.mark.integration
async def test_unlink_kit_clears_person_id(app_client, postgres_dsn) -> None:
    kit_id, person_id = await _seed_kit_and_person(postgres_dsn, same_tree=True)
    await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": str(person_id)},
    )

    resp = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["person_id"] is None

    persisted = await _read_kit_person_link(postgres_dsn, kit_id)
    assert persisted is None


@pytest.mark.db
@pytest.mark.integration
async def test_unlink_is_idempotent(app_client, postgres_dsn) -> None:
    kit_id, _ = await _seed_kit_and_person(postgres_dsn, same_tree=True)

    first = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": None},
    )
    second = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": None},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["person_id"] is None


@pytest.mark.db
@pytest.mark.integration
async def test_kit_not_found_returns_404(app_client) -> None:
    resp = await app_client.patch(
        "/dna-kits/00000000-0000-0000-0000-000000000000/link-person",
        json={"person_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert "kit" in resp.json()["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_person_not_found_returns_404(app_client, postgres_dsn) -> None:
    kit_id, _ = await _seed_kit_and_person(postgres_dsn, same_tree=True)

    resp = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert "person" in resp.json()["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_soft_deleted_person_returns_404(app_client, postgres_dsn) -> None:
    kit_id, person_id = await _seed_kit_and_person(postgres_dsn, same_tree=True)
    await _soft_delete_person(postgres_dsn, person_id)

    resp = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": str(person_id)},
    )
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_cross_tree_link_refused(app_client, postgres_dsn) -> None:
    """ADR-0023 / ADR-0012: запрещаем линковать персону из чужого дерева."""
    kit_id, person_id = await _seed_kit_and_person(postgres_dsn, same_tree=False)

    resp = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={"person_id": str(person_id)},
    )
    assert resp.status_code == 409
    assert "tree" in resp.json()["detail"].lower()

    # Линк не должен установиться.
    persisted = await _read_kit_person_link(postgres_dsn, kit_id)
    assert persisted is None


@pytest.mark.db
@pytest.mark.integration
async def test_payload_must_include_person_id_field(app_client, postgres_dsn) -> None:
    """`{}` без поля `person_id` отклоняется — caller должен явно передать null/uuid."""
    kit_id, _ = await _seed_kit_and_person(postgres_dsn, same_tree=True)
    resp = await app_client.patch(
        f"/dna-kits/{kit_id}/link-person",
        json={},
    )
    assert resp.status_code == 422
