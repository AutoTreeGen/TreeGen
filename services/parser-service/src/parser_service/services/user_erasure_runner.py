"""GDPR right-of-erasure runner (Phase 4.11b, ADR-0049).

Worker-side обработка ``user_action_requests``-row с ``kind='erasure'``.
Шаги (см. ADR-0049 §«Pipeline»):

1. ``status='processing'`` + audit ``ERASURE_PROCESSING``.
2. **Edge check A:** trees с другими members (TreeMembership.role != OWNER)
   → abort с ``status='manual_intervention_required'``,
   ``error='ownership transfer required (Phase 4.11c)'``.
3. **Edge check B:** active export request (status pending|processing) →
   abort с ``manual_intervention_required``.
4. **Edge check C** (best-effort): active subscription. Скип на Phase 4.11b
   (billing-service не deployed); placeholder hook оставлен.
5. Cascade soft-delete domain entities у owned trees:
   :func:`shared_models.cascade_delete.cascade_soft_delete`
   (persons / names / families / events / places / sources / citations /
   notes / multimedia_objects). ``provenance.erasure_request_id`` указывает
   на этот request — для traceability.
6. Hard-delete DNA: :func:`shared_models.cascade_delete.hard_delete_dna_for_user`
   (special category, ADR-0012).
7. Audit ``ERASURE_COMPLETED`` + counts (без user PII в metadata).
8. Clerk delete: ``DELETE /v1/users/{clerk_user_id}`` (Backend API).
   Best-effort: failure → log + продолжаем (Clerk-row остаётся, но
   в нашем БД user уже erased; admin может вычистить Clerk вручную).
9. Email confirmation kind=erasure_confirmation, idempotency_key=request_id.
10. ``status='completed'``, ``processed_at=now()``.
11. На любом исключении после ``processing`` transition → ``status='failed'``,
    audit ``ERASURE_FAILED``, без auto-retry.

Privacy / GDPR notes:

* Audit metadata содержит только counts (``persons_count``, ``dna_count``,
  ...) — никаких user_id, email'ов, или PII.
* Soft-deleted записи остаются в БД с ``deleted_at`` + provenance pointer:
  можно частично восстановить (Phase 4.11c) если user отозвал erasure
  в течение grace-окна. Но encrypted DNA уже физически удалена — для DNA
  никакого undo (ADR-0012 §«Hard delete для special category»).
* User-row в ``users`` остаётся (с ``deleted_at`` уже выставленным
  Clerk-webhook-handler'ом или нашим soft-delete'ом ниже) — нужен для
  email-attribution audit-rows; Phase 4.11c добавит retention-purge
  (после 30-day grace).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from shared_models.cascade_delete import (
    cascade_soft_delete,
    hard_delete_dna_for_user,
)
from shared_models.enums import ActorKind, AuditAction, EmailKind
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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_LOG: Final = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErasureResult:
    """Output run_user_erasure — для worker'а / тестов; sterile (без PII)."""

    request_id: uuid.UUID
    status: str
    persons_count: int
    families_count: int
    events_count: int
    places_count: int
    sources_count: int
    notes_count: int
    multimedia_count: int
    citations_count: int
    names_count: int
    dna_total: int
    trees_processed: int
    email_idempotency_key: str | None
    clerk_deleted: bool


# ---------------------------------------------------------------------------
# Clerk Backend API delete protocol
# ---------------------------------------------------------------------------


# Type-alias для injection в тестах. Production-impl делает HTTP DELETE
# к ``/v1/users/{id}`` Clerk Backend API (см. :func:`default_clerk_delete`).
# Тесты передают async-stub, чтобы не зависеть от живого Clerk endpoint'а.
ClerkDeleteCallable = Callable[[str], Awaitable[bool]]


async def default_clerk_delete(clerk_user_id: str) -> bool:
    """Best-effort DELETE /v1/users/{id} к Clerk Backend API.

    Возвращает ``True`` на 200/204/404 (404 = уже удалён, idempotent).
    На 5xx / network-error логирует и возвращает ``False`` — caller
    помечает ``clerk_deleted=False`` в audit, но не падает (наш-side
    erasure уже сделан). Admin позже вычистит Clerk вручную.

    ENV: ``CLERK_SECRET_KEY`` обязательна. Без ключа — ранний
    ``False`` + warning (не raise).
    """
    import os  # noqa: PLC0415  — ленивый импорт, не нужен в тестах

    secret = os.environ.get("CLERK_SECRET_KEY")
    if not secret:
        _LOG.warning(
            "CLERK_SECRET_KEY not set; skipping Clerk delete for user=%s "
            "(non-fatal, our-side erasure proceeds)",
            clerk_user_id,
        )
        return False

    import httpx  # noqa: PLC0415

    url = f"https://api.clerk.com/v1/users/{clerk_user_id}"
    headers = {"Authorization": f"Bearer {secret}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        _LOG.warning(
            "Clerk delete failed for user=%s: %s (non-fatal)",
            clerk_user_id,
            exc,
        )
        return False

    if resp.status_code in (200, 204, 404):
        return True
    _LOG.warning(
        "Clerk delete returned %s for user=%s body=%s (non-fatal)",
        resp.status_code,
        clerk_user_id,
        resp.text[:500],
    )
    return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_user_erasure(
    session: AsyncSession,
    request_id: uuid.UUID,
    *,
    clerk_delete: ClerkDeleteCallable | None = None,
) -> ErasureResult:
    """Полный erasure pipeline для одного ``user_action_requests``-row.

    Caller (arq job или test) держит сессию, владеет commit/rollback'ом.

    Args:
        session: Открытая async-сессия с собственной транзакцией.
        request_id: UUID UserActionRequest. Должен иметь ``kind='erasure'``.
        clerk_delete: Override для тестов. ``None`` → :func:`default_clerk_delete`.

    Returns:
        :class:`ErasureResult` — sterile sumarry (counts, status), без PII.

    Raises:
        LookupError: row не существует.
        ValueError: row имеет ``kind != 'erasure'``.
        Любое неожиданное исключение re-raise'ится после записи
        ``status='failed'`` + audit ``ERASURE_FAILED``.
    """
    request = await _load_request_or_raise(session, request_id)
    user = await _load_user_or_raise(session, request.user_id)
    now = dt.datetime.now(dt.UTC)
    delete_callable = clerk_delete if clerk_delete is not None else default_clerk_delete

    # Idempotent early-return для terminal статусов.
    if request.status in ("done", "failed", "cancelled", "manual_intervention_required"):
        _LOG.info(
            "run_user_erasure: row %s already terminal (status=%s) — no-op",
            request.id,
            request.status,
        )
        return _result_from_terminal_row(request)

    try:
        # ---- 1. processing transition + audit ----
        request.status = "processing"
        request.error = None
        session.add(
            _build_user_action_audit(
                user_id=user.id,
                request_id=request.id,
                action=AuditAction.ERASURE_PROCESSING,
                metadata={"started_at": now.isoformat()},
                now=now,
            )
        )
        await session.flush()

        # ---- 2-4. edge checks ----
        block_reason = await _check_blockers(session, user_id=user.id)
        if block_reason is not None:
            return await _mark_blocked(
                session,
                request=request,
                user=user,
                reason=block_reason,
            )

        # ---- 5. cascade soft-delete domain entities (owned trees only) ----
        owned_tree_ids = await _list_solo_owned_tree_ids(session, user_id=user.id)
        soft_results = []
        for tree_id in owned_tree_ids:
            r = await cascade_soft_delete(
                session,
                tree_id=tree_id,
                deleted_by_user_id=user.id,
                erasure_request_id=request.id,
                reason="gdpr_erasure",
                now=now,
            )
            soft_results.append(r)
            # Soft-delete tree-row сам, чтобы UI не показывал его в дашборде.
            tree = (
                await session.execute(select(Tree).where(Tree.id == tree_id))
            ).scalar_one_or_none()
            if tree is not None and tree.deleted_at is None:
                tree.deleted_at = now
        await session.flush()

        soft_counts = _aggregate_soft_counts(soft_results)

        # ---- 6. hard-delete DNA ----
        dna_result = await hard_delete_dna_for_user(session, user_id=user.id)
        await session.flush()

        # ---- 7. ERASURE_COMPLETED audit (counts only; no PII) ----
        completed_at = dt.datetime.now(dt.UTC)
        audit_metadata: dict[str, Any] = {
            "completed_at": completed_at.isoformat(),
            "trees_processed": len(owned_tree_ids),
            "soft_deleted": soft_counts,
            "hard_deleted_dna": dna_result.counts,
        }
        session.add(
            _build_user_action_audit(
                user_id=user.id,
                request_id=request.id,
                action=AuditAction.ERASURE_COMPLETED,
                metadata=audit_metadata,
                now=completed_at,
            )
        )

        # ---- 8. Clerk delete (best-effort) ----
        clerk_deleted = False
        if user.clerk_user_id:
            clerk_deleted = await delete_callable(user.clerk_user_id)
            audit_metadata["clerk_deleted"] = clerk_deleted

        # ---- 9. email confirmation (idempotent on retry) ----
        email_key = f"erasure_confirmation:{request.id}"
        await send_transactional_email(
            kind=EmailKind.ERASURE_CONFIRMATION.value,
            recipient_user_id=user.id,
            idempotency_key=email_key,
            params={
                "trees_count": len(owned_tree_ids),
                "completed_at": completed_at.isoformat(),
            },
        )

        # ---- 10. finalize ----
        request.status = "done"
        request.processed_at = completed_at
        request.request_metadata = {
            **(request.request_metadata or {}),
            "completed_at": completed_at.isoformat(),
            "trees_processed": len(owned_tree_ids),
            "clerk_deleted": clerk_deleted,
            "email_idempotency_key": email_key,
        }
        # Soft-delete users-row себя — UI не должно показывать профиль
        # после erasure (даже если auth-cascade webhook ещё не пришёл).
        if user.deleted_at is None:
            user.deleted_at = completed_at
        await session.flush()

        return ErasureResult(
            request_id=request.id,
            status="done",
            persons_count=soft_counts.get("persons", 0),
            families_count=soft_counts.get("families", 0),
            events_count=soft_counts.get("events", 0),
            places_count=soft_counts.get("places", 0),
            sources_count=soft_counts.get("sources", 0),
            notes_count=soft_counts.get("notes", 0),
            multimedia_count=soft_counts.get("multimedia_objects", 0),
            citations_count=soft_counts.get("citations", 0),
            names_count=soft_counts.get("names", 0),
            dna_total=dna_result.total_rows,
            trees_processed=len(owned_tree_ids),
            email_idempotency_key=email_key,
            clerk_deleted=clerk_deleted,
        )

    except Exception as exc:
        # Failure path — пишем status=failed + audit, потом re-raise.
        try:
            failed_at = dt.datetime.now(dt.UTC)
            request.status = "failed"
            request.error = f"{type(exc).__name__}: {exc}"
            request.processed_at = failed_at
            session.add(
                _build_user_action_audit(
                    user_id=user.id,
                    request_id=request.id,
                    action=AuditAction.ERASURE_FAILED,
                    metadata={
                        "error_kind": type(exc).__name__,
                        "error_message": str(exc),
                        "failed_at": failed_at.isoformat(),
                    },
                    now=failed_at,
                )
            )
            await session.flush()
        except Exception:
            _LOG.exception("Failed to write ERASURE_FAILED audit for request %s", request_id)
        raise


# ---------------------------------------------------------------------------
# Edge-check helpers
# ---------------------------------------------------------------------------


async def _check_blockers(session: AsyncSession, *, user_id: uuid.UUID) -> str | None:
    """Run все edge-checks. Возвращает reason-string если блокирует, иначе None.

    Order не критичен — все проверки независимые. Возвращаем первый
    встречный блокер (короткий-circuit для эффективности).
    """
    # A. Shared trees: user owns trees с другими (non-owner) members.
    shared = await _has_shared_owned_trees(session, user_id=user_id)
    if shared:
        return "ownership transfer required (Phase 4.11c)"

    # B. Active export request: GDPR-rule: Art. 15 (export) и Art. 17 (erasure)
    # должны быть satisfied отдельно. Если export pending → дождаться.
    pending_export = await _has_pending_export(session, user_id=user_id)
    if pending_export:
        return "complete export request first"

    # C. Active subscription: hook на billing-service. Phase 4.11b — placeholder;
    # billing-service не deployed на этой фазе. Оставляем как no-op.
    return None


async def _has_shared_owned_trees(session: AsyncSession, *, user_id: uuid.UUID) -> bool:
    """True если user — OWNER хотя бы одного дерева с другим active member'ом."""
    owned_q = select(TreeMembership.tree_id).where(
        TreeMembership.user_id == user_id,
        TreeMembership.role == "owner",
        TreeMembership.revoked_at.is_(None),
    )
    owned_ids = (await session.execute(owned_q)).scalars().all()
    if not owned_ids:
        return False
    # Other-member count в любом из owned trees.
    other_q = (
        select(TreeMembership.id)
        .where(
            TreeMembership.tree_id.in_(owned_ids),
            TreeMembership.user_id != user_id,
            TreeMembership.revoked_at.is_(None),
        )
        .limit(1)
    )
    other = (await session.execute(other_q)).scalar_one_or_none()
    return other is not None


async def _has_pending_export(session: AsyncSession, *, user_id: uuid.UUID) -> bool:
    """True если есть export request в pending/processing."""
    q = (
        select(UserActionRequest.id)
        .where(
            UserActionRequest.user_id == user_id,
            UserActionRequest.kind == "export",
            UserActionRequest.status.in_(("pending", "processing")),
        )
        .limit(1)
    )
    row = (await session.execute(q)).scalar_one_or_none()
    return row is not None


async def _list_solo_owned_tree_ids(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Trees где user — OWNER и нет других active members.

    После _check_blockers уже отсекли shared-deree сценарий, но дублируем
    предикат для defensive-isolation: если invariant нарушится из-за race
    между check и применением soft-delete, мы не тронем tree с другими
    членами.
    """
    membership_ids = (
        (
            await session.execute(
                select(TreeMembership.tree_id).where(
                    TreeMembership.user_id == user_id,
                    TreeMembership.role == "owner",
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
    candidates: set[uuid.UUID] = {*membership_ids, *legacy_ids}
    if not candidates:
        return []

    # Filter: no other active members.
    others_q = select(TreeMembership.tree_id).where(
        TreeMembership.tree_id.in_(candidates),
        TreeMembership.user_id != user_id,
        TreeMembership.revoked_at.is_(None),
    )
    shared_ids = set((await session.execute(others_q)).scalars().all())
    return sorted(candidates - shared_ids)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _aggregate_soft_counts(results: list[Any]) -> dict[str, int]:
    """Sum counts across cascade_soft_delete results from all trees."""
    out: dict[str, int] = {}
    for r in results:
        for table, count in r.counts.items():
            out[table] = out.get(table, 0) + count
    return out


async def _mark_blocked(
    session: AsyncSession,
    *,
    request: UserActionRequest,
    user: User,
    reason: str,
) -> ErasureResult:
    """Перевести row в ``manual_intervention_required`` + audit."""
    blocked_at = dt.datetime.now(dt.UTC)
    request.status = "manual_intervention_required"
    request.error = reason
    request.processed_at = blocked_at
    session.add(
        _build_user_action_audit(
            user_id=user.id,
            request_id=request.id,
            action=AuditAction.ERASURE_BLOCKED,
            metadata={
                "reason": reason,
                "blocked_at": blocked_at.isoformat(),
            },
            now=blocked_at,
        )
    )
    await session.flush()
    return ErasureResult(
        request_id=request.id,
        status="manual_intervention_required",
        persons_count=0,
        families_count=0,
        events_count=0,
        places_count=0,
        sources_count=0,
        notes_count=0,
        multimedia_count=0,
        citations_count=0,
        names_count=0,
        dna_total=0,
        trees_processed=0,
        email_idempotency_key=None,
        clerk_deleted=False,
    )


def _result_from_terminal_row(request: UserActionRequest) -> ErasureResult:
    """Synthesize ErasureResult для idempotent re-call с уже terminal status."""
    metadata = request.request_metadata or {}
    return ErasureResult(
        request_id=request.id,
        status=request.status,
        persons_count=0,
        families_count=0,
        events_count=0,
        places_count=0,
        sources_count=0,
        notes_count=0,
        multimedia_count=0,
        citations_count=0,
        names_count=0,
        dna_total=0,
        trees_processed=int(metadata.get("trees_processed", 0)),
        email_idempotency_key=metadata.get("email_idempotency_key"),
        clerk_deleted=bool(metadata.get("clerk_deleted", False)),
    )


# ---------------------------------------------------------------------------
# Loaders + audit builder (mirror user_export_runner pattern)
# ---------------------------------------------------------------------------


async def _load_request_or_raise(session: AsyncSession, request_id: uuid.UUID) -> UserActionRequest:
    row = (
        await session.execute(select(UserActionRequest).where(UserActionRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        msg = f"UserActionRequest {request_id} not found"
        raise LookupError(msg)
    if row.kind != "erasure":
        msg = f"UserActionRequest {request_id} has kind={row.kind!r}, expected 'erasure'"
        raise ValueError(msg)
    return row


async def _load_user_or_raise(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        msg = f"User {user_id} not found (already erased?)"
        raise LookupError(msg)
    return user


def _build_user_action_audit(
    *,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    action: AuditAction,
    metadata: dict[str, Any],
    now: dt.datetime,
) -> AuditLog:
    """Сконструировать audit_log row для GDPR-erasure step.

    Конвенция (см. ADR-0046, ADR-0049):

    * ``tree_id = NULL`` — user-action.
    * ``entity_type = 'user_action_request'``, ``entity_id = request.id``.
    * ``actor_user_id = user_id`` (тот же user, что инициировал).
    * ``actor_kind = USER`` для request/processing/completed/blocked,
      ``SYSTEM`` для failed (system-side error).
    * ``diff`` хранит metadata-payload — counts / reason / timestamps.
      **Никаких PII** (user_id уже в actor_user_id; emails / display_names
      исключены сознательно — см. ADR-0049 §«Audit privacy»).
    """
    actor_kind = ActorKind.SYSTEM if action == AuditAction.ERASURE_FAILED else ActorKind.USER
    return AuditLog(
        id=new_uuid(),
        tree_id=None,
        entity_type="user_action_request",
        entity_id=request_id,
        action=action.value,
        actor_user_id=user_id,
        actor_kind=actor_kind.value,
        import_job_id=None,
        reason=None,
        diff={"action": action.value, "metadata": metadata},
        created_at=now,
    )


__all__ = [
    "ClerkDeleteCallable",
    "ErasureResult",
    "default_clerk_delete",
    "run_user_erasure",
]
