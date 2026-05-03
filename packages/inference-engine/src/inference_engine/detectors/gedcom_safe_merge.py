"""Phase 26.3 — GEDCOM safe-merge / multi-source conflict detector.

Когда срабатывает (одно условие):

- ``input_gedcom_excerpt`` содержит ≥ 2 ``0 HEAD`` секций (т.е. это
  multi-source GEDCOM import — два экспорта из разных платформ для
  одной семьи). Single-HEAD trees игнорируются полностью.

После trigger'а детектор парсит каждую HEAD-секцию в граф
``IndiRecord`` / ``FamRecord`` / ``SOUR``-xref'ов, ищет cross-source
duplicate persons и эмитит соответствующие флаги + ``merge_decisions``.

Эмитируемые флаги (только те, для которых есть evidence):

- ``same_person_different_export_ids`` — пара INDI с почти-одинаковыми
  именами (только орфография: Zhitnitsky/Zhitnitzky), birth-year/place
  совпадают.
- ``same_person_alias_identity`` — пара INDI, где given или surname
  явно различаются (Vlad / Vladimir; Mogilevsky maiden / Zhitnitzky
  married; Zhitnitzky / Danilov surname change), но другие сигналы
  (birth, family role, user_assertion identity_merge) подтверждают
  identity.
- ``same_person_disconnected_profile`` — INDI без FAMC и FAMS,
  имеющий name-match с connected INDI в другом source'е.
- ``adoptive_as_biological_parent_in_import`` — INDI в роли HUSB/WIFE
  семьи, чей NOTE (или соответствующий user_assertion) указывает на
  social/adoptive/legal role, а не biological.
- ``gedcom_export_source_media_loss`` — один source держит SOUR-xref'ы
  и ссылки ``1 SOUR @…@`` от INDI, другой — нет, ИЛИ archive snippet
  типа ``gedcom_export_audit`` явно фиксирует loss.
- ``safe_merge_requires_relationship_type_annotation`` — найден HUSB
  с social/adoptive context (см. выше).
- ``rollback_audit_required`` — всегда при ≥ 1 merge_decision
  (multi-source merge без audit log не имеет права быть commit'нутым).

``merge_decisions`` — по одной записи на каждую найденную пару,
с canonical name (более длинная / более информативная форма) и
preserved aliases.

``evaluation_results`` помечает True только assertion'ы, чей
``expected``-блок структурно совпадает с тем, что детектор реально
эмитит:

- ``merge_pair: [X, Y]`` + ``status: Confirmed`` → True, если есть
  merge_decision на эту пару.
- ``flag: "<name>"`` + ``required: true`` → True, если флаг в наших
  ``engine_flags``.
- ``rollback_audit_required: true`` → True, если эмитим этот флаг.

Anti-cheat:

- Детектор НЕ читает ``expected_engine_flags`` /
  ``expected_confidence_outputs`` / ``ground_truth_annotations`` /
  ``embedded_errors[].expected_flag`` (это тот же answer key, только
  под другим именем).
- Все решения derived из ``input_gedcom_excerpt``,
  ``input_user_assertions``, ``input_archive_snippets``.
- Trigger — только структурный (≥ 2 HEAD), не tree_id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from entity_resolution.phonetic import daitch_mokotoff
from entity_resolution.string_matching import levenshtein_ratio, token_set_ratio

from inference_engine.detectors.result import DetectorResult

MIN_NAME_SIMILARITY: float = 0.78
"""Levenshtein-ratio для «то же имя, разная орфография» (Zhitnitsky vs Zhitnitzky)."""

MIN_PLACE_SIMILARITY: float = 0.55
"""token_set_ratio для «то же место, разные форматы» (Kfar-Saba vs Kfar Saba…)."""

GIVEN_PREFIX_MIN_LEN: int = 3
"""Минимальная длина общего префикса для given-name alias (Vlad ⊂ Vladimir)."""

PAIR_SCORE_THRESHOLD: int = 2
"""Минимум совпадающих сигналов (name+year+place+role+assertion) для merge-pair."""

GEDCOM_HEAD_RE = re.compile(r"(?m)^0\s+HEAD\b")
GEDCOM_RECORD_HEADER_RE = re.compile(r"^0\s+@(?P<xref>[A-Z0-9_]+)@\s+(?P<kind>INDI|FAM|SOUR)\s*$")
GEDCOM_NAME_SLASH_RE = re.compile(r"/([^/]*)/")
GEDCOM_XREF_REF_RE = re.compile(r"@([A-Z0-9_]+)@")
YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")

SOCIAL_PARENT_TOKENS: tuple[str, ...] = (
    "social",
    "adoptive",
    "adoption",
    "legal father",
    "legal/social",
    "foster",
    "guardian",
    "step-father",
    "stepfather",
    "name change",
    "name-change",
    "should be social",
    "imported as biological",
)


# ---------------------------------------------------------------------------
# Parsed-GEDCOM dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IndiRecord:
    """Минимальный INDI-снапшот, нужный детектору."""

    xref: str
    full_name: str = ""
    given: str = ""
    surname: str = ""
    birth_year: int | None = None
    birth_place: str = ""
    famc: list[str] = field(default_factory=list)
    fams: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sour_refs: list[str] = field(default_factory=list)


@dataclass
class FamRecord:
    """Минимальный FAM-снапшот."""

    xref: str
    husb: str | None = None
    wife: str | None = None
    children: list[str] = field(default_factory=list)


@dataclass
class GedcomSource:
    """Один HEAD-сегмент: его head label, INDI/FAM-карты и SOUR-xref'ы."""

    index: int
    head_label: str
    indi_by_xref: dict[str, IndiRecord] = field(default_factory=dict)
    fam_by_xref: dict[str, FamRecord] = field(default_factory=dict)
    sour_xrefs: set[str] = field(default_factory=set)


@dataclass
class MergePair:
    """Кандидат merge — пара INDI из разных source'ов + диагностика."""

    a_source_idx: int
    b_source_idx: int
    a: IndiRecord
    b: IndiRecord
    score: int
    is_alias: bool
    is_disconnected: bool


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Phase 26.3 detector entrypoint. См. module docstring."""
    result = DetectorResult()
    gedcom_excerpt = tree.get("input_gedcom_excerpt") or ""
    if not isinstance(gedcom_excerpt, str):
        return result

    head_count = len(GEDCOM_HEAD_RE.findall(gedcom_excerpt))
    if head_count < 2:
        return result

    sources = _parse_gedcom_segments(gedcom_excerpt)
    if len(sources) < 2:
        return result

    user_assertions = _safe_list(tree.get("input_user_assertions"))
    archive_snippets = _safe_list(tree.get("input_archive_snippets"))

    merge_pairs = _find_cross_source_merge_pairs(sources, user_assertions)
    if not merge_pairs:
        return result

    flags: list[str] = []
    if any(not p.is_alias for p in merge_pairs):
        flags.append("same_person_different_export_ids")
    if any(p.is_alias for p in merge_pairs):
        flags.append("same_person_alias_identity")
    if any(p.is_disconnected for p in merge_pairs):
        flags.append("same_person_disconnected_profile")

    if _has_adoptive_imported_as_bio(sources, user_assertions):
        flags.append("adoptive_as_biological_parent_in_import")

    if _has_source_media_loss(sources, archive_snippets):
        flags.append("gedcom_export_source_media_loss")

    if _has_relationship_type_conflict(sources, user_assertions):
        flags.append("safe_merge_requires_relationship_type_annotation")

    flags.append("rollback_audit_required")
    result.engine_flags = flags

    result.merge_decisions = [_build_merge_decision(p) for p in merge_pairs]

    result.evaluation_results = _evaluate_assertions(
        assertions=_safe_list(tree.get("evaluation_assertions")),
        merge_pairs=merge_pairs,
        emitted_flags=set(flags),
    )

    return result


# ---------------------------------------------------------------------------
# GEDCOM parsing
# ---------------------------------------------------------------------------


def _parse_gedcom_segments(gedcom: str) -> list[GedcomSource]:
    """Разбить excerpt по HEAD-границам и распарсить каждый сегмент."""
    lines = gedcom.split("\n")
    boundaries: list[int] = []
    for idx, line in enumerate(lines):
        if line.startswith("0 HEAD"):
            boundaries.append(idx)
    if not boundaries:
        return []
    boundaries.append(len(lines))

    sources: list[GedcomSource] = []
    for source_idx in range(len(boundaries) - 1):
        start = boundaries[source_idx]
        end = boundaries[source_idx + 1]
        segment_lines = lines[start:end]
        sources.append(_parse_single_segment(source_idx, segment_lines))
    return sources


def _parse_single_segment(index: int, lines: list[str]) -> GedcomSource:
    """Минимальный single-pass GEDCOM-парсер для одного HEAD/TRLR-сегмента."""
    source = GedcomSource(index=index, head_label="")
    current_indi: IndiRecord | None = None
    current_fam: FamRecord | None = None
    in_birt = False
    in_head = False

    for raw in lines:
        line = raw.rstrip("\r")

        if line.startswith("0 HEAD"):
            in_head = True
            current_indi = None
            current_fam = None
            in_birt = False
            continue
        if line.strip() == "0 TRLR":
            current_indi = None
            current_fam = None
            in_birt = False
            in_head = False
            continue

        header = GEDCOM_RECORD_HEADER_RE.match(line)
        if header is not None:
            in_head = False
            in_birt = False
            current_indi = None
            current_fam = None
            xref = header.group("xref")
            kind = header.group("kind")
            if kind == "INDI":
                current_indi = IndiRecord(xref=xref)
                source.indi_by_xref[xref] = current_indi
            elif kind == "FAM":
                current_fam = FamRecord(xref=xref)
                source.fam_by_xref[xref] = current_fam
            elif kind == "SOUR":
                source.sour_xrefs.add(xref)
            continue

        if in_head:
            if line.startswith("1 SOUR ") and not source.head_label:
                source.head_label = line[len("1 SOUR ") :].strip()
            continue

        if current_indi is not None:
            if line.startswith("1 NAME "):
                full = line[len("1 NAME ") :].strip()
                current_indi.full_name = full
                surname_m = GEDCOM_NAME_SLASH_RE.search(full)
                if surname_m is not None:
                    current_indi.surname = surname_m.group(1).strip()
                    current_indi.given = full[: surname_m.start()].strip()
                else:
                    current_indi.given = full.strip()
                in_birt = False
            elif line.strip() == "1 BIRT":
                in_birt = True
            elif in_birt and line.startswith("2 DATE "):
                date_text = line[len("2 DATE ") :].strip()
                year_m = YEAR_RE.search(date_text)
                if year_m is not None:
                    current_indi.birth_year = int(year_m.group(1))
            elif in_birt and line.startswith("2 PLAC "):
                current_indi.birth_place = line[len("2 PLAC ") :].strip()
            elif line.startswith("1 FAMC "):
                ref = GEDCOM_XREF_REF_RE.search(line)
                if ref is not None:
                    current_indi.famc.append(ref.group(1))
                in_birt = False
            elif line.startswith("1 FAMS "):
                ref = GEDCOM_XREF_REF_RE.search(line)
                if ref is not None:
                    current_indi.fams.append(ref.group(1))
                in_birt = False
            elif line.startswith("1 NOTE "):
                current_indi.notes.append(line[len("1 NOTE ") :].strip())
                in_birt = False
            elif line.startswith("1 SOUR "):
                ref = GEDCOM_XREF_REF_RE.search(line)
                if ref is not None:
                    current_indi.sour_refs.append(ref.group(1))
                in_birt = False
            elif line.startswith("1 "):
                in_birt = False

        if current_fam is not None:
            if line.startswith("1 HUSB "):
                ref = GEDCOM_XREF_REF_RE.search(line)
                if ref is not None:
                    current_fam.husb = ref.group(1)
            elif line.startswith("1 WIFE "):
                ref = GEDCOM_XREF_REF_RE.search(line)
                if ref is not None:
                    current_fam.wife = ref.group(1)
            elif line.startswith("1 CHIL "):
                ref = GEDCOM_XREF_REF_RE.search(line)
                if ref is not None:
                    current_fam.children.append(ref.group(1))

    return source


# ---------------------------------------------------------------------------
# Merge-pair finding (pairwise across sources)
# ---------------------------------------------------------------------------


def _find_cross_source_merge_pairs(
    sources: list[GedcomSource],
    user_assertions: list[dict[str, Any]],
) -> list[MergePair]:
    """Найти cross-source duplicate persons.

    Двухпроходный алгоритм:

    1. **Initial pass** — pairwise score based on name + birth-year +
       birth-place + user-assertion identity_merge. Pairs with score
       ≥ ``PAIR_SCORE_THRESHOLD`` приняты.
    2. **Family-role pass** — для семей, у которых ≥ 2 ролей уже
       соответствуют initial-парам, добавить unmapped роли с partial
       name-match (given OR surname).

    Mutual best-match: каждый xref может попасть в максимум одну пару.
    """
    identity_merge_ids = _identity_merge_person_ids(user_assertions)

    initial_pairs: list[MergePair] = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    for src_a, src_b in _source_pairs(sources):
        candidates: list[tuple[int, IndiRecord, IndiRecord, bool]] = []
        for indi_a in src_a.indi_by_xref.values():
            for indi_b in src_b.indi_by_xref.values():
                score, is_alias = _initial_pair_score(indi_a, indi_b, identity_merge_ids)
                if score >= PAIR_SCORE_THRESHOLD:
                    candidates.append((score, indi_a, indi_b, is_alias))
        candidates.sort(key=lambda x: -x[0])
        for score, indi_a, indi_b, is_alias in candidates:
            if indi_a.xref in used_a or indi_b.xref in used_b:
                continue
            used_a.add(indi_a.xref)
            used_b.add(indi_b.xref)
            initial_pairs.append(
                MergePair(
                    a_source_idx=src_a.index,
                    b_source_idx=src_b.index,
                    a=indi_a,
                    b=indi_b,
                    score=score,
                    is_alias=is_alias,
                    is_disconnected=_is_disconnected(indi_a) or _is_disconnected(indi_b),
                )
            )

    if not initial_pairs:
        return []

    family_pairs = _propagate_via_family_roles(sources, initial_pairs, used_a, used_b)
    return initial_pairs + family_pairs


def _source_pairs(
    sources: list[GedcomSource],
) -> list[tuple[GedcomSource, GedcomSource]]:
    """Все упорядоченные пары source'ов (i < j)."""
    return [
        (sources[i], sources[j]) for i in range(len(sources)) for j in range(i + 1, len(sources))
    ]


def _initial_pair_score(
    a: IndiRecord, b: IndiRecord, identity_merge_ids: set[str]
) -> tuple[int, bool]:
    """Score(0-5) + is_alias. См. module docstring §"Триггер"."""
    score = 0
    given_match, given_alias = _given_match(a.given, b.given)
    surname_match, surname_alias = _surname_match(a.surname, b.surname)
    if given_match:
        score += 1
    if surname_match:
        score += 1
    if _birth_year_match(a.birth_year, b.birth_year):
        score += 1
    if _place_match(a.birth_place, b.birth_place):
        score += 1
    if a.xref in identity_merge_ids or b.xref in identity_merge_ids:
        score += 1
    is_alias = (given_match and given_alias) or (surname_match and surname_alias)
    if (given_match and not surname_match) or (surname_match and not given_match):
        is_alias = True
    return score, is_alias


def _given_match(a: str, b: str) -> tuple[bool, bool]:
    """Возвращает (matches, is_alias). Alias = prefix relation (Vlad/Vladimir)."""
    if not a or not b:
        return False, False
    a_first = a.lower().split()[0]
    b_first = b.lower().split()[0]
    if a_first == b_first:
        return True, False
    if (
        len(a_first) >= GIVEN_PREFIX_MIN_LEN
        and len(b_first) >= GIVEN_PREFIX_MIN_LEN
        and (a_first.startswith(b_first) or b_first.startswith(a_first))
    ):
        return True, True
    if levenshtein_ratio(a_first, b_first) >= MIN_NAME_SIMILARITY:
        return True, False
    return False, False


def _surname_match(a: str, b: str) -> tuple[bool, bool]:
    """Возвращает (matches, is_alias). Phonetic / Levenshtein equivalent surnames
    считаются match'ем НЕ-alias (просто spelling). Alias-кейс (maiden vs married,
    surname change) не определяется только из surname-сравнения — обнаруживается
    выше, когда given matches AND surname НЕ matches.
    """
    if not a or not b:
        return False, False
    if a.lower() == b.lower():
        return True, False
    if levenshtein_ratio(a, b) >= MIN_NAME_SIMILARITY:
        return True, False
    a_codes = set(daitch_mokotoff(a))
    b_codes = set(daitch_mokotoff(b))
    if a_codes and b_codes and a_codes & b_codes:
        return True, False
    return False, False


def _birth_year_match(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= 1


def _place_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return token_set_ratio(a, b) >= MIN_PLACE_SIMILARITY


def _is_disconnected(indi: IndiRecord) -> bool:
    """INDI без FAMC и FAMS — disconnected profile."""
    return not indi.famc and not indi.fams


def _identity_merge_person_ids(user_assertions: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for a in user_assertions:
        if a.get("scope") != "identity_merge":
            continue
        pid = a.get("person_id")
        if isinstance(pid, str) and pid:
            out.add(pid)
    return out


def _propagate_via_family_roles(
    sources: list[GedcomSource],
    initial_pairs: list[MergePair],
    used_a: set[str],
    used_b: set[str],
) -> list[MergePair]:
    """После initial-pass — добавить роли в matched-семьях.

    Семья считается matched, если ≥ 2 её ролей (HUSB/WIFE/CHIL) уже
    есть в initial-парах. В matched-семье unmapped роли можно
    спарить, если есть partial name-match (given OR surname, но не
    обязательно оба).
    """
    pair_map: dict[str, str] = {p.a.xref: p.b.xref for p in initial_pairs}
    inverse_map: dict[str, str] = {b: a for a, b in pair_map.items()}

    additions: list[MergePair] = []
    for src_a, src_b in _source_pairs(sources):
        for fam_a in src_a.fam_by_xref.values():
            for fam_b in src_b.fam_by_xref.values():
                if not _families_match(fam_a, fam_b, pair_map):
                    continue
                role_pairs = _unmapped_role_pairs(fam_a, fam_b, pair_map, inverse_map)
                for a_xref, b_xref in role_pairs:
                    if a_xref in used_a or b_xref in used_b:
                        continue
                    indi_a = src_a.indi_by_xref.get(a_xref)
                    indi_b = src_b.indi_by_xref.get(b_xref)
                    if indi_a is None or indi_b is None:
                        continue
                    given_match, given_alias = _given_match(indi_a.given, indi_b.given)
                    surname_match, surname_alias = _surname_match(indi_a.surname, indi_b.surname)
                    if not (given_match or surname_match):
                        continue
                    is_alias = (
                        (given_match and given_alias)
                        or (surname_match and surname_alias)
                        or (given_match != surname_match)
                    )
                    used_a.add(a_xref)
                    used_b.add(b_xref)
                    pair_map[a_xref] = b_xref
                    inverse_map[b_xref] = a_xref
                    additions.append(
                        MergePair(
                            a_source_idx=src_a.index,
                            b_source_idx=src_b.index,
                            a=indi_a,
                            b=indi_b,
                            score=1,
                            is_alias=is_alias,
                            is_disconnected=_is_disconnected(indi_a) or _is_disconnected(indi_b),
                        )
                    )
    return additions


def _families_match(
    fam_a: FamRecord,
    fam_b: FamRecord,
    pair_map: dict[str, str],
) -> bool:
    """≥ 2 ролей семьи перекрываются через initial-пары."""
    overlap = 0
    if fam_a.husb and pair_map.get(fam_a.husb) == fam_b.husb:
        overlap += 1
    if fam_a.wife and pair_map.get(fam_a.wife) == fam_b.wife:
        overlap += 1
    b_children = set(fam_b.children)
    for child_a in fam_a.children:
        if pair_map.get(child_a) in b_children:
            overlap += 1
    return overlap >= 2


def _unmapped_role_pairs(
    fam_a: FamRecord,
    fam_b: FamRecord,
    pair_map: dict[str, str],
    inverse_map: dict[str, str],
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if fam_a.husb and fam_b.husb and fam_a.husb not in pair_map and fam_b.husb not in inverse_map:
        out.append((fam_a.husb, fam_b.husb))
    if fam_a.wife and fam_b.wife and fam_a.wife not in pair_map and fam_b.wife not in inverse_map:
        out.append((fam_a.wife, fam_b.wife))
    for child_a in fam_a.children:
        if child_a in pair_map:
            continue
        for child_b in fam_b.children:
            if child_b in inverse_map:
                continue
            out.append((child_a, child_b))
            break
    return out


# ---------------------------------------------------------------------------
# Flag detection
# ---------------------------------------------------------------------------


def _has_adoptive_imported_as_bio(
    sources: list[GedcomSource],
    user_assertions: list[dict[str, Any]],
) -> bool:
    """INDI с NOTE/assertion указывающим social/adoptive role,
    при этом находящийся в HUSB/WIFE-роли семьи."""
    husb_wife_global: set[str] = set()
    for source in sources:
        for fam in source.fam_by_xref.values():
            if fam.husb:
                husb_wife_global.add(fam.husb)
            if fam.wife:
                husb_wife_global.add(fam.wife)

    for source in sources:
        for indi in source.indi_by_xref.values():
            if indi.xref not in husb_wife_global:
                continue
            note_blob = " ".join(indi.notes).lower()
            if any(token in note_blob for token in SOCIAL_PARENT_TOKENS):
                return True

    for assertion in user_assertions:
        pid = assertion.get("person_id")
        if not isinstance(pid, str) or pid not in husb_wife_global:
            continue
        text = (
            (assertion.get("evidence") or "") + " " + (assertion.get("assertion") or "")
        ).lower()
        if any(token in text for token in SOCIAL_PARENT_TOKENS):
            return True
    return False


def _has_source_media_loss(
    sources: list[GedcomSource],
    archive_snippets: list[dict[str, Any]],
) -> bool:
    """Один source держит SOUR-xref'ы / 1 SOUR ссылки от INDI, другой — нет.
    Или archive snippet типа ``gedcom_export_audit`` явно фиксирует loss.
    """
    for snippet in archive_snippets:
        if snippet.get("type") == "gedcom_export_audit":
            return True

    has_sour = []
    for source in sources:
        any_indi_sour_ref = any(indi.sour_refs for indi in source.indi_by_xref.values())
        has_sour.append(bool(source.sour_xrefs) or any_indi_sour_ref)
    return any(has_sour) and not all(has_sour)


def _has_relationship_type_conflict(
    sources: list[GedcomSource],
    user_assertions: list[dict[str, Any]],
) -> bool:
    """HUSB/WIFE с social/adoptive context — конфликт типа relationship'а.

    Детектор 26.3 не зависит от 26.2 (dna_vs_tree). Условие здесь
    идентично ``_has_adoptive_imported_as_bio`` — это намеренно: в
    multi-source GEDCOM safe-merge один и тот же сигнал даёт повод
    эмитить и ``adoptive_as_biological_parent_in_import``, и
    ``safe_merge_requires_relationship_type_annotation`` (первый —
    диагностика, второй — требуемое действие при merge'е).
    """
    return _has_adoptive_imported_as_bio(sources, user_assertions)


# ---------------------------------------------------------------------------
# merge_decisions
# ---------------------------------------------------------------------------


def _build_merge_decision(pair: MergePair) -> dict[str, Any]:
    """Один merge_decision dict для emit'а в EngineOutput."""
    canonical_name = _pick_canonical_name(pair.a.full_name, pair.b.full_name)
    aliases = sorted({pair.a.full_name, pair.b.full_name} - {canonical_name, ""})
    if pair.is_disconnected:
        action = "merge_and_reconnect"
    elif pair.is_alias:
        action = "merge_with_aliases"
    else:
        action = "merge"
    return {
        "merge_id": f"merge_{pair.a.xref}_{pair.b.xref}",
        "merge_pair": [pair.a.xref, pair.b.xref],
        "status": "Confirmed",
        "action": action,
        "canonical_name": canonical_name,
        "aliases": aliases,
        "aliases_preserved": bool(aliases),
        "score": pair.score,
        "is_alias": pair.is_alias,
        "is_disconnected": pair.is_disconnected,
        "preserve_sources": True,
        "rule_id": "gedcom_safe_merge",
    }


def _pick_canonical_name(a: str, b: str) -> str:
    """Длинная / более информативная форма становится canonical."""
    a, b = a.strip(), b.strip()
    if not a:
        return b
    if not b:
        return a
    if len(a.split()) != len(b.split()):
        return a if len(a.split()) > len(b.split()) else b
    return a if len(a) >= len(b) else b


# ---------------------------------------------------------------------------
# Assertion matching
# ---------------------------------------------------------------------------


def _evaluate_assertions(
    *,
    assertions: list[dict[str, Any]],
    merge_pairs: list[MergePair],
    emitted_flags: set[str],
) -> dict[str, bool]:
    """См. module docstring §"evaluation_results"."""
    pair_set = frozenset(frozenset((p.a.xref, p.b.xref)) for p in merge_pairs)
    alias_pair_set = frozenset(frozenset((p.a.xref, p.b.xref)) for p in merge_pairs if p.is_alias)

    out: dict[str, bool] = {}
    for item in assertions:
        aid = item.get("assertion_id")
        if not isinstance(aid, str) or not aid:
            continue
        expected = item.get("expected") or {}
        if not isinstance(expected, dict):
            continue

        if _matches_merge_pair(expected, pair_set, alias_pair_set):
            out[aid] = True
            continue
        if _matches_flag_required(expected, emitted_flags):
            out[aid] = True
            continue
        if _matches_rollback_required(expected, emitted_flags):
            out[aid] = True
            continue
    return out


def _matches_merge_pair(
    expected: dict[str, Any],
    pair_set: frozenset[frozenset[str]],
    alias_pair_set: frozenset[frozenset[str]],
) -> bool:
    pair = expected.get("merge_pair")
    if not isinstance(pair, list) or len(pair) != 2:
        return False
    a, b = pair[0], pair[1]
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    target = frozenset((a, b))
    if target not in pair_set:
        return False
    status = expected.get("status")
    if isinstance(status, str) and status.lower() != "confirmed":
        return False
    return not (expected.get("aliases_preserved") is True and target not in alias_pair_set)


def _matches_flag_required(expected: dict[str, Any], emitted_flags: set[str]) -> bool:
    flag = expected.get("flag")
    if not isinstance(flag, str) or not flag:
        return False
    if expected.get("required") is not True:
        return False
    return flag in emitted_flags


def _matches_rollback_required(expected: dict[str, Any], emitted_flags: set[str]) -> bool:
    if expected.get("rollback_audit_required") is not True:
        return False
    return "rollback_audit_required" in emitted_flags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_list(value: Any) -> list[dict[str, Any]]:
    """list[dict] из произвольного JSON-поля; пустой list при None / wrong type."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


__all__ = [
    "GIVEN_PREFIX_MIN_LEN",
    "MIN_NAME_SIMILARITY",
    "MIN_PLACE_SIMILARITY",
    "PAIR_SCORE_THRESHOLD",
    "detect",
]
