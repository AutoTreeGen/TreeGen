"""Self-anchor + ego-relationship API (Phase 10.7a / ADR-0068).

Endpoints:

* ``PATCH /trees/{tree_id}/owner-person`` — OWNER-only, set/clear self-anchor.
* ``GET   /trees/{tree_id}/relationships/{person_id}?language=en`` — VIEWER+,
  возвращает kind/degree/via/twin-flag + локализованный label для пары
  ``(tree.owner_person_id, person_id)``.

Permission contract: setting self-anchor — owner-level decision (только
владелец может объявить «вот я» в дереве). Reading relationships — VIEWER:
само по себе родство — derived data, видно тем, кто видит persons.

URL ``/trees/{tree_id}/relationships/{person_id}`` имеет ровно 4 path-
сегмента после ``/trees/``; параллельный routing на 6-сегментный
``/trees/{tree_id}/relationships/{kind}/{subject_id}/{object_id}/evidence``
(Phase 15.1, ADR-0058) разрешается FastAPI'ем по числу сегментов —
collision'а нет.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from inference_engine.ego_relations import (
    NoPathError,
    humanize,
    relate,
)
from shared_models import TreeRole
from shared_models.orm import Person, Tree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import (
    RelationshipPathPayload,
    RelationshipResponse,
    TreeOwnerPersonRequest,
    TreeOwnerPersonResponse,
)
from parser_service.services.ego_traversal import load_family_traversal
from parser_service.services.permissions import require_tree_role

router = APIRouter()


@router.get(
    "/trees/{tree_id}/owner-person",
    response_model=TreeOwnerPersonResponse,
    summary="Read current self-anchor for this tree (VIEWER+).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_owner_person(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TreeOwnerPersonResponse:
    """Текущее значение ``trees.owner_person_id`` (или null)."""
    tree = await session.get(Tree, tree_id)
    if tree is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found",
        )
    return TreeOwnerPersonResponse(
        tree_id=tree.id,
        owner_person_id=tree.owner_person_id,
    )


@router.patch(
    "/trees/{tree_id}/owner-person",
    response_model=TreeOwnerPersonResponse,
    summary="Owner-only — set or clear the self-anchor person for this tree.",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def set_owner_person(
    tree_id: uuid.UUID,
    body: TreeOwnerPersonRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TreeOwnerPersonResponse:
    """Установить или сбросить self-anchor дерева.

    Валидация: если ``body.person_id`` не ``None`` — он должен принадлежать
    этому дереву (NOT 404 на person, а 422 — невалидный bind для anchor'а).

    Идемпотентно: повторный PATCH с тем же ``person_id`` — no-op для DB
    (UPDATE с тем же значением SQLAlchemy всё равно выполнит, но
    наблюдаемого эффекта нет).
    """
    tree = await session.get(Tree, tree_id)
    if tree is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found",
        )

    if body.person_id is not None:
        # Person должен существовать в этом дереве и не быть soft-deleted.
        person_tree_id = await session.scalar(
            select(Person.tree_id).where(
                Person.id == body.person_id,
                Person.deleted_at.is_(None),
            )
        )
        if person_tree_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Person {body.person_id} not found",
            )
        if person_tree_id != tree_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Person {body.person_id} belongs to a different tree "
                    f"and cannot anchor tree {tree_id}"
                ),
            )

    tree.owner_person_id = body.person_id
    await session.flush()

    return TreeOwnerPersonResponse(
        tree_id=tree.id,
        owner_person_id=tree.owner_person_id,
    )


_SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ru", "he", "nl", "de")


@router.get(
    "/trees/{tree_id}/relationships/{person_id}",
    response_model=RelationshipResponse,
    summary="Resolve relationship from tree's self-anchor (ego) to person_id.",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_relationship_to_ego(
    tree_id: uuid.UUID,
    person_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    language: Annotated[
        Literal["en", "ru", "he", "nl", "de"],
        Query(description="Язык для humanize-метки. Поддержано: en/ru/he/nl/de."),
    ] = "en",
) -> RelationshipResponse:
    """Вернуть родство ``(tree.owner_person_id → person_id)``.

    Status codes:

    * 200 — путь найден, payload содержит kind/degree/via/twin-flag + label.
    * 404 — ``person_id`` не существует/soft-deleted в этом дереве, или
      между ego и target нет пути (disconnected components в дереве).
    * 409 — ``tree.owner_person_id`` is null: до использования эго-резолвера
      владелец должен явно set'нуть self-anchor через PATCH.
    """
    tree = await session.get(Tree, tree_id)
    # require_tree_role уже отдал 404 если дерева нет; здесь tree гарантированно
    # есть, но добавляем guard чтобы тип был не Optional.
    if tree is None:  # pragma: no cover — gate уже проверил
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tree not found")

    if tree.owner_person_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Tree {tree_id} has no self-anchor; "
                f"PATCH /trees/{tree_id}/owner-person before resolving relationships."
            ),
        )

    # Target person существует в этом дереве?
    target_tree_id = await session.scalar(
        select(Person.tree_id).where(
            Person.id == person_id,
            Person.deleted_at.is_(None),
        )
    )
    if target_tree_id is None or target_tree_id != tree_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {person_id} not found in tree {tree_id}",
        )

    traversal = await load_family_traversal(session, tree_id=tree_id)

    try:
        path = relate(tree.owner_person_id, person_id, tree=traversal)
    except NoPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    label = humanize(path, language)

    return RelationshipResponse(
        tree_id=tree_id,
        from_person_id=tree.owner_person_id,
        to_person_id=person_id,
        language=language,
        path=RelationshipPathPayload(
            kind=path.kind,
            degree=path.degree,
            via=path.via,
            is_twin=path.is_twin,
            blood_relation=path.blood_relation,
        ),
        label=label,
    )


__all__ = ["_SUPPORTED_LANGUAGES", "router"]
