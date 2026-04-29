"""Auto ownership-transfer для shared trees перед GDPR-erasure (Phase 4.11c).

Public entry-points:

* :func:`prepare_ownership_transfers_for_user` — preflight для erasure
  (вызывается из 4.11b ``user_erasure_runner``). Сканирует owned trees
  user'а; для каждого, у которого есть active не-owner members, создаёт
  ``UserActionRequest(kind='ownership_transfer')`` row + enqueue'ит
  worker job. Возвращает counts {auto_pickable, blocked} — caller
  решает, можно ли продолжать erasure.
* :func:`run_ownership_transfer` — worker logic для одной ``ownership_transfer``
  request-row. Picks next-eligible editor (oldest active EDITOR
  membership), вызывает :func:`swap_tree_owner_atomic`, audit'ит, шлёт
  email новому owner'у. Если нет eligible editor — ``status='failed'``,
  notification к user'у через ``OWNERSHIP_TRANSFER_REQUIRED``.

Invariants:

* Tree-scoped audit (``tree_id != NULL``): action OWNERSHIP_TRANSFER_AUTO
  для success, OWNERSHIP_TRANSFER_BLOCKED для no-eligible-editor.
  ``actor_user_id = выходящий owner``, ``actor_kind = SYSTEM`` (worker).
* Email to new owner — ``EmailKind.OWNERSHIP_TRANSFERRED`` с
  idempotency_key=``"ownership_transfer:{request_id}"``.
* Notification к выходящему owner'у при blocked — через
  ``NotificationEventType.OWNERSHIP_TRANSFER_REQUIRED``.

Integration handoff: 4.11b's runner (``user_erasure_runner.run_user_erasure``)
вызывает :func:`prepare_ownership_transfers_for_user` перед основной
soft-delete cascade и решает (по counts) — продолжать ли erasure
немедленно или wait'ить, пока user разберётся с blocked-row'ами.
Конкретный wiring — Phase 4.11d (см. ADR-0050 §«Integration»).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from shared_models.enums import (
    ActorKind,
    AuditAction,
    EmailKind,
    TreeRole,
)
from shared_models.orm import (
    AuditLog,
    Tree,
    TreeMembership,
    User,
    UserActionRequest,
)
from shared_models.types import new_uuid
from sqlalchemy import select

from parser_service.services.email_dispatcher import send_transactional_email
from parser_service.services.notifications import (
    notify_ownership_transfer_required,
)
from parser_service.services.ownership_transfer import (
    SwapResult,
    swap_tree_owner_atomic,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_LOG: Final = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparedTransferReport:
    """Output ``prepare_ownership_transfers_for_user`` для caller'а (4.11b)."""

    user_id: uuid.UUID
    auto_pickable_request_ids: list[uuid.UUID]
    """Каждый row создан и enqueued; есть ≥ 1 eligible editor."""
    blocked_tree_ids: list[uuid.UUID]
    """Trees БЕЗ active editor — auto-transfer невозможен, request-row не
    создавался; user должен вручную разобраться. 4.11b может либо
    abort'ить erasure, либо продолжить (по policy)."""


@dataclass(frozen=True, slots=True)
class TransferResult:
    """Output ``run_ownership_transfer`` для worker'а / тестов."""

    request_id: uuid.UUID
    tree_id: uuid.UUID
    new_owner_user_id: uuid.UUID | None
    """``None`` если transfer blocked (нет eligible editor)."""
    blocked: bool


# ---------------------------------------------------------------------------
# Public preflight: vызывается из 4.11b user_erasure_runner
# ---------------------------------------------------------------------------


async def prepare_ownership_transfers_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> PreparedTransferReport:
    """Сканировать owned trees + создать transfer-requests где возможно.

    Args:
        session: AsyncSession (caller commit'ит).
        user_id: уходящий owner.

    Returns:
        :class:`PreparedTransferReport` с двумя списками — кто может
        быть auto-transferred (request-row создан), и кто blocked
        (нет active editor, нужно manual intervention).

    Side-effects:
        Вставляет ``UserActionRequest(kind='ownership_transfer')`` и
        ``audit_log`` rows. Не enqueue'ит arq jobs — это делает caller
        (потому что pool-injection — HTTP/worker concern, не service-level).
        Caller получает request_ids и сам пушит в очередь.
    """
    owned_tree_ids = await _list_owned_tree_ids(session, user_id=user_id)
    if not owned_tree_ids:
        return PreparedTransferReport(
            user_id=user_id,
            auto_pickable_request_ids=[],
            blocked_tree_ids=[],
        )

    auto_pickable: list[uuid.UUID] = []
    blocked: list[uuid.UUID] = []
    now = dt.datetime.now(dt.UTC)

    for tree_id in owned_tree_ids:
        # Если у дерева нет других active members — не нужен transfer
        # (erasure просто soft-delete'ит дерево). Skip без request-row.
        other_members = await _count_other_active_members(
            session, tree_id=tree_id, exclude_user_id=user_id
        )
        if other_members == 0:
            continue

        eligible = await _next_eligible_editor(session, tree_id=tree_id, exclude_user_id=user_id)
        if eligible is None:
            blocked.append(tree_id)
            await _emit_blocked_notification(
                session,
                user_id=user_id,
                tree_id=tree_id,
                now=now,
            )
            session.add(
                _build_audit(
                    tree_id=tree_id,
                    actor_user_id=user_id,
                    action=AuditAction.OWNERSHIP_TRANSFER_BLOCKED,
                    metadata={
                        "reason": "no_eligible_editor",
                        "other_members": other_members,
                    },
                    now=now,
                )
            )
            continue

        # Eligible editor нашёлся — создаём request-row.
        request_id = await _create_transfer_request(
            session,
            user_id=user_id,
            tree_id=tree_id,
            target_user_id=eligible,
            now=now,
        )
        auto_pickable.append(request_id)

    await session.flush()
    return PreparedTransferReport(
        user_id=user_id,
        auto_pickable_request_ids=auto_pickable,
        blocked_tree_ids=blocked,
    )


# ---------------------------------------------------------------------------
# Worker: process one ownership_transfer request-row
# ---------------------------------------------------------------------------


async def run_ownership_transfer(
    session: AsyncSession,
    request_id: uuid.UUID,
) -> TransferResult:
    """Process one ``UserActionRequest(kind='ownership_transfer')`` row.

    Pipeline:

    1. Load row + sanity-check kind. If terminal — early-return idempotent.
    2. Set ``status='processing'``.
    3. Re-validate eligible editor (between preflight и worker run могло
       пройти время; member мог revoke'нуть себя, или новый editor
       мог появиться). Picks newest snapshot.
    4. Если nobody eligible → `status='failed'`, audit BLOCKED, notify.
       Возвращает ``blocked=True``.
    5. Если eligible → :func:`swap_tree_owner_atomic`, audit AUTO,
       email новому owner'у, ``status='done'``. Возвращает new_owner_id.

    Caller (arq job) commit'ит session.
    """
    request = await _load_transfer_request(session, request_id)
    user = await session.get(User, request.user_id)
    if user is None:
        msg = f"User {request.user_id} not found (already erased?)"
        raise LookupError(msg)

    tree_id_raw = (request.request_metadata or {}).get("tree_id")
    if not isinstance(tree_id_raw, str):
        msg = f"UserActionRequest {request_id} has malformed metadata: tree_id missing"
        raise ValueError(msg)
    tree_id = uuid.UUID(tree_id_raw)

    if request.status in ("done", "failed", "cancelled"):
        _LOG.info(
            "run_ownership_transfer: row %s already terminal (status=%s) — no-op",
            request_id,
            request.status,
        )
        # Reconstruct sterile result; не имеем больше точного new_owner_id
        # без догадок, поэтому только request_id + tree_id.
        new_owner = (request.request_metadata or {}).get("new_owner_user_id")
        return TransferResult(
            request_id=request_id,
            tree_id=tree_id,
            new_owner_user_id=uuid.UUID(new_owner) if isinstance(new_owner, str) else None,
            blocked=request.status == "failed",
        )

    request.status = "processing"
    request.error = None
    await session.flush()

    # Re-validate eligible editor — preflight могла быть давно.
    eligible = await _next_eligible_editor(session, tree_id=tree_id, exclude_user_id=user.id)
    now = dt.datetime.now(dt.UTC)

    if eligible is None:
        await _emit_blocked_notification(
            session,
            user_id=user.id,
            tree_id=tree_id,
            now=now,
        )
        request.status = "failed"
        request.processed_at = now
        request.error = "no_eligible_editor"
        request.request_metadata = {
            **(request.request_metadata or {}),
            "blocked_at": now.isoformat(),
        }
        session.add(
            _build_audit(
                tree_id=tree_id,
                actor_user_id=user.id,
                action=AuditAction.OWNERSHIP_TRANSFER_BLOCKED,
                metadata={
                    "reason": "no_eligible_editor_at_runtime",
                    "request_id": str(request_id),
                },
                now=now,
            )
        )
        await session.flush()
        return TransferResult(
            request_id=request_id,
            tree_id=tree_id,
            new_owner_user_id=None,
            blocked=True,
        )

    swap = await swap_tree_owner_atomic(
        session,
        tree_id=tree_id,
        current_owner_user_id=user.id,
        new_owner_user_id=eligible,
    )

    # Audit (tree-scoped, actor=уходящий owner, kind=SYSTEM т.к. worker).
    session.add(
        _build_audit(
            tree_id=tree_id,
            actor_user_id=user.id,
            action=AuditAction.OWNERSHIP_TRANSFER_AUTO,
            metadata={
                "previous_owner_user_id": str(swap.previous_owner_user_id),
                "new_owner_user_id": str(swap.new_owner_user_id),
                "request_id": str(request_id),
                "swapped_at": swap.swapped_at.isoformat(),
            },
            now=now,
        )
    )

    # Email new owner. Idempotency-key per request → safe re-enqueue.
    await _notify_new_owner_via_email(
        session,
        new_owner_user_id=swap.new_owner_user_id,
        previous_owner_user_id=swap.previous_owner_user_id,
        tree_id=tree_id,
        request_id=request_id,
    )

    request.status = "done"
    request.processed_at = now
    request.request_metadata = {
        **(request.request_metadata or {}),
        "new_owner_user_id": str(swap.new_owner_user_id),
        "swapped_at": swap.swapped_at.isoformat(),
    }
    await session.flush()

    return TransferResult(
        request_id=request_id,
        tree_id=tree_id,
        new_owner_user_id=swap.new_owner_user_id,
        blocked=False,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _list_owned_tree_ids(session: AsyncSession, *, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Trees где user — active OWNER (через TreeMembership ИЛИ legacy
    ``trees.owner_user_id``).

    Аналогично ``user_export_runner._list_owned_tree_ids`` — оставляем
    inline вместо общего helper'а, чтобы 4.11b/c рефакторинг не каскадил.
    """
    membership_ids = (
        (
            await session.execute(
                select(TreeMembership.tree_id).where(
                    TreeMembership.user_id == user_id,
                    TreeMembership.role == TreeRole.OWNER.value,
                    TreeMembership.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    legacy_ids = (
        (await session.execute(select(Tree.id).where(Tree.owner_user_id == user_id)))
        .scalars()
        .all()
    )
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for tid in (*membership_ids, *legacy_ids):
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


async def _count_other_active_members(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    exclude_user_id: uuid.UUID,
) -> int:
    """Сколько active не-owner members у дерева (не считая выходящего user'а)."""
    rows = (
        (
            await session.execute(
                select(TreeMembership.id).where(
                    TreeMembership.tree_id == tree_id,
                    TreeMembership.user_id != exclude_user_id,
                    TreeMembership.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def _next_eligible_editor(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    exclude_user_id: uuid.UUID,
) -> uuid.UUID | None:
    """Pick next-eligible editor: oldest ``role='editor'`` membership.

    Policy (см. ADR-0050 §«Eligibility»): только ``role='editor'``;
    viewers НЕ eligible (даже если они старшие). Это снижает риск
    случайной передачи дерева кому-то, кто не работал с ним. Если
    eligible нет — caller fall-back'ает на BLOCKED + manual intervention
    notification.

    Tiebreaker — ``created_at ASC, id ASC`` для детерминизма (если
    два editor'а добавлены в одну миллисекунду — выигрывает меньший UUID).
    """
    result: uuid.UUID | None = await session.scalar(
        select(TreeMembership.user_id)
        .where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.user_id != exclude_user_id,
            TreeMembership.role == TreeRole.EDITOR.value,
            TreeMembership.revoked_at.is_(None),
        )
        .order_by(TreeMembership.created_at.asc(), TreeMembership.id.asc())
        .limit(1)
    )
    return result


async def _create_transfer_request(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
    target_user_id: uuid.UUID,
    now: dt.datetime,
) -> uuid.UUID:
    """Insert UserActionRequest + AUTO-pickable audit-entry."""
    row = UserActionRequest(
        id=new_uuid(),
        user_id=user_id,
        kind="ownership_transfer",
        status="pending",
        request_metadata={
            "tree_id": str(tree_id),
            "candidate_new_owner_user_id": str(target_user_id),
            "preflight_at": now.isoformat(),
        },
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.add(
        _build_audit(
            tree_id=tree_id,
            actor_user_id=user_id,
            action=AuditAction.OWNERSHIP_TRANSFER_AUTO,
            metadata={
                "stage": "preflight_request_created",
                "request_id": str(row.id),
                "candidate_new_owner_user_id": str(target_user_id),
            },
            now=now,
        )
    )
    return row.id


async def _load_transfer_request(session: AsyncSession, request_id: uuid.UUID) -> UserActionRequest:
    """Load row; raise LookupError/ValueError на отсутствие/неверный kind."""
    row = await session.scalar(select(UserActionRequest).where(UserActionRequest.id == request_id))
    if row is None:
        msg = f"UserActionRequest {request_id} not found"
        raise LookupError(msg)
    if row.kind != "ownership_transfer":
        msg = f"UserActionRequest {request_id} has kind={row.kind!r}, expected 'ownership_transfer'"
        raise ValueError(msg)
    return row


def _build_audit(
    *,
    tree_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: AuditAction,
    metadata: dict[str, Any],
    now: dt.datetime,
) -> AuditLog:
    """Tree-scoped audit-entry — ownership_transfer относится к дереву."""
    return AuditLog(
        id=new_uuid(),
        tree_id=tree_id,
        entity_type="trees",
        entity_id=tree_id,
        action=action.value,
        actor_user_id=actor_user_id,
        actor_kind=ActorKind.SYSTEM.value,
        import_job_id=None,
        reason=None,
        diff={"action": action.value, "metadata": metadata},
        created_at=now,
    )


async def _emit_blocked_notification(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
    now: dt.datetime,
) -> None:
    """Trigger ``OWNERSHIP_TRANSFER_REQUIRED`` notification.

    Использует :func:`notify_ownership_transfer_required` (helper в
    parser_service.services.notifications), который enqueue'ит arq job
    → notification-service делает реальную доставку. Pattern зеркалит
    :func:`notify_hypothesis_pending_review`. Schema-mismatch: BigInteger
    ``Notification.user_id`` через ``UUID.int`` coercion (см. ADR-0024
    §«user_id legacy»; full schema fix — отдельная фаза).

    Session-параметр держим для симметрии с другими helper'ами этого
    модуля (audit-write через session). Сама notification — best-effort,
    cross-service.
    """
    _ = session  # см. docstring
    _ = now  # notification timestamp ставит notification-service сама.
    await notify_ownership_transfer_required(
        user_id=user_id,
        tree_id=tree_id,
        reason="no_eligible_editor",
    )


async def _notify_new_owner_via_email(
    session: AsyncSession,
    *,
    new_owner_user_id: uuid.UUID,
    previous_owner_user_id: uuid.UUID,
    tree_id: uuid.UUID,
    request_id: uuid.UUID,
) -> None:
    """Fire ``ownership_transferred`` email через email-dispatcher stub.

    Phase 12.2a stub: log-only. Phase 12.2b добавит real HTTP к email-service.
    Idempotency-key per request — safe re-enqueue.
    """
    _ = session  # email-dispatcher не использует session — параметр держим
    # для симметрии с другими worker-helper'ами + future-proof.
    await send_transactional_email(
        kind=EmailKind.OWNERSHIP_TRANSFERRED.value,
        recipient_user_id=new_owner_user_id,
        idempotency_key=f"ownership_transfer:{request_id}",
        params={
            "tree_id": str(tree_id),
            "previous_owner_user_id": str(previous_owner_user_id),
        },
    )


__all__ = [
    "PreparedTransferReport",
    "SwapResult",
    "TransferResult",
    "prepare_ownership_transfers_for_user",
    "run_ownership_transfer",
]
