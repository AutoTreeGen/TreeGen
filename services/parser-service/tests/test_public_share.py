"""Интеграционные тесты Phase 11.2 public-share API.

Покрывают:

* ``POST /trees/{id}/public-share`` — owner-only (403 для не-owner), happy path,
  идемпотентность (возврат существующего вместо создания второго).
* ``GET /trees/{id}/public-share`` — null если нет активного, объект если есть.
* ``DELETE /trees/{id}/public-share`` — soft-revoke, идемпотентен.
* ``GET /public/trees/{token}`` — без auth, 404 для unknown/revoked/expired,
  rate-limit (429), DNA-cut, alive-anonymization.

Маркеры: ``db`` + ``integration`` — testcontainers Postgres.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from parser_service.utils.rate_limiter import (
    PUBLIC_SHARE_RATE_LIMIT_MAX,
    public_share_rate_limiter,
)
from shared_models import TreeRole
from shared_models.orm import (
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Name,
    Person,
    PublicTreeShare,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Каждый тест получает чистый bucket'ы — иначе предыдущий тест мог
    выжрать budget этого IP."""
    public_share_rate_limiter.reset()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"share-{uuid.uuid4().hex[:8]}@example.com"
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


async def _make_tree_with_owner_membership(factory: Any, *, owner: User) -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Public Share Test {uuid.uuid4().hex[:6]}",
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


async def _add_person(
    factory: Any,
    *,
    tree: Tree,
    given: str,
    surname: str,
    sex: str = "M",
    birth_year: int | None = None,
    death_year: int | None = None,
) -> Person:
    """Создать персону + Name + опционально BIRT/DEAT events."""
    async with factory() as session:
        person = Person(tree_id=tree.id, sex=sex)
        session.add(person)
        await session.flush()
        session.add(
            Name(
                person_id=person.id,
                given_name=given,
                surname=surname,
            )
        )
        if birth_year is not None:
            ev = Event(
                tree_id=tree.id,
                event_type="BIRT",
                date_start=dt.date(birth_year, 1, 1),
            )
            session.add(ev)
            await session.flush()
            session.add(EventParticipant(event_id=ev.id, person_id=person.id))
        if death_year is not None:
            ev = Event(
                tree_id=tree.id,
                event_type="DEAT",
                date_start=dt.date(death_year, 1, 1),
            )
            session.add(ev)
            await session.flush()
            session.add(EventParticipant(event_id=ev.id, person_id=person.id))
        await session.commit()
        await session.refresh(person)
        return person


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# Owner-side endpoints.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_share_owner_only(app_client, session_factory: Any) -> None:
    """Не-owner получает 403."""
    owner = await _make_user(session_factory)
    intruder = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(intruder),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_share_happy_path(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert len(body["share_token"]) >= 16  # ~20 chars URL-safe
    assert body["public_url"].endswith(body["share_token"])
    assert body["expires_at"] is not None


@pytest.mark.asyncio
async def test_create_share_no_expiration(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": None},
        headers=_hdr(owner),
    )
    assert r.status_code == 201
    assert r.json()["expires_at"] is None


@pytest.mark.asyncio
async def test_create_share_idempotent_returns_active(
    app_client,
    session_factory: Any,
) -> None:
    """Повторный POST при наличии активного share возвращает тот же row."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r1 = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201
    first_token = r1.json()["share_token"]

    r2 = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 7},  # body отличается, но активный игнорирует
        headers=_hdr(owner),
    )
    assert r2.status_code == 201
    assert r2.json()["share_token"] == first_token


@pytest.mark.asyncio
async def test_get_share_returns_null_when_none(
    app_client,
    session_factory: Any,
) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.get(f"/trees/{tree.id}/public-share", headers=_hdr(owner))
    assert r.status_code == 200
    assert r.json() is None


@pytest.mark.asyncio
async def test_delete_share_revokes_active(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]

    r = await app_client.delete(f"/trees/{tree.id}/public-share", headers=_hdr(owner))
    assert r.status_code == 204

    # Public lookup теперь 404.
    r = await app_client.get(f"/public/trees/{token}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_share_idempotent(app_client, session_factory: Any) -> None:
    """DELETE без активного share — 204 без эффекта."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.delete(f"/trees/{tree.id}/public-share", headers=_hdr(owner))
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# Token uniqueness / entropy.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_share_tokens_are_unique(app_client, session_factory: Any) -> None:
    """20 разных trees → 20 разных tokens."""
    owner = await _make_user(session_factory)
    tokens: set[str] = set()
    for _ in range(20):
        tree = await _make_tree_with_owner_membership(session_factory, owner=owner)
        r = await app_client.post(
            f"/trees/{tree.id}/public-share",
            json={"expires_in_days": 30},
            headers=_hdr(owner),
        )
        assert r.status_code == 201
        tokens.add(r.json()["share_token"])
    assert len(tokens) == 20, "duplicate token generated"


# ---------------------------------------------------------------------------
# Public endpoint (no auth).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_get_no_auth_required(app_client, session_factory: Any) -> None:
    """GET /public/trees/{token} НЕ требует X-User-Id."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]

    # Без headers вообще.
    r = await app_client.get(f"/public/trees/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tree_name"] == tree.name


@pytest.mark.asyncio
async def test_public_get_unknown_token_returns_404(app_client) -> None:
    r = await app_client.get("/public/trees/this-token-does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_public_get_revoked_returns_404(
    app_client,
    session_factory: Any,
) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]
    await app_client.delete(f"/trees/{tree.id}/public-share", headers=_hdr(owner))

    r = await app_client.get(f"/public/trees/{token}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_public_get_expired_returns_404(
    app_client,
    session_factory: Any,
) -> None:
    """Backdate expires_at напрямую в DB → 404."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    async with session_factory() as session:
        share = PublicTreeShare(
            tree_id=tree.id,
            share_token="expired-token-fixture-xx",
            created_by_user_id=owner.id,
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        )
        session.add(share)
        await session.commit()
        token = share.share_token

    r = await app_client.get(f"/public/trees/{token}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Privacy filters: NO DNA, alive anonymized.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_response_has_no_dna_keys(
    app_client,
    session_factory: Any,
) -> None:
    """Структурный тест: на верхнем и person-level нет DNA-ключей вообще."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)
    await _add_person(
        session_factory,
        tree=tree,
        given="John",
        surname="Smith",
        birth_year=1850,
        death_year=1920,
    )

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]

    r = await app_client.get(f"/public/trees/{token}")
    body = r.json()
    forbidden_substrings = ("dna", "kit", "match", "ethnicity", "consent")
    serialized = str(body).lower()
    for sub in forbidden_substrings:
        assert sub not in serialized, f"DNA-related key {sub!r} leaked into public response"


@pytest.mark.asyncio
async def test_public_anonymizes_alive_person(
    app_client,
    session_factory: Any,
) -> None:
    """Persons без DEAT-event'а и с recent birth — anonymized в ответе."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    now = dt.datetime.now(dt.UTC).year
    # Living relative — родился 30 лет назад, нет DEAT.
    living = await _add_person(
        session_factory,
        tree=tree,
        given="Jane",
        surname="Doe",
        sex="F",
        birth_year=now - 30,
    )
    # Deceased — has DEAT.
    deceased = await _add_person(
        session_factory,
        tree=tree,
        given="John",
        surname="Smith",
        sex="M",
        birth_year=1850,
        death_year=1920,
    )

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]

    r = await app_client.get(f"/public/trees/{token}")
    body = r.json()
    persons_by_id = {p["id"]: p for p in body["persons"]}

    living_dto = persons_by_id[str(living.id)]
    assert living_dto["is_anonymized"] is True
    assert living_dto["display_name"] == "Living relative"
    assert living_dto["birth_year"] is None
    assert living_dto["death_year"] is None
    assert living_dto["sex"] == "F"  # Sex сохранён.

    deceased_dto = persons_by_id[str(deceased.id)]
    assert deceased_dto["is_anonymized"] is False
    assert "John" in deceased_dto["display_name"] or "Smith" in deceased_dto["display_name"]
    assert deceased_dto["birth_year"] == 1850
    assert deceased_dto["death_year"] == 1920


@pytest.mark.asyncio
async def test_public_includes_families(app_client, session_factory: Any) -> None:
    """Family edges (husband/wife/children) присутствуют в ответе."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    husband = await _add_person(
        session_factory,
        tree=tree,
        given="A",
        surname="X",
        sex="M",
        birth_year=1850,
        death_year=1920,
    )
    wife = await _add_person(
        session_factory,
        tree=tree,
        given="B",
        surname="X",
        sex="F",
        birth_year=1855,
        death_year=1925,
    )
    child = await _add_person(
        session_factory,
        tree=tree,
        given="C",
        surname="X",
        sex="M",
        birth_year=1880,
        death_year=1940,
    )
    async with session_factory() as session:
        family = Family(
            tree_id=tree.id,
            husband_id=husband.id,
            wife_id=wife.id,
        )
        session.add(family)
        await session.flush()
        session.add(FamilyChild(family_id=family.id, child_person_id=child.id))
        await session.commit()

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]

    r = await app_client.get(f"/public/trees/{token}")
    body = r.json()
    assert len(body["families"]) == 1
    fam = body["families"][0]
    assert fam["husband_id"] == str(husband.id)
    assert fam["wife_id"] == str(wife.id)
    assert str(child.id) in fam["children_ids"]


# ---------------------------------------------------------------------------
# Rate-limiting.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_rate_limit_returns_429(
    app_client,
    session_factory: Any,
) -> None:
    """61-й запрос за минуту с того же IP → 429."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/public-share",
        json={"expires_in_days": 30},
        headers=_hdr(owner),
    )
    token = r.json()["share_token"]

    # Истратить весь budget (60 ok-запросов).
    for _ in range(PUBLIC_SHARE_RATE_LIMIT_MAX):
        r = await app_client.get(f"/public/trees/{token}")
        assert r.status_code == 200

    # 61-й — 429.
    r = await app_client.get(f"/public/trees/{token}")
    assert r.status_code == 429
