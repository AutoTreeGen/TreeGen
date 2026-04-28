"""Merge-mode resolver для FS-импорта (Phase 5.2).

Phase 5.1 importer всегда вставляет FS-persons как новые записи; Phase 5.2.1
после INSERT'а пишет review-suggestion'ы в ``fs_dedup_attempts``. Этот
модуль — третий, более жёсткий слой: **до** INSERT'а merger проверяет,
есть ли в целевом дереве high-confidence матч с FS-персоной, и решает:

* :attr:`MergeStrategy.SKIP` — FS-person уже представлен в дереве
  (по ``fs_pid`` идемпотентности); ничего не делаем.
* :attr:`MergeStrategy.MERGE` — score ≥ ``HIGH_CONFIDENCE_THRESHOLD``
  по entity-resolution scorer'у; используем существующий Person'а как
  таргет, не создаём новую row.
* :attr:`MergeStrategy.CREATE_AS_NEW` — score < high-threshold (или
  кандидатов нет вообще): создаём новый Person'а как раньше. Если
  score попал в mid-confidence коридор (``0.5 ≤ score < 0.9``),
  attempt помечается ``needs_review=True`` для последующего ручного
  ревью.

Решение записывается в ``fs_import_merge_attempts``. Сам модуль НЕ
мутирует Person'ов и не пишет ORM-rows кроме self-reporting attempt'а;
вызывающая сторона (importer) применяет стратегию.

CLAUDE.md §5: SKIP/MERGE на FS-import → MERGE здесь означает «прицепить
FS-source/Names/Events к **существующему** Person'у», а не cross-person
merge. Двухсторонний merge двух local-Person'ов — отдельный flow Phase 4.6.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from entity_resolution import PersonForMatching, person_match_score
from familysearch_client import FsFact, FsPerson
from shared_models.enums import MergeStrategy
from shared_models.orm import Event, EventParticipant, Name, Person, Place
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Высокий порог: всё ≥ него — MERGE. CLAUDE.md §5 запрещает cross-person
# auto-merge, но «прицепить FS-evidence к уже существующему» — другое
# действие; берём верхний коридор «definitely the same person» (ADR-0015
# upper bracket — 0.85+, тут чуть консервативнее 0.9 чтобы не схватывать
# false-positives).
HIGH_CONFIDENCE_THRESHOLD: float = 0.9

# Нижняя граница «возможно дубликат» — score ниже неё означает «явно
# разные люди», флажок review не выставляется.
MID_CONFIDENCE_THRESHOLD: float = 0.5


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Решение, принятое :func:`resolve_fs_person` для одной FS-персоны.

    Атрибуты:
        strategy: :class:`MergeStrategy` — что делать importer'у.
        matched_person_id: Persons.id, на который приземлилось решение
            (см. doc-string :class:`shared_models.orm.FsImportMergeAttempt`),
            либо None.
        score: composite score от scorer'а; None если matcher не дошёл
            до подсчёта (SKIP по fs_pid идемпотентности, либо нет
            кандидатов).
        components: покомпонентный breakdown скорера; пустой dict если
            ``score is None``.
        needs_review: True для CREATE_AS_NEW в mid-confidence коридоре —
            UI Phase 4.5/4.6 предложит юзеру ручной merge.
        reason: короткий label для audit'а (см. ORM-докстр
            :class:`shared_models.orm.FsImportMergeAttempt.reason`).
    """

    strategy: MergeStrategy
    matched_person_id: uuid.UUID | None = None
    score: float | None = None
    components: dict[str, float] = field(default_factory=dict)
    needs_review: bool = False
    reason: str = ""


async def resolve_fs_person(
    session: AsyncSession,
    fs_record: FsPerson,
    target_tree_id: uuid.UUID,
    *,
    high_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
    mid_threshold: float = MID_CONFIDENCE_THRESHOLD,
) -> ResolutionResult:
    """Решить судьбу одной FS-персоны при импорте в существующее дерево.

    Алгоритм (детерминированный, идемпотентный):

    1. **fs_pid idempotency.** Если в дереве уже есть Person с
       ``provenance->>'fs_person_id' = fs_record.id`` — это refresh-сценарий,
       решение SKIP (или, точнее, «не создавай дубликат, существующая
       Person и есть таргет»).
    2. **Scorer.** Иначе считаем
       :func:`entity_resolution.person_match_score` против всех не-FS
       (т.е. ``provenance.source != 'familysearch'``) Person'ов дерева и
       берём топового кандидата.
    3. **Threshold-decision tree:**

       * ``score ≥ high_threshold`` → MERGE с этим кандидатом.
       * ``mid_threshold ≤ score < high_threshold`` → CREATE_AS_NEW
         + ``needs_review=True`` (UI flag).
       * ``score < mid_threshold`` или нет кандидатов → CREATE_AS_NEW,
         needs_review=False.

    Args:
        session: AsyncSession. Read-only (мы только SELECT'им).
        fs_record: FS-person из pedigree, как пришёл из FamilySearch API.
        target_tree_id: дерево, в котором проверяем кандидатов.
        high_threshold: override для тестов / тюнинга.
        mid_threshold: override для тестов / тюнинга.

    Returns:
        :class:`ResolutionResult` — стратегия + дополнительный контекст.
    """
    # 1. fs_pid idempotency — это самый дешёвый путь, делаем первым.
    existing = await _find_by_fs_pid(session, target_tree_id, fs_record.id)
    if existing is not None:
        return ResolutionResult(
            strategy=MergeStrategy.SKIP,
            matched_person_id=existing,
            score=None,
            components={},
            needs_review=False,
            reason="fs_pid_idempotent",
        )

    # 2. Scorer против локальных не-FS persons.
    fs_pfm = _fs_person_to_pfm(fs_record)
    candidates = await _load_local_candidates(session, target_tree_id)
    if not candidates:
        return ResolutionResult(
            strategy=MergeStrategy.CREATE_AS_NEW,
            matched_person_id=None,
            score=None,
            components={},
            needs_review=False,
            reason="no_candidates",
        )

    best_score = -1.0
    best_components: dict[str, float] = {}
    best_person_id: uuid.UUID | None = None
    for cand_id, cand_pfm in candidates:
        score, components = person_match_score(fs_pfm, cand_pfm)
        if score > best_score:
            best_score = score
            best_components = components
            best_person_id = cand_id

    # 3. Threshold-decision tree.
    if best_score >= high_threshold and best_person_id is not None:
        return ResolutionResult(
            strategy=MergeStrategy.MERGE,
            matched_person_id=best_person_id,
            score=best_score,
            components=best_components,
            needs_review=False,
            reason="high_confidence_match",
        )
    if best_score >= mid_threshold and best_person_id is not None:
        return ResolutionResult(
            strategy=MergeStrategy.CREATE_AS_NEW,
            matched_person_id=best_person_id,
            score=best_score,
            components=best_components,
            needs_review=True,
            reason="mid_confidence_review",
        )
    # Низкий score или ноль — топ-кандидат не релевантен, но мы всё равно
    # фиксируем его в attempt-row (для аудита «вот что было ближайшее»).
    if best_person_id is not None and best_score >= 0.0:
        return ResolutionResult(
            strategy=MergeStrategy.CREATE_AS_NEW,
            matched_person_id=best_person_id,
            score=best_score,
            components=best_components,
            needs_review=False,
            reason="low_confidence",
        )
    return ResolutionResult(
        strategy=MergeStrategy.CREATE_AS_NEW,
        matched_person_id=None,
        score=None,
        components={},
        needs_review=False,
        reason="no_candidates",
    )


async def _find_by_fs_pid(
    session: AsyncSession,
    tree_id: uuid.UUID,
    fs_pid: str,
) -> uuid.UUID | None:
    """Вернуть Persons.id, у которой ``provenance.fs_person_id`` совпал, либо None."""
    stmt = (
        select(Person.id)
        .where(Person.tree_id == tree_id)
        .where(Person.deleted_at.is_(None))
        .where(Person.provenance["fs_person_id"].astext == fs_pid)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _load_local_candidates(
    session: AsyncSession,
    tree_id: uuid.UUID,
) -> list[tuple[uuid.UUID, PersonForMatching]]:
    """Загрузить всех не-FS Person'ов дерева + их scoring-фичи.

    Mirror'ит подход ``fs_dedup.find_fs_dedup_candidates`` (single bulk
    SELECT за Person'ами + bulk-load имён и BIRT-events). Не возвращает
    deleted_at-row'ы.
    """
    persons = (
        (
            await session.execute(
                select(Person).where(
                    Person.tree_id == tree_id,
                    Person.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    # Кандидаты: только non-FS persons. FS-persons (своя предыдущая
    # история того же дерева) не должны провоцировать MERGE — для них
    # action либо SKIP по fs_pid (выше), либо они как «тоже-FS» не
    # участвуют в merge-mode (направленность — FS → local).
    locals_only = [p for p in persons if (p.provenance or {}).get("source") != "familysearch"]
    if not locals_only:
        return []

    person_ids = [p.id for p in locals_only]
    name_by_person = await _load_primary_names(session, person_ids)
    birt_year, birt_place = await _load_birth_features(session, person_ids)

    out: list[tuple[uuid.UUID, PersonForMatching]] = []
    for p in locals_only:
        n = name_by_person.get(p.id)
        out.append(
            (
                p.id,
                PersonForMatching(
                    given=n.given_name if n else None,
                    surname=n.surname if n else None,
                    birth_year=birt_year.get(p.id),
                    death_year=None,
                    birth_place=birt_place.get(p.id),
                    sex=p.sex,
                ),
            )
        )
    return out


async def _load_primary_names(
    session: AsyncSession,
    person_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Name]:
    """Имя ``sort_order=0`` для каждого person_id (одно на персону)."""
    if not person_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Name)
                .where(Name.person_id.in_(person_ids), Name.sort_order == 0)
                .order_by(Name.person_id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, Name] = {}
    for n in rows:
        out.setdefault(n.person_id, n)
    return out


async def _load_birth_features(
    session: AsyncSession,
    person_ids: list[uuid.UUID],
) -> tuple[dict[uuid.UUID, int | None], dict[uuid.UUID, str | None]]:
    """BIRT year + birth_place per person через event_participants."""
    if not person_ids:
        return {}, {}
    rows = (
        await session.execute(
            select(Event, EventParticipant.person_id, Place.canonical_name)
            .join(EventParticipant, EventParticipant.event_id == Event.id)
            .join(Place, Place.id == Event.place_id, isouter=True)
            .where(
                EventParticipant.person_id.in_(person_ids),
                Event.deleted_at.is_(None),
                Event.event_type == "BIRT",
            )
        )
    ).all()
    birt_year: dict[uuid.UUID, int | None] = {}
    birt_place: dict[uuid.UUID, str | None] = {}
    for event, pid, place_name in rows:
        if pid in birt_year:
            continue
        birt_year[pid] = event.date_start.year if event.date_start else None
        if place_name:
            birt_place[pid] = place_name
    return birt_year, birt_place


def _fs_person_to_pfm(fs: FsPerson) -> PersonForMatching:
    """Сжать ``FsPerson`` до ``PersonForMatching`` для скорера.

    Берём preferred-имя (или первое); birth_year и birth_place — из
    Birth-fact'а (см. ``_FACT_TYPE_MAP`` importer'а).
    """
    given: str | None = None
    surname: str | None = None
    if fs.names:
        # Preferred имя — приоритет. Иначе первое.
        primary = next((n for n in fs.names if n.preferred), fs.names[0])
        given = primary.given
        surname = primary.surname
        if given is None and surname is None and primary.full_text:
            # Fallback на full_text как given (importer делает то же).
            given = primary.full_text

    birth_year: int | None = None
    birth_place: str | None = None
    for fact in fs.facts:
        if fact.type != "Birth":
            continue
        birth_year = _extract_year(fact)
        if fact.place_original:
            birth_place = fact.place_original.strip() or None
        break

    sex_value: str | None = None
    if fs.gender.value == "MALE":
        sex_value = "M"
    elif fs.gender.value == "FEMALE":
        sex_value = "F"
    elif fs.gender.value == "UNKNOWN":
        sex_value = "U"

    return PersonForMatching(
        given=given,
        surname=surname,
        birth_year=birth_year,
        death_year=None,
        birth_place=birth_place,
        sex=sex_value,
    )


def _extract_year(fact: FsFact) -> int | None:
    """Грубо извлечь 4-значный год из ``date_original``.

    GEDCOM-X в FS отдаёт даты как свободный текст («3 Apr 1850», «1850»,
    «ABT 1900»). Полноценный парсер — вне scope Phase 5.2; здесь нужен
    только год для match-фичи. Берём первое 4-значное вхождение
    1000..2999, чтобы не схватывать дни/часы.
    """
    if fact.date_original is None:
        return None
    digits = ""
    for ch in fact.date_original:
        if ch.isdigit():
            digits += ch
            if len(digits) == 4:
                year = int(digits)
                if 1000 <= year <= 2999:
                    return year
                # сбросим — продолжим искать другую 4-цифровую группу
                digits = digits[1:]
        else:
            digits = ""
    return None


__all__ = [
    "HIGH_CONFIDENCE_THRESHOLD",
    "MID_CONFIDENCE_THRESHOLD",
    "ResolutionResult",
    "resolve_fs_person",
]
