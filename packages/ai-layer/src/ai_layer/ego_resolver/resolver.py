"""Public entry-point ego_resolver'а: :func:`resolve_reference`.

Pipeline:

1. :func:`grammar.parse_reference` → :class:`ParsedReference`
   (relationship-path + опциональный name_tail).
2. Если path не пустой — :func:`walker.walk_path` от anchor'а возвращает
   set кандидатов; иначе set = все persons (для pure-name resolve).
3. Если name_tail не пустой — :func:`name_index.search_names` ранжирует
   кандидатов по name-match'у. Иначе — кандидаты остаются равноправными
   (структурный hit, score=1.0).
4. Пустой результат → ``None``. Иначе — :class:`ResolvedPerson` с
   ``confidence`` и ``alternatives`` (top-3 минус выбранный top-1).

Confidence rules:

* 1 candidate, structural-only path → 1.0 (unambiguous direct match).
* 1 candidate, name-based exact match → 1.0 (NameMatcher exact-tier).
* 1 candidate, name-based fuzzy/translit → ``NameMatcher.score`` (<1.0).
* N candidates → top-score ``/ N`` (ambiguity penalty).

Возвращаем хотя бы какой-то match чтобы UI мог показать confirmation
prompt со списком alternatives — это полезнее чем ``None`` (см. ROADMAP
§10.7d).
"""

from __future__ import annotations

import uuid

from ai_layer.ego_resolver.grammar import parse_reference
from ai_layer.ego_resolver.name_index import NameHit, search_names
from ai_layer.ego_resolver.types import (
    PersonNames,
    RelStep,
    ResolvedPerson,
    TreeContext,
)
from ai_layer.ego_resolver.walker import walk_path

# Top-N alternatives'ов в response'е (top-3 включая выбранный → 2 в alternatives).
# Ограничиваем чтобы UI mock'и оставались компактными; все candidates всё
# равно доступны caller'у через repeat-call с уточнённым name_tail'ом.
_TOP_K = 3


def _structural_hits(
    candidates: set[uuid.UUID],
    persons: dict[uuid.UUID, PersonNames] | None,  # noqa: ARG001
) -> list[NameHit]:
    """Превращает структурные кандидаты в NameHit'ы со score=1.0.

    Используется когда parsed-reference содержит path но не name_tail —
    каждая персона из path-кандидатов одинаково вероятна, score=1.0.
    Сортировка стабильная по UUID-string'у — для воспроизводимости тестов.
    """
    return sorted(
        (NameHit(person_id=pid, score=1.0, matched="") for pid in candidates),
        key=lambda h: (-h.score, str(h.person_id)),
    )


def _build_resolved(
    hits: list[NameHit],
    path: tuple[RelStep, ...],
) -> ResolvedPerson | None:
    """Из ranked NameHit'ов собирает ResolvedPerson с confidence-penalty.

    Returns:
        ``None`` если ``hits`` пустой; иначе top-1 + до ``_TOP_K - 1``
        альтернатив. Confidence = ``top.score`` для unique-кандидата;
        ``top.score / len(hits)`` для ambiguous (penalty линеен в number
        of equally-likely candidates).
    """
    if not hits:
        return None

    top = hits[0]
    n = len(hits)
    confidence = top.score if n == 1 else top.score / n

    alternatives = tuple(
        ResolvedPerson(
            person_id=h.person_id,
            confidence=h.score / n,
            path=path,
            alternatives=(),
        )
        for h in hits[1:_TOP_K]
    )

    return ResolvedPerson(
        person_id=top.person_id,
        confidence=confidence,
        path=path,
        alternatives=alternatives,
    )


def resolve_reference(
    tree: TreeContext,
    anchor_person_id: uuid.UUID,
    reference: str,
) -> ResolvedPerson | None:
    """Резолвит free-text relative-reference в :class:`ResolvedPerson`.

    Args:
        tree: Snapshot структуры дерева + name-records (см. ``TreeContext``).
        anchor_person_id: Ego — обычно ``trees.owner_person_id`` из 10.7a.
            Используется как стартовая точка walker'а для relationship-
            path'ов; для pure-name lookup'а не нужен (но мы всё равно
            требуем anchor для consistency и future context-window инференции).
        reference: Free-text — «my wife», «брат матери жены», «Dvora»,
            «my wife's mother Olga», «moja zhena».

    Returns:
        :class:`ResolvedPerson` с top-1 hit + до 2 альтернативами; ``None``
        если ни один кандидат не подобран (пустое дерево / нерелевантный
        reference / disconnected anchor).

    Examples:
        >>> resolve_reference(tree, owner.id, "my wife").person_id
        wife.id
        >>> resolve_reference(tree, owner.id, "my mother's brother").person_id
        uncle.id
        >>> resolve_reference(tree, owner.id, "Dvora").person_id
        dvora.id
        >>> resolve_reference(tree, owner.id, "moja zhena").person_id
        wife.id
    """
    parsed = parse_reference(reference)

    persons_dict = dict(tree.persons)

    if parsed.path:
        candidates = walk_path(anchor_person_id, parsed.path, tree.traversal)
        if not candidates:
            return None
    else:
        # Pure-name lookup — ищем по всему дереву (ego не включаем чтобы
        # «my» / «mine» self-reference не возвращало pure-name search'у).
        candidates = set(persons_dict.keys()) - {anchor_person_id}
        if not candidates:
            return None

    if parsed.name_tail:
        hits = search_names(persons_dict, parsed.name_tail, candidates=candidates)
    else:
        hits = _structural_hits(candidates, persons_dict)

    return _build_resolved(hits, parsed.path)


__all__ = ["resolve_reference"]
