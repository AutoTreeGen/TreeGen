"""Tests для quarantine + round-trip с unknown_tags (Phase 5.5a, ADR-0062).

Покрывает:

* ``quarantine_record`` / ``quarantine_document`` на synthetic GED'ах со
  смесью known + proprietary tags (Ancestry ``_FSFTID``, MyHeritage ``_UID``,
  Geni ``_PUBLIC``, custom ``_CUSTOM``).
* Whitelist-invariant: тег который consumes семантический слой
  (``Person.from_record`` и т.п.) не попадает в unknown_tags.
* Round-trip semantics (Variant B — structural diff after re-parse):
  parse → quarantine → strip unknown direct-children → inject → write →
  re-parse → original-equivalent.
* Real-corpus smoke (``gedcom_real`` marker): на каждом файле corpus'а
  unknown_tags survive parse → write → re-parse цикл.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from gedcom_parser import (
    GedcomDocument,
    GedcomRecord,
    RawTagBlock,
    inject_unknown_tags,
    parse_text,
    quarantine_document,
    write_records,
)
from gedcom_parser.quarantine import (
    KNOWN_FAM_TAGS,
    KNOWN_INDI_TAGS,
    KNOWN_SOUR_TAGS,
)

# -----------------------------------------------------------------------------
# Synthetic helpers.
# -----------------------------------------------------------------------------


def _ged(text: str) -> str:
    """Trim leading whitespace + ensure trailing newline."""
    return "\n".join(line.lstrip() for line in text.strip().splitlines()) + "\n"


_INDI_WITH_UNKNOWN = _ged(
    """
    0 HEAD
    1 GEDC
    2 VERS 5.5.5
    2 FORM LINEAGE-LINKED
    1 CHAR UTF-8
    0 @I1@ INDI
    1 NAME John /Smith/
    1 SEX M
    1 _FSFTID 12345-ABC
    1 BIRT
    2 DATE 1850
    2 _PRIM Y
    1 _UID ABCDEF
    0 @I2@ INDI
    1 NAME Anna /Smith/
    1 _CUSTOM hello world
    0 TRLR
    """
)


_FAM_WITH_UNKNOWN = _ged(
    """
    0 HEAD
    1 GEDC
    2 VERS 5.5.5
    2 FORM LINEAGE-LINKED
    1 CHAR UTF-8
    0 @F1@ FAM
    1 HUSB @I1@
    1 WIFE @I2@
    1 _UID FAMILY-UID-XYZ
    1 _STAT Verified
    0 TRLR
    """
)


_SOUR_WITH_UNKNOWN = _ged(
    """
    0 HEAD
    1 GEDC
    2 VERS 5.5.5
    2 FORM LINEAGE-LINKED
    1 CHAR UTF-8
    0 @S1@ SOUR
    1 TITL Slonim parish register 1850
    1 AUTH Russian Orthodox Church
    1 _APID 1,7619::1234567
    0 TRLR
    """
)


# -----------------------------------------------------------------------------
# Whitelist invariants.
# -----------------------------------------------------------------------------


def test_known_indi_tags_includes_event_tags() -> None:
    """``BIRT``/``DEAT`` обязаны быть в KNOWN_INDI_TAGS — иначе их subtree
    попадёт в quarantine, что мы делать не хотим (event'ы — known).
    """
    assert "BIRT" in KNOWN_INDI_TAGS
    assert "DEAT" in KNOWN_INDI_TAGS
    assert "EVEN" in KNOWN_INDI_TAGS


def test_known_fam_tags_includes_marr_and_husb() -> None:
    assert "MARR" in KNOWN_FAM_TAGS
    assert "HUSB" in KNOWN_FAM_TAGS
    assert "WIFE" in KNOWN_FAM_TAGS
    assert "CHIL" in KNOWN_FAM_TAGS


def test_known_sour_tags_includes_titl_and_repo() -> None:
    assert "TITL" in KNOWN_SOUR_TAGS
    assert "AUTH" in KNOWN_SOUR_TAGS
    assert "REPO" in KNOWN_SOUR_TAGS


# -----------------------------------------------------------------------------
# quarantine_record / quarantine_document.
# -----------------------------------------------------------------------------


def test_quarantine_indi_proprietary_tags() -> None:
    records = parse_text(_INDI_WITH_UNKNOWN)
    blocks = quarantine_document(records)

    # Ожидаем _FSFTID, _UID для I1 и _CUSTOM для I2 — но НЕ _PRIM
    # (_PRIM сидит внутри known BIRT child'а, который на 5.5a не
    # сканируется).
    pairs = [(b.owner_xref_id, b.record.tag) for b in blocks]
    assert ("I1", "_FSFTID") in pairs
    assert ("I1", "_UID") in pairs
    assert ("I2", "_CUSTOM") in pairs
    # Известные теги в quarantine не попадают.
    assert all(b.record.tag.startswith("_") or b.record.tag == "_CUSTOM" for b in blocks)


def test_quarantine_fam_proprietary_tags() -> None:
    records = parse_text(_FAM_WITH_UNKNOWN)
    blocks = quarantine_document(records)

    fam_tags = sorted(b.record.tag for b in blocks if b.owner_xref_id == "F1")
    assert fam_tags == ["_STAT", "_UID"]
    assert all(b.owner_kind == "family" for b in blocks if b.owner_xref_id == "F1")


def test_quarantine_sour_proprietary_tags() -> None:
    records = parse_text(_SOUR_WITH_UNKNOWN)
    blocks = quarantine_document(records)

    sour_blocks = [b for b in blocks if b.owner_xref_id == "S1"]
    assert len(sour_blocks) == 1
    assert sour_blocks[0].record.tag == "_APID"
    assert sour_blocks[0].owner_kind == "source"


def test_quarantine_returns_full_subtree() -> None:
    """Children'ы quarantined тегов сохраняются целиком."""
    ged = _ged(
        """
        0 HEAD
        1 CHAR UTF-8
        0 @I1@ INDI
        1 NAME J /Smith/
        1 _PROFILE
        2 _PHOTO main.jpg
        2 _LINK https://example.com/12345
        0 TRLR
        """
    )
    records = parse_text(ged)
    blocks = quarantine_document(records)
    profile = next(b for b in blocks if b.record.tag == "_PROFILE")
    sub_tags = sorted(c.tag for c in profile.record.children)
    assert sub_tags == ["_LINK", "_PHOTO"]


def test_quarantine_skips_trlr_and_records_without_xref() -> None:
    """TRLR + records без xref'а не попадают в quarantine."""
    ged = _ged(
        """
        0 HEAD
        1 CHAR UTF-8
        0 INDI
        1 NAME orphan
        0 TRLR
        """
    )
    records = parse_text(ged)
    blocks = quarantine_document(records)
    # HEAD-only — пустой, INDI без xref'а — пропущен, TRLR — пропущен.
    assert all(b.owner_kind != "individual" or b.owner_xref_id != "" for b in blocks)


def test_document_from_records_populates_unknown_tags() -> None:
    """``GedcomDocument.from_records`` интегрирует quarantine."""
    records = parse_text(_INDI_WITH_UNKNOWN)
    doc = GedcomDocument.from_records(records)
    assert len(doc.unknown_tags) >= 3
    assert all(isinstance(b, RawTagBlock) for b in doc.unknown_tags)
    # Person-фабрика отработала параллельно — никакого regression'а.
    assert "I1" in doc.persons
    assert "I2" in doc.persons


# -----------------------------------------------------------------------------
# Round-trip via inject_unknown_tags.
# -----------------------------------------------------------------------------


def _strip_unknown_direct_children(
    records: list[GedcomRecord],
    blocks: list[RawTagBlock],
) -> list[GedcomRecord]:
    """Remove direct-children'ов из records, которые сейчас сидят в blocks.

    Имитирует «реконструкцию из ORM», когда entity-слой собрал record'ы
    из типизированных полей, а unknown_tags хранятся отдельно в jsonb.
    """
    # Группируем blocks по owner_xref_id; нам нужно только выкинуть direct
    # children с совпадающим (tag, value, line_no) — line_no уникален в
    # пределах файла, значит идентичность гарантируется.
    by_owner: dict[str, set[tuple[str, str, int]]] = {}
    for block in blocks:
        if block.path:
            continue
        key = block.owner_xref_id
        by_owner.setdefault(key, set()).add(
            (block.record.tag, block.record.value, block.record.line_no),
        )

    out: list[GedcomRecord] = []
    for record in records:
        owner_key = "HEAD" if record.tag == "HEAD" else (record.xref_id or "")
        targets = by_owner.get(owner_key)
        if not targets:
            out.append(record)
            continue
        kept = [c for c in record.children if (c.tag, c.value, c.line_no) not in targets]
        out.append(record.model_copy(update={"children": kept}))
    return out


def test_round_trip_unknown_tags_reinject() -> None:
    """parse → quarantine → strip → inject → write → re-parse → quarantine'

    После полного цикла quarantine' должен совпадать с quarantine
    структурно (по тегам owner'а + path).
    """
    records = parse_text(_INDI_WITH_UNKNOWN)
    blocks = list(quarantine_document(records))
    assert len(blocks) >= 3

    # Срезаем unknown direct-children (имитация DB round-trip).
    stripped = _strip_unknown_direct_children(records, blocks)
    # Ни один stripped-record не должен иметь quarantined-теги в children'ах.
    for record in stripped:
        if record.xref_id == "I1":
            tags = {c.tag for c in record.children}
            assert "_FSFTID" not in tags
            assert "_UID" not in tags

    # Re-injectim обратно.
    rebuilt = inject_unknown_tags(stripped, blocks)
    text_out = write_records(rebuilt)
    records2 = parse_text(text_out)

    # На records2 quarantine должен дать те же блоки (по tag+owner pair).
    blocks2 = quarantine_document(records2)
    pairs1 = sorted(
        (b.owner_xref_id, b.owner_kind, b.path, b.record.tag, b.record.value) for b in blocks
    )
    pairs2 = sorted(
        (b.owner_xref_id, b.owner_kind, b.path, b.record.tag, b.record.value) for b in blocks2
    )
    assert pairs1 == pairs2


def test_round_trip_with_no_unknown_tags_is_noop() -> None:
    """Round-trip на «чистом» GED'е без проприетарных тегов не ломается."""
    ged = _ged(
        """
        0 HEAD
        1 GEDC
        2 VERS 5.5.5
        2 FORM LINEAGE-LINKED
        1 CHAR UTF-8
        0 @I1@ INDI
        1 NAME Alice /Doe/
        1 SEX F
        1 BIRT
        2 DATE 1900
        0 TRLR
        """
    )
    records = parse_text(ged)
    blocks = quarantine_document(records)
    assert blocks == ()
    rebuilt = inject_unknown_tags(records, blocks)
    text_out = write_records(rebuilt)
    records2 = parse_text(text_out)
    assert quarantine_document(records2) == ()
    # И persons всё ещё парсятся.
    doc = GedcomDocument.from_records(records2)
    assert "I1" in doc.persons


def test_inject_skips_paths_for_now() -> None:
    """Phase 5.5a: ``path != ""`` не re-inject'ится; защищает от double-write."""
    record = GedcomRecord(level=2, tag="_PRIM", value="Y", line_no=99, children=[])
    block = RawTagBlock(
        owner_xref_id="I1",
        owner_kind="individual",
        path="BIRT",
        record=record,
    )
    target = GedcomRecord(level=0, xref_id="I1", tag="INDI", value="", line_no=1, children=[])
    out = inject_unknown_tags([target], [block])
    # path != "" → блок проигнорирован.
    assert out[0].children == []


def test_write_document_stub_raises_not_implemented() -> None:
    """``write_document`` сейчас raises — full reverse-конвертер на 5.5b."""
    from gedcom_parser.writer import write_document

    with pytest.raises(NotImplementedError):
        write_document(object())


# -----------------------------------------------------------------------------
# Real corpus (gedcom_real).
# -----------------------------------------------------------------------------


_CORPUS_DIR = Path(os.environ.get("GEDCOM_TEST_CORPUS", "D:/Projects/GED"))


def _corpus_files() -> list[Path]:
    if not _CORPUS_DIR.exists():
        return []
    # Берём только небольшие файлы (< 10 МБ) для quarantine round-trip:
    # это unit-тест, не perf-benchmark; стресс-тестирование отдельно.
    return sorted(p for p in _CORPUS_DIR.glob("*.ged") if p.stat().st_size < 10_000_000)


@pytest.mark.gedcom_real
@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.name)
def test_real_corpus_round_trip_preserves_unknown_tags(path: Path) -> None:
    """На каждом файле corpus'а: quarantine → inject → write → re-parse →
    quarantine' даёт тот же набор тегов owner'ов (структурное равенство).
    """
    from gedcom_parser.parser import parse_file

    records, _ = parse_file(path)
    blocks = list(quarantine_document(records))
    if not blocks:
        # Файл без проприетарных тегов — round-trip всё равно не должен
        # ломаться, но проверка ниже становится trivial.
        pytest.skip(f"{path.name} has no proprietary tags to round-trip")

    stripped = _strip_unknown_direct_children(records, blocks)
    rebuilt = inject_unknown_tags(stripped, blocks)
    text_out = write_records(rebuilt)
    records2 = parse_text(text_out)
    blocks2 = quarantine_document(records2)

    # Сравниваем по (owner, tag) tuple — совпадение количества +
    # содержимого.
    pairs1 = sorted((b.owner_xref_id, b.owner_kind, b.record.tag) for b in blocks)
    pairs2 = sorted((b.owner_xref_id, b.owner_kind, b.record.tag) for b in blocks2)
    assert pairs1 == pairs2, (
        f"{path.name}: quarantine pairs differ after round-trip\n"
        f"  before: {len(pairs1)} pairs\n"
        f"  after:  {len(pairs2)} pairs"
    )
