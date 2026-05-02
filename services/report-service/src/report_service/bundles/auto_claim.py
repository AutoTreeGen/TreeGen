"""Auto-derive ``claimed_relationship`` для одной пары (Phase 24.4).

Если caller не указал claim в bundle-input, worker запускает 3 direct-claim
резолвера (parent_child / sibling / spouse) — если ровно один найден,
используем его; если ноль — :class:`AutoClaimUnresolvableError`; если больше
одного (теоретически возможно для half-sibling: persons могут быть и
sibling, и parent_child одновременно если структура странная) — приоритет
``parent_child > spouse > sibling``.

Cousin / grandparent / aunt-uncle не auto-derivable из Family/FamilyChild
(нужно chain через intermediate generations) — эти claim'ы должны быть
явно указаны caller'ом, иначе bundle endpoint дёргает 422.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from report_service.relationship.data import resolve_relationship_families
from report_service.relationship.models import ClaimedRelationship

# Приоритет если несколько direct-claim'ов резолвятся одновременно.
# Биологически parent_child строго subsumes sibling (нельзя быть и sib и parent),
# но ORM не enforce'ит — handle defensively.
_PRIORITY: tuple[ClaimedRelationship, ...] = (
    ClaimedRelationship.PARENT_CHILD,
    ClaimedRelationship.SPOUSE,
    ClaimedRelationship.SIBLING,
)


class AutoClaimUnresolvableError(LookupError):
    """Поднимается, когда auto-derive не нашёл ни одной direct-связи.

    Caller (bundle endpoint) маппит в HTTP 422 с подсказкой
    «specify claimed_relationship explicitly for pair (a, b)».
    """


async def auto_derive_claim(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> ClaimedRelationship:
    """Вернуть direct claim, найденный в Family/FamilyChild.

    Raises:
        AutoClaimUnresolvableError: ни одна direct-связь не найдена.
    """
    matches: list[ClaimedRelationship] = []
    for candidate in _PRIORITY:
        families = await resolve_relationship_families(
            session,
            tree_id=tree_id,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
            claim=candidate,
        )
        if families:
            matches.append(candidate)
    if not matches:
        msg = (
            f"No direct (parent_child/sibling/spouse) relationship found "
            f"between {person_a_id} and {person_b_id} in tree {tree_id}; "
            "specify claimed_relationship explicitly."
        )
        raise AutoClaimUnresolvableError(msg)
    # Priority order = _PRIORITY tuple iteration order; matches preserves it.
    return matches[0]


__all__ = ["AutoClaimUnresolvableError", "auto_derive_claim"]
