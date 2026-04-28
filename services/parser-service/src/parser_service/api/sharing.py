"""Sharing API — приглашения и членства (Phase 11.0).

См. ADR-0036 «Sharing & permissions model».

Эндпоинты:

* ``POST   /trees/{tree_id}/invitations``           — owner создаёт invite (email + role).
* ``GET    /trees/{tree_id}/invitations``           — owner смотрит свой list.
* ``DELETE /invitations/{invitation_id}``           — owner revoke'ит pending invite.
* ``POST   /invitations/{token}/accept``            — invitee accept'ит → новый Membership.
* ``GET    /trees/{tree_id}/members``               — owner смотрит active memberships.
* ``PATCH  /memberships/{membership_id}``           — owner меняет role (только non-OWNER).
* ``DELETE /memberships/{membership_id}``           — owner revoke'ит access.

Permission contract:

* OWNER endpoints (``POST/GET /invitations``, list/patch/delete members,
  delete invitation) — gated через :func:`require_tree_role(TreeRole.OWNER)`.
* Accept endpoint — НЕ требует существующего членства; user может быть приглашён
  без предварительного access'а к дереву. Аутентификация (``current_user``) всё
  равно нужна, чтобы привязать accept к конкретному пользователю.

Privacy: invitee_email возвращается только в ответах для OWNER (через
``InvitationResponse``); accept-flow не утечкает email (только tree_id +
membership). Audit-trail приглашений будет добавлен в Phase 11.1 (отдельный
endpoint, sharing-history view).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models import TreeRole, role_satisfies
from shared_models.orm import TreeInvitation, TreeMembership, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import get_current_user
from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.schemas import (
    InvitationAcceptResponse,
    InvitationCreateRequest,
    InvitationListResponse,
    InvitationResponse,
    MemberListResponse,
    MemberResponse,
    MemberRoleUpdateRequest,
)
from parser_service.services.permissions import (
    check_tree_permission,
    require_tree_role,
)

router = APIRouter()


# ---- helpers --------------------------------------------------------------


def _build_invite_url(settings: Settings, token: uuid.UUID) -> str:
    """Собрать shareable URL: ``${public_base_url}/invitations/{token}``."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/invitations/{token}"


def _to_invitation_response(inv: TreeInvitation, *, settings: Settings) -> InvitationResponse:
    """ORM → DTO с готовым invite_url."""
    return InvitationResponse(
        id=inv.id,
        tree_id=inv.tree_id,
        invitee_email=inv.invitee_email,
        role=inv.role,
        token=inv.token,
        invite_url=_build_invite_url(settings, inv.token),
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        revoked_at=inv.revoked_at,
        created_at=inv.created_at,
    )


def _normalize_email(raw: str) -> str:
    """Lowercase + trim. Минимальная нормализация для consistency."""
    return raw.strip().lower()


# ---- POST /trees/{tree_id}/invitations -----------------------------------


@router.post(
    "/trees/{tree_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Owner-only — создать приглашение по email",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def create_invitation(
    tree_id: uuid.UUID,
    body: InvitationCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
) -> InvitationResponse:
    """Создать invitation. Owner-only.

    Token генерируется DB (``gen_random_uuid()``) при INSERT, сразу возвращаем
    готовый ``invite_url``. TTL = ``settings.invitation_ttl_days``.

    Идемпотентность не реализуется здесь — два POST'а с тем же email создадут
    два invitation'а. UI пусть фильтрует pending до отправки.
    """
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=settings.invitation_ttl_days)
    invitation = TreeInvitation(
        tree_id=tree_id,
        inviter_user_id=user.id,
        invitee_email=_normalize_email(body.email),
        role=body.role,
        expires_at=expires_at,
    )
    session.add(invitation)
    await session.flush()
    await session.refresh(invitation)
    # `get_session` auto-commit'ит после yield — здесь явный commit не нужен.
    return _to_invitation_response(invitation, settings=settings)


# ---- GET /trees/{tree_id}/invitations ------------------------------------


@router.get(
    "/trees/{tree_id}/invitations",
    response_model=InvitationListResponse,
    summary="Owner-only — pending + recent invitations",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def list_invitations(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvitationListResponse:
    """Список приглашений (включая accepted и revoked для аудита).

    Сортировка — created_at DESC, последние сверху.
    """
    res = await session.execute(
        select(TreeInvitation)
        .where(TreeInvitation.tree_id == tree_id)
        .order_by(TreeInvitation.created_at.desc())
    )
    items = [_to_invitation_response(inv, settings=settings) for inv in res.scalars().all()]
    return InvitationListResponse(tree_id=tree_id, items=items)


# ---- DELETE /invitations/{invitation_id} ---------------------------------


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Owner-only — revoke pending invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Soft-revoke invitation. 404 если не существует, 403 если caller не OWNER дерева.

    Уже accepted invitation revoke не имеет смысла — возвращаем 409 (создавайте
    membership-revoke вместо). Уже revoked → 204 идемпотентно.
    """
    invitation = await session.get(TreeInvitation, invitation_id)
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invitation {invitation_id} not found",
        )

    # Manual permission check — gate factory работает только когда tree_id в path.
    is_owner = await check_tree_permission(
        session,
        user_id=user.id,
        tree_id=invitation.tree_id,
        required=TreeRole.OWNER,
    )
    if not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tree OWNER can revoke invitations",
        )

    if invitation.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Invitation already accepted — revoke the resulting membership instead"),
        )

    if invitation.revoked_at is None:
        invitation.revoked_at = dt.datetime.now(dt.UTC)
        invitation.revoked_by_user_id = user.id
        await session.flush()


# ---- POST /invitations/{token}/accept ------------------------------------


@router.post(
    "/invitations/{token}/accept",
    response_model=InvitationAcceptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Accept invitation — создаёт membership для текущего user'а",
)
async def accept_invitation(
    token: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> InvitationAcceptResponse:
    """Идемпотентный accept.

    Возможные исходы:

    * Invitation не найден → 404.
    * Revoked → 410 Gone.
    * Expired → 410 Gone.
    * Already accepted этим же user'ом → 200 (идемпотент, возвращает существующий membership).
    * Already accepted другим user'ом → 409.
    * Happy path → 201, новый Membership с role из invitation, accepted_at=now().

    Email match (invitee_email == user.email) НЕ enforce'им — спец говорит
    «sign up to accept», т.е. user может зарегистрироваться под любым email
    и accept'нуть. ADR-0036 §security обсуждает trade-off.
    """
    res = await session.execute(select(TreeInvitation).where(TreeInvitation.token == token))
    invitation = res.scalar_one_or_none()
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    if invitation.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invitation has been revoked",
        )

    now = dt.datetime.now(dt.UTC)
    if invitation.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invitation has expired",
        )

    # Idempotency: уже accepted'ный invitation тем же user'ом → достаём membership и возвращаем.
    if invitation.accepted_at is not None:
        if invitation.accepted_by_user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invitation already accepted by another user",
            )
        existing = await session.execute(
            select(TreeMembership).where(
                TreeMembership.tree_id == invitation.tree_id,
                TreeMembership.user_id == user.id,
                TreeMembership.revoked_at.is_(None),
            )
        )
        membership = existing.scalar_one_or_none()
        if membership is None:
            # Странный state — accepted, но membership ушёл (revoked отдельно).
            # Чтобы поведение было предсказуемым: 410, не ре-создаём.
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Invitation accepted but membership has been revoked",
            )
        return InvitationAcceptResponse(
            tree_id=invitation.tree_id,
            membership_id=membership.id,
            role=membership.role,
        )

    # Уже есть active membership (например, OWNER или accept'нул через
    # другой invite раньше) — accept'аем invitation, но второй membership не создаём.
    existing_res = await session.execute(
        select(TreeMembership).where(
            TreeMembership.tree_id == invitation.tree_id,
            TreeMembership.user_id == user.id,
            TreeMembership.revoked_at.is_(None),
        )
    )
    existing_membership = existing_res.scalar_one_or_none()

    invitation.accepted_at = now
    invitation.accepted_by_user_id = user.id

    if existing_membership is not None:
        # Принимаем invitation, но не апгрейдим/дегрейдим роль автоматически.
        # Owner = owner, никаких суррогатов.
        await session.flush()
        return InvitationAcceptResponse(
            tree_id=invitation.tree_id,
            membership_id=existing_membership.id,
            role=existing_membership.role,
        )

    membership = TreeMembership(
        tree_id=invitation.tree_id,
        user_id=user.id,
        role=invitation.role,
        invited_by=invitation.inviter_user_id,
        accepted_at=now,
    )
    session.add(membership)
    await session.flush()
    await session.refresh(membership)

    return InvitationAcceptResponse(
        tree_id=invitation.tree_id,
        membership_id=membership.id,
        role=membership.role,
    )


# ---- GET /trees/{tree_id}/members ----------------------------------------


@router.get(
    "/trees/{tree_id}/members",
    response_model=MemberListResponse,
    summary="Owner-only — active memberships дерева",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def list_members(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberListResponse:
    """Список active memberships (revoked_at IS NULL).

    JOIN на ``users`` чтобы отдать email + display_name за один round-trip.
    Сортировка: OWNER первым, потом EDITOR, потом VIEWER, внутри — по joined.
    """
    rows = await session.execute(
        select(TreeMembership, User)
        .join(User, User.id == TreeMembership.user_id)
        .where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.revoked_at.is_(None),
        )
    )
    role_order = {TreeRole.OWNER.value: 0, TreeRole.EDITOR.value: 1, TreeRole.VIEWER.value: 2}
    pairs = list(rows.all())
    pairs.sort(
        key=lambda pair: (
            role_order.get(pair[0].role, 99),
            (pair[0].accepted_at or pair[0].created_at),
        )
    )

    items = [
        MemberResponse(
            id=membership.id,
            user_id=membership.user_id,
            email=user_obj.email,
            display_name=user_obj.display_name,
            role=membership.role,
            invited_by=membership.invited_by,
            joined_at=membership.accepted_at or membership.created_at,
            revoked_at=membership.revoked_at,
        )
        for membership, user_obj in pairs
    ]
    return MemberListResponse(tree_id=tree_id, items=items)


# ---- PATCH /memberships/{membership_id} ----------------------------------


@router.patch(
    "/memberships/{membership_id}",
    response_model=MemberResponse,
    summary="Owner-only — change role (editor↔viewer; OWNER transfer — Phase 11.1)",
)
async def update_member_role(
    membership_id: uuid.UUID,
    body: MemberRoleUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> MemberResponse:
    """Менять роль can только OWNER дерева.

    Запрещено:

    * Менять роль самому себе если сам OWNER (нужен сначала transfer — будет в Phase 11.1).
    * Менять роль OWNER-membership через этот endpoint (DB partial unique
      будет stop'нуть, но мы хотим явный 409 с понятной ошибкой).
    """
    membership = await session.get(TreeMembership, membership_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Membership {membership_id} not found",
        )

    is_owner = await check_tree_permission(
        session,
        user_id=user.id,
        tree_id=membership.tree_id,
        required=TreeRole.OWNER,
    )
    if not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tree OWNER can change member roles",
        )

    if role_satisfies(membership.role, TreeRole.OWNER):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot demote OWNER directly — transfer ownership to another "
                "member first (Phase 11.1)."
            ),
        )

    if membership.user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot change your own role; transfer ownership first.",
        )

    membership.role = body.role
    await session.flush()
    await session.refresh(membership)

    user_obj = await session.get(User, membership.user_id)
    if user_obj is None:  # pragma: no cover — RESTRICT FK защищает
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Membership user vanished",
        )
    return MemberResponse(
        id=membership.id,
        user_id=membership.user_id,
        email=user_obj.email,
        display_name=user_obj.display_name,
        role=membership.role,
        invited_by=membership.invited_by,
        joined_at=membership.accepted_at or membership.created_at,
        revoked_at=membership.revoked_at,
    )


# ---- DELETE /memberships/{membership_id} ---------------------------------


@router.delete(
    "/memberships/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Owner-only — revoke membership (soft)",
)
async def revoke_member(
    membership_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Soft-revoke. Запрещено revoke'ить OWNER-membership (нужен transfer).

    Owner может revoke'ить себя если он не OWNER (например, EDITOR покидает
    чужое дерево) — но это ситуация, которой Phase 11.0 практически не рождает,
    так что не специально.
    """
    membership = await session.get(TreeMembership, membership_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Membership {membership_id} not found",
        )

    is_owner = await check_tree_permission(
        session,
        user_id=user.id,
        tree_id=membership.tree_id,
        required=TreeRole.OWNER,
    )
    if not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tree OWNER can revoke memberships",
        )

    if role_satisfies(membership.role, TreeRole.OWNER):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot revoke OWNER membership — transfer ownership to another "
                "member first (Phase 11.1)."
            ),
        )

    if membership.revoked_at is None:
        membership.revoked_at = dt.datetime.now(dt.UTC)
        await session.flush()
