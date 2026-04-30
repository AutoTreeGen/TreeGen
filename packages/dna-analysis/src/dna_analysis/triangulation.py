"""DNA triangulation engine (Phase 6.4 / ADR-0054).

Triangulation — это situation, когда **три человека делят IBD-сегмент в одном
и том же месте на одной хромосоме**. Если kit-owner совпадает с двумя своими
matches A и B на пересекающемся участке, и A с B сами тоже matches между
собой (shared match), это сильный сигнал, что A, B и owner унаследовали
этот фрагмент от общего предка (MRCA).

Phase 6.4 поставляет compute-only часть:

* :class:`Match`, :class:`TriangulationSegment`, :class:`TriangulationGroup` —
  Pydantic-модели входа/выхода. БД-агностичны: вызывающий код (dna-service)
  сам резолвит ORM → Match.
* :func:`find_triangulation_groups` — находит группы matches, у которых
  IBD-сегменты пересекаются на одной хромосоме на ≥ ``min_overlap_cm`` cM
  И которые попарно являются shared matches (mutual relation).
* :func:`bayes_boost` — простой множитель уверенности для same-person
  гипотезы по характеристикам группы. Полная Bayes-модель (с tree prior)
  — Phase 7.5 / ADR-0023.

Алгоритм (детально — ADR-0054 §«Decision»):

1. Перебор всех пар matches (A, B), где ``B.match_id ∈ A.shared_match_ids``
   и наоборот (mutual). Для каждой пары — все пары сегментов на той же
   хромосоме; пересечение ≥ ``min_overlap_cm`` создаёт «триплет»
   (chrom, overlap_start_cm, overlap_end_cm, {A, B}).
2. Триплеты на одной хромосоме объединяются в группы через union-find:
   два триплета сливаются, если они делят хотя бы одного member'а **И**
   их интервалы пересекаются ≥ ``min_overlap_cm``.
3. Финальный интервал группы = пересечение интервалов всех вошедших
   триплетов (то место, где гарантировано все members делят IBD).
   Если пересечение < ``min_overlap_cm``, группа отбрасывается
   (геометрия не сходится — это, скорее всего, два независимых сегмента
   на одной хромосоме, не настоящая триангуляция).

Privacy: вход — агрегаты cM на сегмент (не raw genotypes/positions/rsid).
Логи — только статистика (см. ADR-0012). Этот модуль ничего не пишет
в storage и не делает сетевых вызовов.

Known limitation (Phase 6.5 fix): endogamy. В endogamous популяциях
(Ashkenazi Jewish, Roma, Amish) много дальних родственников делят
короткие IBD-сегменты по нескольким независимым линиям одновременно,
из-за чего triangulation alone теряет специфичность. :func:`bayes_boost`
понижает confidence_boost до 0.5x, если в группе > 10 members — это
эвристический флаг «вероятно endogamy», не строгая статистика.
Полное решение (IBD2 + phasing для разделения линий) — Phase 6.5.
"""

from __future__ import annotations

import logging
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

_LOG: Final = logging.getLogger(__name__)

# Industry standard threshold для half-IBD сегментов (см. ADR-0014).
DEFAULT_MIN_OVERLAP_CM: Final[float] = 7.0

# Эвристический порог: > N matches на одну группу при отсутствии MRCA →
# вероятно endogamy. Phase 6.5 заменит на честный IBD2-detector.
ENDOGAMY_MEMBER_COUNT_THRESHOLD: Final[int] = 10

# Boost-множители для bayes_boost (см. ADR-0054 §«Confidence policy»).
_BOOST_PAIR_TRIPLET: Final[float] = 1.2
_BOOST_GROUP_NO_MRCA: Final[float] = 1.0
_BOOST_GROUP_WITH_MRCA: Final[float] = 1.5
_BOOST_ENDOGAMY_PENALTY: Final[float] = 0.5


class TriangulationSegment(BaseModel):
    """Один IBD-сегмент match'а в cM-координатах.

    Координаты в cM (генетических, а не bp), чтобы триангуляция работала
    одинаково на любой ссылочной сборке: dna-service резолвит bp→cM
    через :class:`dna_analysis.GeneticMap` перед сборкой :class:`Match`.

    Хромосома — autosomal 1..22; sex-хромосомы (X/Y/MT) триангуляция
    не покрывает (ADR-0014).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chromosome: int = Field(..., ge=1, le=22)
    start_cm: float = Field(..., ge=0.0)
    end_cm: float = Field(..., ge=0.0)

    @model_validator(mode="after")
    def _validate_interval(self) -> TriangulationSegment:
        """Проверяет, что start_cm < end_cm (вырожденный сегмент = ошибка ввода)."""
        if self.end_cm <= self.start_cm:
            msg = (
                f"TriangulationSegment requires end_cm > start_cm "
                f"(got start_cm={self.start_cm}, end_cm={self.end_cm})"
            )
            raise ValueError(msg)
        return self


class Match(BaseModel):
    """Один match kit-owner'а вместе со своими IBD-сегментами и shared-match relation.

    Пустая ``shared_match_ids`` — допустима (match без known shared
    matches; не участвует в триангуляции, но может присутствовать в input
    для consistency со списком из БД).

    ``has_known_mrca`` — флаг «в дереве пользователя есть кандидат MRCA
    с этим match» (например, link на person через ``DnaMatch.matched_person_id``
    + tree-resolved relationship). Используется только в :func:`bayes_boost`.
    Phase 6.4 не считает MRCA сама — caller подставляет, что знает.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str = Field(..., min_length=1)
    segments: tuple[TriangulationSegment, ...] = Field(default=())
    shared_match_ids: frozenset[str] = Field(default=frozenset())
    has_known_mrca: bool = False


class TriangulationGroup(BaseModel):
    """Группа matches триангулирующих на одном участке одной хромосомы.

    Attributes:
        chromosome: Autosomal 1..22.
        start_cm: Начало гарантированного overlap'а всех members в cM.
        end_cm: Конец гарантированного overlap'а в cM. ``end_cm - start_cm``
            ≥ ``min_overlap_cm``, на котором группа была построена.
        members: Отсортированные match_id всех matches в группе. Минимум 2;
            kit-owner подразумевается, не входит в этот список.
        confidence_boost: Множитель confidence для same-person гипотезы.
            Default 1.0 в выходе :func:`find_triangulation_groups`;
            обновляется через :func:`bayes_boost` при наличии tree-context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chromosome: int = Field(..., ge=1, le=22)
    start_cm: float = Field(..., ge=0.0)
    end_cm: float = Field(..., gt=0.0)
    members: tuple[str, ...] = Field(..., min_length=2)
    confidence_boost: float = Field(default=1.0, ge=0.0, le=10.0)


# --- Algorithm ---------------------------------------------------------------


def find_triangulation_groups(
    matches: list[Match],
    min_overlap_cm: float = DEFAULT_MIN_OVERLAP_CM,
) -> list[TriangulationGroup]:
    """Возвращает все triangulation-группы по списку matches.

    Args:
        matches: Список matches kit-owner'а, каждый со своими IBD-сегментами
            (в cM-координатах) и frozenset id'шников shared matches.
        min_overlap_cm: Минимальная длина пересечения сегментов для признания
            триангуляции (default 7.0 cM, см. ADR-0014).

    Returns:
        Список :class:`TriangulationGroup`, отсортированный по
        ``(chromosome, start_cm)``. ``confidence_boost`` всех групп = 1.0;
        для финального confidence_boost вызывайте :func:`bayes_boost` отдельно
        с tree-context.
    """
    if min_overlap_cm <= 0:
        msg = "min_overlap_cm must be positive"
        raise ValueError(msg)

    triplets = _build_triplets(matches, min_overlap_cm=min_overlap_cm)
    groups = _merge_triplets(triplets, min_overlap_cm=min_overlap_cm)

    _LOG.debug(
        "triangulation: %d matches → %d triplets → %d groups (min_overlap=%.2f)",
        len(matches),
        len(triplets),
        len(groups),
        min_overlap_cm,
    )
    return groups


def bayes_boost(
    group: TriangulationGroup,
    tree_relationship: str | None,
) -> float:
    """Возвращает множитель confidence для same-person гипотезы по группе.

    Простая heuristic policy (см. ADR-0054):

    * ``len(members) >= 3`` и ``tree_relationship is not None`` → ``1.5``
      (сильный сигнал: triangulating cluster с известным MRCA в дереве).
    * ``len(members) >= 3`` и ``tree_relationship is None`` → ``1.0``
      (детектировано, но без tree prior boost не даём).
    * ``len(members) == 2`` → ``1.2``
      (одинокий triplet — слабый сигнал, минимальный boost).
    * ``len(members) > 10`` → ``0.5`` (override остального;
      эвристический endogamy-флаг, см. ADR-0054 §known limitations).

    Args:
        group: Группа из :func:`find_triangulation_groups`.
        tree_relationship: Известное relationship-label из дерева
            (например, ``"3rd cousin once removed"``) или ``None``,
            если MRCA не идентифицирован.

    Returns:
        Множитель в диапазоне [0.5, 1.5]. НЕ posterior probability.
    """
    member_count = len(group.members)

    if member_count > ENDOGAMY_MEMBER_COUNT_THRESHOLD:
        return _BOOST_ENDOGAMY_PENALTY

    if member_count == 2:
        return _BOOST_PAIR_TRIPLET

    if tree_relationship is not None:
        return _BOOST_GROUP_WITH_MRCA
    return _BOOST_GROUP_NO_MRCA


# --- Internals ---------------------------------------------------------------


# Один pairwise triangulation triplet до union-find фазы.
# (chromosome, start_cm, end_cm, frozenset({mid_a, mid_b}))
_Triplet = tuple[int, float, float, frozenset[str]]


def _build_triplets(
    matches: list[Match],
    *,
    min_overlap_cm: float,
) -> list[_Triplet]:
    """Создаёт pairwise-triplets по всем mutual shared-match парам.

    Сложность: O(M²·S²) в худшем (где M = matches, S = avg segments per match);
    в реальных данных S ≤ десятков и shared_match_ids разрежены.
    """
    by_id: dict[str, Match] = {m.match_id: m for m in matches}
    triplets: list[_Triplet] = []

    for m_a in matches:
        for shared_id in m_a.shared_match_ids:
            # Канонизация порядка пары (по match_id) + проверка
            # mutuality — иначе считаем half-edge невалидным.
            if shared_id <= m_a.match_id:
                continue
            m_b = by_id.get(shared_id)
            if m_b is None:
                continue
            if m_a.match_id not in m_b.shared_match_ids:
                continue

            for seg_a in m_a.segments:
                for seg_b in m_b.segments:
                    if seg_a.chromosome != seg_b.chromosome:
                        continue
                    overlap_start = max(seg_a.start_cm, seg_b.start_cm)
                    overlap_end = min(seg_a.end_cm, seg_b.end_cm)
                    if overlap_end - overlap_start >= min_overlap_cm:
                        triplets.append(
                            (
                                seg_a.chromosome,
                                overlap_start,
                                overlap_end,
                                frozenset({m_a.match_id, m_b.match_id}),
                            )
                        )

    return triplets


def _merge_triplets(
    triplets: list[_Triplet],
    *,
    min_overlap_cm: float,
) -> list[TriangulationGroup]:
    """Сливает triplets в группы через union-find и собирает финальные интервалы."""
    n = len(triplets)
    if n == 0:
        return []

    parent = list(range(n))

    def _find(x: int) -> int:
        # Path compression.
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: int, y: int) -> None:
        rx, ry = _find(x), _find(y)
        if rx != ry:
            parent[rx] = ry

    # Per-chromosome bucketing — два triplet'а на разных хромосомах
    # никогда не сольются, незачем сравнивать каждый с каждым.
    by_chrom: dict[int, list[int]] = {}
    for i, (chrom, _, _, _) in enumerate(triplets):
        by_chrom.setdefault(chrom, []).append(i)

    for indices in by_chrom.values():
        # Сортируем по start_cm, чтобы O(n²) превратить в O(n²) — но с
        # ранним выходом по start_cm > j_end_cm (sweep-line).
        indices.sort(key=lambda i: triplets[i][1])
        for ii, i in enumerate(indices):
            _, si, ei, mi = triplets[i]
            for j in indices[ii + 1 :]:
                _, sj, ej, mj = triplets[j]
                if sj > ei:
                    # Все следующие triplets начинаются ещё позже —
                    # пересечения с i гарантированно нет (sweep-line cutoff).
                    break
                if not (mi & mj):
                    continue
                overlap_start = max(si, sj)
                overlap_end = min(ei, ej)
                if overlap_end - overlap_start >= min_overlap_cm:
                    _union(i, j)

    components: dict[int, list[int]] = {}
    for i in range(n):
        root = _find(i)
        components.setdefault(root, []).append(i)

    groups: list[TriangulationGroup] = []
    for indices in components.values():
        chrom = triplets[indices[0]][0]
        # Финальный интервал = пересечение всех triplets — это участок,
        # на котором гарантировано пересекаются ВСЕ члены группы.
        start_cm = max(triplets[i][1] for i in indices)
        end_cm = min(triplets[i][2] for i in indices)
        if end_cm - start_cm < min_overlap_cm:
            # После union-find пересечение может оказаться слишком
            # коротким — группа геометрически разрушается.
            continue
        members: set[str] = set()
        for i in indices:
            members |= triplets[i][3]
        groups.append(
            TriangulationGroup(
                chromosome=chrom,
                start_cm=start_cm,
                end_cm=end_cm,
                members=tuple(sorted(members)),
            )
        )

    groups.sort(key=lambda g: (g.chromosome, g.start_cm, g.end_cm))
    return groups
