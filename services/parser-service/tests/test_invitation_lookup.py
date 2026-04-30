"""Phase 11.1 — тесты для public lookup эндпоинта ``GET /invitations/{token}``.

UI accept-landing (``/invitations/[token]``) делает GET до accept'а, чтобы
показать tree+inviter+role и детектировать invalid/expired без consume'а
токена. Auth не требуется (token = secret); accept остаётся auth-gated.

Покрыто:

* 404 — неизвестный token.
* 410 — revoked invitation.
* 410 — expired invitation.
* 200 — pending invitation: invitee_email, role, tree_name, inviter,
  expires_at, accepted_at=null.
* 200 — already-accepted invitation (UI рисует «уже принято» state).
* No auth: запрос без X-User-Id header'а проходит (роутер в
  ``router_public`` без ``_AUTH_DEPS``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import Tree, TreeInvitation, TreeMembership, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


_DEFAULT_DISPLAY_NAME: Any = object()


async def _make_user(
    factory: Any,
    *,
    email: str | None = None,
    display_name: Any = _DEFAULT_DISPLAY_NAME,
) -> User:
    e = email or f"lookup-{uuid.uuid4().hex[:8]}@example.com"
    # Тонкость: explicit ``display_name=None`` означает «у user'а нет имени»
    # (тест fallback-а), а omitted — берём local-part email'а как default.
    resolved = e.split("@", 1)[0] if display_name is _DEFAULT_DISPLAY_NAME else display_name
    async with factory() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name=resolved,
            locale="en",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_tree(factory: Any, *, owner: User, name: str = "Lookup Test Tree") -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=name,
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


async def _make_invitation(
    factory: Any,
    *,
    tree: Tree,
    inviter: User,
    invitee_email: str = "invitee@example.com",
    role: str = "viewer",
    expires_at: dt.datetime | None = None,
    revoked_at: dt.datetime | None = None,
    accepted_at: dt.datetime | None = None,
) -> TreeInvitation:
    async with factory() as session:
        inv = TreeInvitation(
            tree_id=tree.id,
            inviter_user_id=inviter.id,
            invitee_email=invitee_email,
            role=role,
            expires_at=expires_at or (dt.datetime.now(dt.UTC) + dt.timedelta(days=14)),
            revoked_at=revoked_at,
            accepted_at=accepted_at,
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        return inv


# ---------------------------------------------------------------------------
# 404 — unknown token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_unknown_token_returns_404(app_client) -> None:
    """Случайный UUID, которого нет в DB → 404 Not Found."""
    fake_token = uuid.uuid4()
    r = await app_client.get(f"/invitations/{fake_token}")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 410 — revoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_revoked_invitation_returns_410(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    inv = await _make_invitation(
        session_factory,
        tree=tree,
        inviter=owner,
        revoked_at=dt.datetime.now(dt.UTC),
    )

    r = await app_client.get(f"/invitations/{inv.token}")
    assert r.status_code == 410
    assert "revoked" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 410 — expired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_expired_invitation_returns_410(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    inv = await _make_invitation(
        session_factory,
        tree=tree,
        inviter=owner,
        expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
    )

    r = await app_client.get(f"/invitations/{inv.token}")
    assert r.status_code == 410
    assert "expired" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 200 — happy path (pending)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_valid_invitation_returns_details(app_client, session_factory: Any) -> None:
    """Pending invitation → 200 с invitee_email/role/tree/inviter и accepted_at=None."""
    owner = await _make_user(
        session_factory,
        email="alice@example.com",
        display_name="Alice Cooper",
    )
    tree = await _make_tree(session_factory, owner=owner, name="Cooper Family Tree")
    inv = await _make_invitation(
        session_factory,
        tree=tree,
        inviter=owner,
        invitee_email="bob@example.com",
        role="editor",
    )

    r = await app_client.get(f"/invitations/{inv.token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invitee_email"] == "bob@example.com"
    assert body["role"] == "editor"
    assert body["tree_id"] == str(tree.id)
    assert body["tree_name"] == "Cooper Family Tree"
    assert body["inviter_display_name"] == "Alice Cooper"
    assert body["accepted_at"] is None
    # token не утекает обратно через response — он уже у клиента в URL.
    assert "token" not in body
    # expires_at сериализован.
    assert "expires_at" in body


@pytest.mark.asyncio
async def test_lookup_falls_back_to_inviter_email_when_no_display_name(
    app_client, session_factory: Any
) -> None:
    """Если у inviter'а нет display_name — возвращаем email."""
    owner = await _make_user(
        session_factory,
        email="anon@example.com",
        display_name=None,
    )
    tree = await _make_tree(session_factory, owner=owner)
    inv = await _make_invitation(session_factory, tree=tree, inviter=owner)

    r = await app_client.get(f"/invitations/{inv.token}")
    assert r.status_code == 200
    assert r.json()["inviter_display_name"] == "anon@example.com"


# ---------------------------------------------------------------------------
# 200 — already accepted (UI рисует «уже принято»)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_already_accepted_invitation_returns_200(
    app_client, session_factory: Any
) -> None:
    """Accepted, но не revoked/expired → 200 с accepted_at заполненным.

    UI отделяет «можно принять» от «уже принято» по этому полю.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    inv = await _make_invitation(
        session_factory,
        tree=tree,
        inviter=owner,
        accepted_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
    )

    r = await app_client.get(f"/invitations/{inv.token}")
    assert r.status_code == 200
    assert r.json()["accepted_at"] is not None


# ---------------------------------------------------------------------------
# Auth-not-required smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_does_not_require_auth_header(app_client, session_factory: Any) -> None:
    """Запрос БЕЗ ``X-User-Id`` всё равно проходит — endpoint в router_public.

    conftest.py подменяет ``get_current_user`` на stub'е, который не
    падает без header'а; для real check'а unauth нужно убедиться, что
    ручка зарегистрирована в роутере без ``_AUTH_DEPS``. Тест ниже
    выходит зелёным потому что endpoint не вызывает ``get_current_user``
    и не ломается если он не вернул user'а.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    inv = await _make_invitation(session_factory, tree=tree, inviter=owner)

    r = await app_client.get(f"/invitations/{inv.token}")
    assert r.status_code == 200
