"""Tests for Phase 4.11c — auto ownership-transfer (ADR-0050).

Покрытие:

* :func:`prepare_ownership_transfers_for_user` — preflight scan:
  no owned trees → empty report; tree без других members → skip;
  tree с editor → auto_pickable; tree без editor (только viewer) → blocked.
* :func:`run_ownership_transfer` — worker logic: happy swap +
  audit + email; no-eligible-editor at runtime → blocked;
  idempotent re-call на done/failed.
* :func:`swap_tree_owner_atomic` — direct unit (extract из 4.11c
  helper, проверяем атомарность + partial-unique constraint).
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from parser_service.services.auto_transfer import (
    prepare_ownership_transfers_for_user,
    run_ownership_transfer,
)
from parser_service.services.ownership_transfer import (
    TreeMembershipMissingError,
    swap_tree_owner_atomic,
)
from shared_models.enums import AuditAction, TreeRole
from shared_models.orm import (
    AuditLog,
    Tree,
    TreeMembership,
    User,
    UserActionRequest,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_maker(postgres_dsn: str):
    from parser_service.database import get_engine, init_engine

    init_engine(postgres_dsn)
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def stub_email_dispatcher():
    """Stub send_transactional_email — log-only stub Phase 12.2a, всё равно AsyncMock'аем."""
    with patch(
        "parser_service.services.auto_transfer.send_transactional_email",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock:
        yield mock


@pytest_asyncio.fixture(autouse=True)
async def stub_notify_helper():
    """Stub notify_ownership_transfer_required — env URL пустая в тестах, helper
    делает early-return; mock на всякий случай чтобы не ходить в queue."""
    with patch(
        "parser_service.services.auto_transfer.notify_ownership_transfer_required",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(sm: async_sessionmaker, *, email: str | None = None) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    e = email or f"u_{suffix}@test.local"
    async with sm() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name=e.split("@", 1)[0].title(),
            locale="en",
        )
        session.add(user)
        await session.flush()
        await session.commit()
        return user.id


async def _make_tree_with_owner_membership(
    sm: async_sessionmaker, *, owner_id: uuid.UUID
) -> uuid.UUID:
    """Tree + явный OWNER membership (current contract от Phase 11.0)."""
    suffix = uuid.uuid4().hex[:6]
    async with sm() as session:
        tree = Tree(
            owner_user_id=owner_id,
            name=f"Tree {suffix}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=owner_id,
                role=TreeRole.OWNER.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.flush()
        await session.commit()
        return tree.id


async def _add_membership(
    sm: async_sessionmaker,
    *,
    tree_id: uuid.UUID,
    user_id: uuid.UUID,
    role: TreeRole,
    created_at: dt.datetime | None = None,
) -> uuid.UUID:
    async with sm() as session:
        m = TreeMembership(
            tree_id=tree_id,
            user_id=user_id,
            role=role.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        if created_at is not None:
            m.created_at = created_at
            m.updated_at = created_at
        session.add(m)
        await session.flush()
        await session.commit()
        return m.id


# ---------------------------------------------------------------------------
# swap_tree_owner_atomic — direct unit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swap_changes_owner_and_demotes_old_owner(session_maker) -> None:
    owner = await _make_user(session_maker)
    new_owner = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree_id, user_id=new_owner, role=TreeRole.EDITOR)

    async with session_maker() as session:
        result = await swap_tree_owner_atomic(
            session,
            tree_id=tree_id,
            current_owner_user_id=owner,
            new_owner_user_id=new_owner,
        )
        await session.commit()

    assert result.previous_owner_user_id == owner
    assert result.new_owner_user_id == new_owner

    async with session_maker() as session:
        memberships = (
            (await session.execute(select(TreeMembership).where(TreeMembership.tree_id == tree_id)))
            .scalars()
            .all()
        )
        tree = await session.get(Tree, tree_id)
    by_user = {m.user_id: m.role for m in memberships}
    assert by_user[owner] == TreeRole.EDITOR.value
    assert by_user[new_owner] == TreeRole.OWNER.value
    assert tree is not None
    assert tree.owner_user_id == new_owner


@pytest.mark.asyncio
async def test_swap_raises_when_target_has_no_membership(session_maker) -> None:
    owner = await _make_user(session_maker)
    stranger = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)

    async with session_maker() as session:
        with pytest.raises(TreeMembershipMissingError):
            await swap_tree_owner_atomic(
                session,
                tree_id=tree_id,
                current_owner_user_id=owner,
                new_owner_user_id=stranger,
            )


# ---------------------------------------------------------------------------
# prepare_ownership_transfers_for_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_no_owned_trees_returns_empty(session_maker) -> None:
    user_id = await _make_user(session_maker)
    async with session_maker() as session:
        report = await prepare_ownership_transfers_for_user(session, user_id=user_id)
    assert report.auto_pickable_request_ids == []
    assert report.blocked_tree_ids == []


@pytest.mark.asyncio
async def test_prepare_solo_tree_skips_no_request(session_maker) -> None:
    """Tree без других members: erasure просто soft-delete'ит, без transfer."""
    owner = await _make_user(session_maker)
    await _make_tree_with_owner_membership(session_maker, owner_id=owner)

    async with session_maker() as session:
        report = await prepare_ownership_transfers_for_user(session, user_id=owner)
        await session.commit()
    assert report.auto_pickable_request_ids == []
    assert report.blocked_tree_ids == []


@pytest.mark.asyncio
async def test_prepare_with_editor_creates_request(session_maker) -> None:
    owner = await _make_user(session_maker)
    editor = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree_id, user_id=editor, role=TreeRole.EDITOR)

    async with session_maker() as session:
        report = await prepare_ownership_transfers_for_user(session, user_id=owner)
        await session.commit()

    assert len(report.auto_pickable_request_ids) == 1
    assert report.blocked_tree_ids == []

    # Request-row created, status=pending.
    request_id = report.auto_pickable_request_ids[0]
    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert row.kind == "ownership_transfer"
    assert row.status == "pending"
    assert row.request_metadata["tree_id"] == str(tree_id)
    assert row.request_metadata["candidate_new_owner_user_id"] == str(editor)


@pytest.mark.asyncio
async def test_prepare_only_viewer_blocks(session_maker, stub_notify_helper) -> None:
    """Tree с viewer (не editor) — auto-transfer невозможен; blocked + notification."""
    owner = await _make_user(session_maker)
    viewer = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree_id, user_id=viewer, role=TreeRole.VIEWER)

    async with session_maker() as session:
        report = await prepare_ownership_transfers_for_user(session, user_id=owner)
        await session.commit()

    assert report.auto_pickable_request_ids == []
    assert report.blocked_tree_ids == [tree_id]
    stub_notify_helper.assert_awaited()

    # Audit BLOCKED entry written.
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tree_id == tree_id,
                        AuditLog.action == AuditAction.OWNERSHIP_TRANSFER_BLOCKED.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_prepare_picks_oldest_editor(session_maker) -> None:
    """Когда есть два editor'а — выигрывает с меньшим created_at."""
    owner = await _make_user(session_maker)
    older_editor = await _make_user(session_maker)
    newer_editor = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)

    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    await _add_membership(
        session_maker,
        tree_id=tree_id,
        user_id=older_editor,
        role=TreeRole.EDITOR,
        created_at=base,
    )
    await _add_membership(
        session_maker,
        tree_id=tree_id,
        user_id=newer_editor,
        role=TreeRole.EDITOR,
        created_at=base + dt.timedelta(days=30),
    )

    async with session_maker() as session:
        report = await prepare_ownership_transfers_for_user(session, user_id=owner)
        await session.commit()

    request_id = report.auto_pickable_request_ids[0]
    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert row.request_metadata["candidate_new_owner_user_id"] == str(older_editor)


@pytest.mark.asyncio
async def test_prepare_handles_multiple_trees(session_maker) -> None:
    owner = await _make_user(session_maker)
    editor1 = await _make_user(session_maker)
    editor2 = await _make_user(session_maker)
    tree1 = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    tree2 = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree1, user_id=editor1, role=TreeRole.EDITOR)
    await _add_membership(session_maker, tree_id=tree2, user_id=editor2, role=TreeRole.EDITOR)

    async with session_maker() as session:
        report = await prepare_ownership_transfers_for_user(session, user_id=owner)
        await session.commit()

    assert len(report.auto_pickable_request_ids) == 2
    assert report.blocked_tree_ids == []


# ---------------------------------------------------------------------------
# run_ownership_transfer (worker)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_happy_path_swaps_and_emails(session_maker, stub_email_dispatcher) -> None:
    owner = await _make_user(session_maker)
    editor = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree_id, user_id=editor, role=TreeRole.EDITOR)

    # Create the request row directly (bypass prepare for unit isolation).
    async with session_maker() as session:
        req = UserActionRequest(
            user_id=owner,
            kind="ownership_transfer",
            status="pending",
            request_metadata={
                "tree_id": str(tree_id),
                "candidate_new_owner_user_id": str(editor),
            },
        )
        session.add(req)
        await session.flush()
        await session.commit()
        request_id = req.id

    async with session_maker() as session:
        result = await run_ownership_transfer(session, request_id)
        await session.commit()

    assert not result.blocked
    assert result.new_owner_user_id == editor
    stub_email_dispatcher.assert_awaited_once()
    call = stub_email_dispatcher.await_args
    assert call.kwargs["kind"] == "ownership_transferred"
    assert call.kwargs["recipient_user_id"] == editor
    assert call.kwargs["params"]["tree_id"] == str(tree_id)

    # Tree owner switched.
    async with session_maker() as session:
        tree = await session.get(Tree, tree_id)
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
        audit_rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tree_id == tree_id,
                        AuditLog.action == AuditAction.OWNERSHIP_TRANSFER_AUTO.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert tree is not None
    assert tree.owner_user_id == editor
    assert row.status == "done"
    assert row.processed_at is not None
    assert row.request_metadata["new_owner_user_id"] == str(editor)
    assert len(audit_rows) >= 1


@pytest.mark.asyncio
async def test_run_blocked_when_no_eligible_at_runtime(session_maker, stub_notify_helper) -> None:
    """Editor revoke'нул себя между preflight и run — worker emits blocked."""
    owner = await _make_user(session_maker)
    editor = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree_id, user_id=editor, role=TreeRole.EDITOR)

    async with session_maker() as session:
        req = UserActionRequest(
            user_id=owner,
            kind="ownership_transfer",
            status="pending",
            request_metadata={
                "tree_id": str(tree_id),
                "candidate_new_owner_user_id": str(editor),
            },
        )
        session.add(req)
        await session.flush()
        await session.commit()
        request_id = req.id

        # Editor revoke'нул себя ДО запуска worker'а.
        m = await session.scalar(
            select(TreeMembership).where(
                TreeMembership.tree_id == tree_id,
                TreeMembership.user_id == editor,
            )
        )
        m.revoked_at = dt.datetime.now(dt.UTC)
        await session.commit()

    async with session_maker() as session:
        result = await run_ownership_transfer(session, request_id)
        await session.commit()

    assert result.blocked
    assert result.new_owner_user_id is None
    stub_notify_helper.assert_awaited()

    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
        tree = await session.get(Tree, tree_id)
    assert row.status == "failed"
    assert row.error == "no_eligible_editor"
    assert tree is not None
    assert tree.owner_user_id == owner  # не изменился


@pytest.mark.asyncio
async def test_run_idempotent_on_done(session_maker, stub_email_dispatcher) -> None:
    """Повторный run на done-row — early-return без re-swap / re-email."""
    owner = await _make_user(session_maker)
    editor = await _make_user(session_maker)
    tree_id = await _make_tree_with_owner_membership(session_maker, owner_id=owner)
    await _add_membership(session_maker, tree_id=tree_id, user_id=editor, role=TreeRole.EDITOR)

    async with session_maker() as session:
        req = UserActionRequest(
            user_id=owner,
            kind="ownership_transfer",
            status="pending",
            request_metadata={
                "tree_id": str(tree_id),
                "candidate_new_owner_user_id": str(editor),
            },
        )
        session.add(req)
        await session.flush()
        await session.commit()
        request_id = req.id

    # First run.
    async with session_maker() as session:
        await run_ownership_transfer(session, request_id)
        await session.commit()

    stub_email_dispatcher.reset_mock()

    # Second run on now-done row.
    async with session_maker() as session:
        result = await run_ownership_transfer(session, request_id)
        await session.commit()

    assert not result.blocked
    stub_email_dispatcher.assert_not_awaited()
