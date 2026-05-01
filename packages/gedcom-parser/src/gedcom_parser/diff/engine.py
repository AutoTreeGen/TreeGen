"""Diff engine: сравнение двух ``GedcomDocument`` с возвратом ``DiffReport``.

Алгоритм по высокому уровню:

1. **Person matching** — composite ``person_match_score`` из
   :mod:`entity_resolution.persons` (ADR-0015), blocked by Daitch-Mokotoff
   surname кодам. Greedy 1:1 above ``options.person_match_threshold``.
2. **Source matching** — ``source_match_score`` из
   :mod:`entity_resolution.sources`, naive O(|L|×|R|) попарно.
3. **Family matching** — через перенос ``(husband, wife)``-пары через
   person matches. Семьи без HUSB и WIFE не матчатся (всегда added/removed).
4. **Field-level diffs** для matched персон / источников.
5. **unknown_tags** (Phase 5.5a quarantined) сравниваются с переносом
   owner-xref через person/source/family matches.

xref'ы между двумя GEDCOM-файлами не совпадают (каждый импортёр генерит
свои), поэтому всё matching — content-based. xref'ы появляются в diff'е
только как identifier'ы для downstream consumer'ов (UI / apply-step в
5.7b/c).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from entity_resolution.persons import PersonForMatching, person_match_score
from entity_resolution.phonetic import daitch_mokotoff
from entity_resolution.sources import source_match_score

from gedcom_parser.diff.types import (
    DiffOptions,
    DiffReport,
    FamilyChange,
    FieldChange,
    PersonChange,
    SourceChange,
    UnknownTagChange,
)

if TYPE_CHECKING:
    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Event, Family, Person


# Bucket-код для персон без surname'а / без DM-кодов.
_NO_DM_BUCKET = ""


def diff_gedcoms(
    left: GedcomDocument,
    right: GedcomDocument,
    options: DiffOptions | None = None,
) -> DiffReport:
    """Сравнить два GedcomDocument и вернуть structured diff.

    Args:
        left: Документ-«было». Появляется в ``persons_removed`` если без
            пары в right.
        right: Документ-«стало». Появляется в ``persons_added`` если без
            пары в left.
        options: Параметры сравнения. ``None`` — defaults
            (см. :class:`~gedcom_parser.diff.types.DiffOptions`).

    Returns:
        :class:`~gedcom_parser.diff.types.DiffReport` с детерминированной
        сортировкой во всех секциях. Пустой report (все поля пустые)
        означает «документы эквивалентны по сравниваемым полям».
    """
    opts = options or DiffOptions()

    person_matches = _match_persons(left, right, opts)
    source_matches = _match_sources(left, right, opts)
    family_matches = _match_families(left, right, person_matches)

    matched_left_persons = set(person_matches)
    matched_right_persons = {r for r, _ in person_matches.values()}

    persons_added = tuple(sorted(set(right.persons) - matched_right_persons))
    persons_removed = tuple(sorted(set(left.persons) - matched_left_persons))
    persons_modified = _diff_matched_persons(left, right, person_matches, opts)

    matched_left_sources = set(source_matches)
    matched_right_sources = {r for r, _ in source_matches.values()}

    sources_added = tuple(sorted(set(right.sources) - matched_right_sources))
    sources_removed = tuple(sorted(set(left.sources) - matched_left_sources))
    sources_modified = _diff_matched_sources(left, right, source_matches, opts)

    relations_added, relations_modified, relations_removed = _emit_family_changes(
        left, right, person_matches, family_matches
    )

    unknown_tag_changes = _diff_unknown_tags(
        left,
        right,
        person_matches=person_matches,
        source_matches=source_matches,
        family_matches=family_matches,
    )

    return DiffReport(
        persons_added=persons_added,
        persons_modified=persons_modified,
        persons_removed=persons_removed,
        relations_added=relations_added,
        relations_modified=relations_modified,
        relations_removed=relations_removed,
        sources_added=sources_added,
        sources_modified=sources_modified,
        sources_removed=sources_removed,
        unknown_tag_changes=unknown_tag_changes,
    )


# =============================================================================
# Person matching
# =============================================================================


def _match_persons(
    left: GedcomDocument,
    right: GedcomDocument,
    options: DiffOptions,
) -> dict[str, tuple[str, float]]:
    """Greedy 1:1 person matching, ``{left_xref: (right_xref, score)}``.

    Blocking по Daitch-Mokotoff surname-кодам; внутри bucket'а полное
    попарное scoring через :func:`entity_resolution.persons.person_match_score`.
    Outside-of-bucket пары не сравниваются (с разными surname-фонетиками
    composite score не перешагнёт threshold даже если совпали given +
    birth_year).
    """
    threshold = options.person_match_threshold

    left_pm = {xref: _person_to_p4m(p) for xref, p in left.persons.items()}
    right_pm = {xref: _person_to_p4m(p) for xref, p in right.persons.items()}

    right_buckets: dict[str, list[str]] = {}
    for xref, pm in right_pm.items():
        for code in _dm_codes_or_fallback(pm.surname):
            right_buckets.setdefault(code, []).append(xref)

    candidates: list[tuple[str, str, float]] = []
    for left_xref, left_p in left_pm.items():
        right_xrefs: set[str] = set()
        for code in _dm_codes_or_fallback(left_p.surname):
            right_xrefs.update(right_buckets.get(code, ()))
        for right_xref in right_xrefs:
            score, _components = person_match_score(left_p, right_pm[right_xref])
            if score >= threshold:
                candidates.append((left_xref, right_xref, score))

    # Greedy: highest score first, lex tie-break для воспроизводимости.
    candidates.sort(key=lambda t: (-t[2], t[0], t[1]))
    matches: dict[str, tuple[str, float]] = {}
    used_right: set[str] = set()
    for left_xref, right_xref, score in candidates:
        if left_xref in matches or right_xref in used_right:
            continue
        matches[left_xref] = (right_xref, score)
        used_right.add(right_xref)
    return matches


def _person_to_p4m(person: Person) -> PersonForMatching:
    """Сжать Person entity в PersonForMatching (ADR-0015 minimal-fields).

    Берётся первое NAME (primary name) и первое BIRT/DEAT-событие. Если
    Name не задан — given/surname ``None`` (matcher положит persona в
    anonymous bucket).
    """
    given: str | None = None
    surname: str | None = None
    if person.names:
        primary = person.names[0]
        given = primary.given
        surname = primary.surname

    birth_year: int | None = None
    death_year: int | None = None
    birth_place: str | None = None
    for event in person.events:
        if event.tag == "BIRT" and birth_year is None:
            if event.date is not None and event.date.date_lower is not None:
                birth_year = event.date.date_lower.year
            if birth_place is None and event.place_raw:
                birth_place = event.place_raw
        elif event.tag == "DEAT" and death_year is None:
            if event.date is not None and event.date.date_lower is not None:
                death_year = event.date.date_lower.year

    return PersonForMatching(
        given=given,
        surname=surname,
        birth_year=birth_year,
        death_year=death_year,
        birth_place=birth_place,
        sex=person.sex,
    )


def _dm_codes_or_fallback(surname: str | None) -> tuple[str, ...]:
    """DM-коды surname'а либо ``("",)`` для anonymous-bucket'а."""
    if not surname:
        return (_NO_DM_BUCKET,)
    codes = daitch_mokotoff(surname)
    if not codes:
        return (_NO_DM_BUCKET,)
    return tuple(codes)


# =============================================================================
# Person field diffs
# =============================================================================


def _diff_matched_persons(
    left: GedcomDocument,
    right: GedcomDocument,
    matches: dict[str, tuple[str, float]],
    options: DiffOptions,
) -> tuple[PersonChange, ...]:
    """Для каждой matched пары — собрать FieldChange-список."""
    out: list[PersonChange] = []
    for left_xref, (right_xref, score) in matches.items():
        changes = _person_field_diffs(left.persons[left_xref], right.persons[right_xref], options)
        if changes:
            out.append(
                PersonChange(
                    left_xref=left_xref,
                    right_xref=right_xref,
                    match_score=score,
                    changes=changes,
                )
            )
    out.sort(key=lambda c: c.left_xref)
    return tuple(out)


def _person_field_diffs(
    left: Person,
    right: Person,
    options: DiffOptions,
) -> tuple[FieldChange, ...]:
    """Сравнить персоны поле-за-полем после matching'а."""
    out: list[FieldChange] = []

    left_name = left.names[0].value if left.names else None
    right_name = right.names[0].value if right.names else None
    if not _strings_equal(left_name, right_name, options.case_insensitive_names):
        out.append(FieldChange(field="name", left_value=left_name, right_value=right_name))

    if left.sex != right.sex:
        out.append(FieldChange(field="sex", left_value=left.sex, right_value=right.sex))

    out.extend(
        _event_field_diffs(
            "birth", _first_event(left, "BIRT"), _first_event(right, "BIRT"), options
        )
    )
    out.extend(
        _event_field_diffs(
            "death", _first_event(left, "DEAT"), _first_event(right, "DEAT"), options
        )
    )

    # Notes / sources — пока сравниваем сырые xref-наборы. Cross-file
    # note/source matching выполняется отдельно (sources matched через
    # source_match_score; notes — задача 5.7b/c). Если xref-составы
    # различаются, эмитим diff: typically true для двух разных файлов,
    # но для downstream UI / apply этот сигнал нужен как маркер «здесь
    # есть что посмотреть».
    if set(left.notes_xrefs) != set(right.notes_xrefs):
        out.append(
            FieldChange(
                field="notes_xrefs",
                left_value=", ".join(left.notes_xrefs) or None,
                right_value=", ".join(right.notes_xrefs) or None,
            )
        )
    if set(left.sources_xrefs) != set(right.sources_xrefs):
        out.append(
            FieldChange(
                field="sources_xrefs",
                left_value=", ".join(left.sources_xrefs) or None,
                right_value=", ".join(right.sources_xrefs) or None,
            )
        )

    return tuple(out)


def _first_event(person: Person, tag: str) -> Event | None:
    """Первое событие персоны с указанным тегом."""
    return next((e for e in person.events if e.tag == tag), None)


def _event_field_diffs(
    field_prefix: str,
    a: Event | None,
    b: Event | None,
    options: DiffOptions,
) -> list[FieldChange]:
    """Сравнить date+place двух событий (или их отсутствие)."""
    out: list[FieldChange] = []
    a_date_raw = a.date_raw if a is not None else None
    b_date_raw = b.date_raw if b is not None else None
    if not _dates_equal(a, b, options.date_tolerance_days):
        out.append(
            FieldChange(
                field=f"{field_prefix}_date",
                left_value=a_date_raw,
                right_value=b_date_raw,
            )
        )

    a_place = a.place_raw if a is not None else None
    b_place = b.place_raw if b is not None else None
    if not _strings_equal(a_place, b_place, options.case_insensitive_names):
        out.append(
            FieldChange(
                field=f"{field_prefix}_place",
                left_value=a_place,
                right_value=b_place,
            )
        )
    return out


def _dates_equal(a: Event | None, b: Event | None, tolerance_days: int) -> bool:
    """Равенство дат с учётом ``date_tolerance_days``.

    Tolerance применяется только когда обе даты — точечные (single-day,
    ``date_lower == date_upper``) и обе разобрались. Для ranges / periods
    падаем обратно на raw-string equality.
    """
    a_raw = a.date_raw if a is not None else None
    b_raw = b.date_raw if b is not None else None
    if a_raw is None and b_raw is None:
        return True
    if a_raw is None or b_raw is None:
        return False
    if a_raw == b_raw:
        return True
    if tolerance_days <= 0:
        return False

    a_date = a.date if a is not None else None
    b_date = b.date if b is not None else None
    if (
        a_date is None
        or b_date is None
        or a_date.date_lower is None
        or a_date.date_upper is None
        or b_date.date_lower is None
        or b_date.date_upper is None
    ):
        return False
    if a_date.date_lower != a_date.date_upper or b_date.date_lower != b_date.date_upper:
        return False
    delta = abs((a_date.date_lower - b_date.date_lower).days)
    return delta <= tolerance_days


def _strings_equal(a: str | None, b: str | None, case_insensitive: bool) -> bool:
    """Сравнение строк с trim'ом + опциональным lowercase'ом.

    ``None == None`` → True. Остальные пары с None — False.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if case_insensitive:
        return a.strip().lower() == b.strip().lower()
    return a.strip() == b.strip()


# =============================================================================
# Source matching + diffs
# =============================================================================


def _match_sources(
    left: GedcomDocument,
    right: GedcomDocument,
    options: DiffOptions,
) -> dict[str, tuple[str, float]]:
    """Greedy 1:1 source matching через ``source_match_score``.

    Naive O(|L|×|R|): количество SOUR-записей даже в больших деревьях
    обычно 10²–10³, не оптимизируем preemptively (ADR-0015 §«Источники»).
    """
    threshold = options.source_match_threshold
    candidates: list[tuple[str, str, float]] = []
    for l_xref, l_src in left.sources.items():
        l_title = l_src.title or ""
        for r_xref, r_src in right.sources.items():
            r_title = r_src.title or ""
            if not l_title and not r_title:
                # Без title source matching не работает; обе стороны
                # анонимные → не пытаемся matching, попадут в added/removed.
                continue
            score = source_match_score(
                l_title,
                l_src.author,
                l_src.abbreviation,
                r_title,
                r_src.author,
                r_src.abbreviation,
            )
            if score >= threshold:
                candidates.append((l_xref, r_xref, score))

    candidates.sort(key=lambda t: (-t[2], t[0], t[1]))
    matches: dict[str, tuple[str, float]] = {}
    used_right: set[str] = set()
    for l_xref, r_xref, score in candidates:
        if l_xref in matches or r_xref in used_right:
            continue
        matches[l_xref] = (r_xref, score)
        used_right.add(r_xref)
    return matches


def _diff_matched_sources(
    left: GedcomDocument,
    right: GedcomDocument,
    matches: dict[str, tuple[str, float]],
    options: DiffOptions,
) -> tuple[SourceChange, ...]:
    """Field-level diff для matched sources."""
    out: list[SourceChange] = []
    for l_xref, (r_xref, score) in matches.items():
        l_src = left.sources[l_xref]
        r_src = right.sources[r_xref]
        changes: list[FieldChange] = []
        for field, l_val, r_val in (
            ("title", l_src.title, r_src.title),
            ("author", l_src.author, r_src.author),
            ("abbreviation", l_src.abbreviation, r_src.abbreviation),
            ("publication", l_src.publication, r_src.publication),
            ("text", l_src.text, r_src.text),
        ):
            if not _strings_equal(l_val, r_val, options.case_insensitive_names):
                changes.append(FieldChange(field=field, left_value=l_val, right_value=r_val))
        if changes:
            out.append(
                SourceChange(
                    left_xref=l_xref,
                    right_xref=r_xref,
                    match_score=score,
                    changes=tuple(changes),
                )
            )
    out.sort(key=lambda c: c.left_xref)
    return tuple(out)


# =============================================================================
# Family matching + diffs
# =============================================================================


def _match_families(
    left: GedcomDocument,
    right: GedcomDocument,
    person_matches: dict[str, tuple[str, float]],
) -> dict[str, str]:
    """Семьи matched через перенос ``(husband, wife)``-пары.

    Семья без HUSB и WIFE не матчится (нет якоря для cross-file lookup'а).
    Семья только с одним из родителей всё ещё может matched — пара
    ``(husband_match, None)`` или ``(None, wife_match)`` ищется в right.

    Returns:
        ``{left_fam_xref: right_fam_xref}`` для matched семей.
    """
    person_l_to_r = {left: right for left, (right, _) in person_matches.items()}

    right_pair_index: dict[tuple[str | None, str | None], str] = {}
    for fam_xref, fam in right.families.items():
        right_pair_index[(fam.husband_xref, fam.wife_xref)] = fam_xref

    matched: dict[str, str] = {}
    used_right: set[str] = set()
    for left_xref, left_fam in left.families.items():
        h_left = left_fam.husband_xref
        w_left = left_fam.wife_xref
        h_right = person_l_to_r.get(h_left) if h_left else None
        w_right = person_l_to_r.get(w_left) if w_left else None
        if h_right is None and w_right is None:
            continue
        right_fam_xref = right_pair_index.get((h_right, w_right))
        if right_fam_xref is None or right_fam_xref in used_right:
            continue
        matched[left_xref] = right_fam_xref
        used_right.add(right_fam_xref)
    return matched


def _emit_family_changes(
    left: GedcomDocument,
    right: GedcomDocument,
    person_matches: dict[str, tuple[str, float]],
    family_matches: dict[str, str],
) -> tuple[
    tuple[FamilyChange, ...],
    tuple[FamilyChange, ...],
    tuple[FamilyChange, ...],
]:
    """Сформировать relations_added / modified / removed."""
    person_l_to_r = {left: right for left, (right, _) in person_matches.items()}
    matched_right_fams = set(family_matches.values())

    added: list[FamilyChange] = []
    modified: list[FamilyChange] = []
    removed: list[FamilyChange] = []

    for left_xref, left_fam in left.families.items():
        if left_xref in family_matches:
            continue
        removed.append(_family_removed(left_xref, left_fam))

    for right_xref, right_fam in right.families.items():
        if right_xref in matched_right_fams:
            continue
        added.append(_family_added(right_xref, right_fam))

    for left_xref, right_xref in family_matches.items():
        change = _family_modified(
            left_xref,
            right_xref,
            left.families[left_xref],
            right.families[right_xref],
            person_l_to_r,
        )
        if change is not None:
            modified.append(change)

    added.sort(key=lambda c: c.right_xref or "")
    modified.sort(key=lambda c: c.left_xref or "")
    removed.sort(key=lambda c: c.left_xref or "")
    return tuple(added), tuple(modified), tuple(removed)


def _family_removed(xref: str, fam: Family) -> FamilyChange:
    """Семья из left без match'а в right."""
    return FamilyChange(
        left_xref=xref,
        husband_left_xref=fam.husband_xref,
        wife_left_xref=fam.wife_xref,
        children_removed=fam.children_xrefs,
        description=f"family {xref} not present in right",
    )


def _family_added(xref: str, fam: Family) -> FamilyChange:
    """Семья из right без match'а в left."""
    return FamilyChange(
        right_xref=xref,
        children_added=fam.children_xrefs,
        description=f"family {xref} not present in left",
    )


def _family_modified(
    left_xref: str,
    right_xref: str,
    left_fam: Family,
    right_fam: Family,
    person_l_to_r: dict[str, str],
) -> FamilyChange | None:
    """Diff детей между matched семьями. Возвращает ``None`` если нет diff'а."""
    left_children = set(left_fam.children_xrefs)
    right_children = set(right_fam.children_xrefs)

    left_mapped = {person_l_to_r[c] for c in left_children if c in person_l_to_r}
    added_children = right_children - left_mapped
    removed_children = {
        c for c in left_children if c not in person_l_to_r or person_l_to_r[c] not in right_children
    }
    if not added_children and not removed_children:
        return None
    return FamilyChange(
        left_xref=left_xref,
        right_xref=right_xref,
        husband_left_xref=left_fam.husband_xref,
        wife_left_xref=left_fam.wife_xref,
        children_added=tuple(sorted(added_children)),
        children_removed=tuple(sorted(removed_children)),
        description="children differ",
    )


# =============================================================================
# Unknown tags (Phase 5.5a quarantined)
# =============================================================================


def _diff_unknown_tags(
    left: GedcomDocument,
    right: GedcomDocument,
    *,
    person_matches: dict[str, tuple[str, float]],
    source_matches: dict[str, tuple[str, float]],
    family_matches: dict[str, str],
) -> tuple[UnknownTagChange, ...]:
    """Diff quarantined ``unknown_tags`` через перенос owner-xref.

    Owner xref маппится в right-сторону через соответствующий matcher
    (individual → person_matches, source → source_matches, family →
    family_matches). Для остальных kind'ов (note, object, repository,
    submitter, header) — strict equality по xref'у. Header всегда имеет
    owner_xref_id == ``"HEAD"`` и kind == ``"header"``, поэтому работает
    из коробки.
    """
    person_l_to_r = {left: right for left, (right, _) in person_matches.items()}
    source_l_to_r = {left: right for left, (right, _) in source_matches.items()}

    def map_owner(kind: str, xref: str) -> str | None:
        if kind == "individual":
            return person_l_to_r.get(xref)
        if kind == "source":
            return source_l_to_r.get(xref)
        if kind == "family":
            return family_matches.get(xref)
        return xref

    right_fingerprints: set[tuple[str, str, str, str]] = set()
    for blk in right.unknown_tags:
        right_fingerprints.add((blk.owner_kind, blk.owner_xref_id, blk.path, blk.record.tag))

    left_mapped_fingerprints: set[tuple[str, str, str, str]] = set()
    for blk in left.unknown_tags:
        target = map_owner(blk.owner_kind, blk.owner_xref_id)
        if target is None:
            continue
        left_mapped_fingerprints.add((blk.owner_kind, target, blk.path, blk.record.tag))

    out: list[UnknownTagChange] = []

    for blk in left.unknown_tags:
        target = map_owner(blk.owner_kind, blk.owner_xref_id)
        fp_in_right = (
            target is not None
            and (
                blk.owner_kind,
                target,
                blk.path,
                blk.record.tag,
            )
            in right_fingerprints
        )
        if not fp_in_right:
            out.append(
                UnknownTagChange(
                    side="removed",
                    owner_xref_id=blk.owner_xref_id,
                    owner_kind=blk.owner_kind,
                    path=blk.path,
                    tag=blk.record.tag,
                )
            )

    for blk in right.unknown_tags:
        fp = (blk.owner_kind, blk.owner_xref_id, blk.path, blk.record.tag)
        if fp not in left_mapped_fingerprints:
            out.append(
                UnknownTagChange(
                    side="added",
                    owner_xref_id=blk.owner_xref_id,
                    owner_kind=blk.owner_kind,
                    path=blk.path,
                    tag=blk.record.tag,
                )
            )

    out.sort(key=lambda c: (c.side, c.owner_kind, c.owner_xref_id, c.path, c.tag))
    return tuple(out)


__all__ = ["diff_gedcoms"]
