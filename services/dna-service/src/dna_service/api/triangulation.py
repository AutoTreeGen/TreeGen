"""Phase 6.4 — triangulation endpoint (см. ADR-0054).

``GET /trees/{tree_id}/triangulation?min_overlap_cm=7.0`` — compute-on-demand
триангуляция всех DNA matches дерева. Compute-only: ничего не пишется в БД,
результат кэшируется в Redis на 1 час.

Permission gate: ``require_tree_role(TreeRole.VIEWER)`` — любой active member
дерева может смотреть triangulation (это не утечка privacy: matches уже
видимы через ``GET /dna-kits/{id}/matches`` для тех же ролей).

Privacy: ответ содержит только match.id'шники + cM-координаты + chromosome.
Никаких rsid / genotypes / display_name (UI делает отдельный fetch на
``GET /dna-matches/{id}`` для метаданных).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Final

from dna_analysis import (
    Match,
    TriangulationSegment,
    find_triangulation_groups,
)
from fastapi import APIRouter, Depends, Query
from shared_models import TreeRole
from shared_models.orm import DnaKit, DnaMatch, SharedMatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.database import get_session
from dna_service.schemas import TriangulationGroupItem, TriangulationListResponse
from dna_service.services.cache import CacheBackend, get_cache
from dna_service.services.permissions import require_tree_role

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)

# Cache TTL: 1 час. Обоснование — ADR-0054 §«Caching strategy»:
# match-list меняется редко (импорт раз в дни/недели), а compute group'ов
# с десятками matches — заметная latency. 1ч — sweet spot между freshness
# и amortized compute cost.
_CACHE_TTL_SECONDS: Final[int] = 3600

# Privacy: ключ строится из tree_id (uuid) + min_overlap_cm; namespace
# отделяет от других возможных consumer'ов Redis в dna-service.
_CACHE_NAMESPACE: Final[str] = "dna:triangulation"


def _cache_key(tree_id: uuid.UUID, min_overlap_cm: float) -> str:
    """Стабильный ключ Redis для пары (tree, min_overlap_cm)."""
    return f"{_CACHE_NAMESPACE}:{tree_id}:{min_overlap_cm:.2f}"


def _segment_from_provenance(entry: dict[str, Any]) -> TriangulationSegment | None:
    """Извлекает один сегмент из provenance jsonb-записи.

    Phase 6.4 contract: provenance.segments[i] должен содержать
    ``chromosome``, ``start_cm``, ``end_cm``. Записи без cM-координат
    (legacy bp-only сегменты до Phase 6.4) пропускаются — они не
    участвуют в триангуляции, но и не ломают endpoint.

    Возвращает ``None`` для невалидных/частичных записей.
    """
    if not isinstance(entry, dict):
        return None
    try:
        chromosome = int(entry["chromosome"])
        start_cm = float(entry["start_cm"])
        end_cm = float(entry["end_cm"])
    except (KeyError, TypeError, ValueError):
        return None
    if chromosome < 1 or chromosome > 22:
        return None
    if end_cm <= start_cm:
        return None
    return TriangulationSegment(chromosome=chromosome, start_cm=start_cm, end_cm=end_cm)


def _segments_from_match(match: DnaMatch) -> tuple[TriangulationSegment, ...]:
    """Все валидные cM-сегменты match'а из provenance jsonb."""
    raw = match.provenance.get("segments") if isinstance(match.provenance, dict) else None
    if not isinstance(raw, list):
        return ()
    out: list[TriangulationSegment] = []
    for entry in raw:
        seg = _segment_from_provenance(entry)
        if seg is not None:
            out.append(seg)
    return tuple(out)


async def _load_tree_matches(
    session: AsyncSession,
    tree_id: uuid.UUID,
) -> list[Match]:
    """Загружает все matches дерева с их segments и shared-match relation.

    Возвращает list[Match] для прямой передачи в
    :func:`dna_analysis.find_triangulation_groups`. ``match_id`` — строковое
    представление DnaMatch.id (UUID hex), для роутера на serialization.

    Soft-deleted matches и matches с deleted kit'ом исключаются (privacy
    + consistency с ``GET /dna-matches/{id}``).
    """
    rows = (
        (
            await session.execute(
                select(DnaMatch)
                .join(DnaKit, DnaKit.id == DnaMatch.kit_id)
                .where(
                    DnaMatch.tree_id == tree_id,
                    DnaMatch.deleted_at.is_(None),
                    DnaKit.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return []

    visible_ids = {m.id for m in rows}

    shared_rows = (
        await session.execute(
            select(SharedMatch.match_a_id, SharedMatch.match_b_id).where(
                SharedMatch.tree_id == tree_id
            )
        )
    ).all()

    # Симметризация: SharedMatch хранит каждую пару один раз
    # (match_a_id < match_b_id, см. shared_match.py CHECK), но для
    # триангуляции нужны оба направления.
    shared: dict[uuid.UUID, set[uuid.UUID]] = {mid: set() for mid in visible_ids}
    for a, b in shared_rows:
        if a not in visible_ids or b not in visible_ids:
            # ON DELETE CASCADE гарантирует FK consistency, но матч
            # с soft-deleted kit'ом всё ещё в shared_matches — не учитываем.
            continue
        shared[a].add(b)
        shared[b].add(a)

    matches: list[Match] = []
    for orm_match in rows:
        segments = _segments_from_match(orm_match)
        shared_ids = frozenset(str(other_id) for other_id in shared.get(orm_match.id, ()))
        matches.append(
            Match(
                match_id=str(orm_match.id),
                segments=segments,
                shared_match_ids=shared_ids,
                # Phase 6.4 не делает MRCA-резолюцию из дерева; это Phase 7.5+.
                has_known_mrca=orm_match.matched_person_id is not None,
            )
        )

    _LOG.debug(
        "triangulation: loaded %d matches with %d shared-match edges for tree=%s",
        len(matches),
        len(shared_rows),
        tree_id,
    )
    return matches


@router.get(
    "/trees/{tree_id}/triangulation",
    response_model=TriangulationListResponse,
    tags=["triangulation"],
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_tree_triangulation(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    min_overlap_cm: Annotated[float, Query(ge=1.0, le=50.0)] = 7.0,
) -> TriangulationListResponse:
    """Compute on-demand triangulation для всех DNA matches дерева.

    Phase 6.4 contract: compute-only. Результат не пишется в БД,
    кэшируется в Redis (если ``DNA_SERVICE_REDIS_URL`` сконфигурирован)
    на 1 час по ключу ``dna:triangulation:{tree_id}:{min_overlap_cm}``.

    Args:
        tree_id: Path-параметр; permission gate выше проверяет VIEWER+.
        min_overlap_cm: Порог overlap для триангуляции в cM
            (default 7.0 cM, см. ADR-0014 §default thresholds).
            Range 1..50 cM защищает от deg cases.

    Returns:
        :class:`TriangulationListResponse` с полем ``groups``,
        отсортированным по ``(chromosome, start_cm)``.
    """
    cache_key = _cache_key(tree_id, min_overlap_cm)

    cached = await cache.get(cache_key)
    if cached is not None:
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8")
        return TriangulationListResponse.model_validate_json(cached)

    matches = await _load_tree_matches(session, tree_id)
    groups = find_triangulation_groups(matches, min_overlap_cm=min_overlap_cm)

    items = [
        TriangulationGroupItem(
            chromosome=g.chromosome,
            start_cm=g.start_cm,
            end_cm=g.end_cm,
            members=[uuid.UUID(mid) for mid in g.members],
            confidence_boost=g.confidence_boost,
        )
        for g in groups
    ]
    response = TriangulationListResponse(
        tree_id=tree_id,
        min_overlap_cm=min_overlap_cm,
        groups=items,
    )

    # Cache write — best-effort; ошибки Redis не должны валить запрос
    # (компромисс stateless cache: cache miss дороже, но не fatal).
    try:
        await cache.setex(cache_key, _CACHE_TTL_SECONDS, response.model_dump_json())
    except Exception:
        _LOG.warning("triangulation cache write failed for tree=%s", tree_id, exc_info=True)

    return response
