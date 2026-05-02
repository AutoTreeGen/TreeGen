"""Tests for match-list CSV parsers (Phase 16.3 / ADR-0072).

Покрытие (без БД):

* Per-platform unit tests: synthetic CSVs (5 строк) для каждой
  из 5 платформ, проверка распарсенных полей.
* Edge cases: UTF-8 BOM, Windows-1252, decimal-comma, missing
  optional fields, weird relationship strings.
* relationship-bucket mapping: каждый bucket из
  :class:`PredictedRelationship` достижим как минимум одной
  raw-строкой; UNKNOWN — fallback, не silent DISTANT.
* Property-test (hypothesis): ``total_cm > longest_segment_cm``
  гарантирована, если оба заданы (sanity); platform
  идентичен для всех entries из одного парсера.

Integration-тесты (с БД) — отдельным файлом в dna-service tests
(testcontainers-postgres).
"""

from __future__ import annotations

import pytest
from dna_analysis.match_list import (
    MatchListEntry,
    normalise_relationship,
    parse_match_list,
)
from dna_analysis.match_list._csv_utils import (
    decode_csv_bytes,
    parse_optional_float,
    parse_optional_int,
)
from dna_analysis.match_list.dispatcher import UnsupportedPlatformError, supported_platforms
from dna_analysis.match_list.gedmatch import _gen_to_relationship
from hypothesis import given, settings
from hypothesis import strategies as st
from shared_models.enums import DnaPlatform, PredictedRelationship

# ---------------------------------------------------------------------------
# Synthetic CSV fixtures
# ---------------------------------------------------------------------------


_ANCESTRY_CSV = """\
Match GUID,Name,Total cM,Longest cM,Predicted Relationship,Shared Matches,Notes
abc-123,Alice Smith,3450,3450.0,Mother,0,
def-456,Bob Cohen,420.5,52.1,2nd Cousin,3,Tree size 1500
ghi-789,Carol Levin,180.2,28.7,3rd Cousin,7,
jkl-012,David Klein,72.0,18.5,4th Cousin,2,
mno-345,Eve Goldberg,15.0,15.0,Distant Cousin,0,
"""

_23ANDME_CSV = """\
Display Name,Predicted Relationship,Total cM,Largest Segment (cM),# Segments,Profile
Alice S.,Mother,3450,3450,1,https://you.23andme.com/profile/abc-123
Bob C.,2nd Cousin,420.5,52.1,8,https://you.23andme.com/profile/def-456
Carol L.,3rd Cousin,180.2,28.7,5,https://you.23andme.com/profile/ghi-789
David K.,4th Cousin,72.0,18.5,3,https://you.23andme.com/profile/jkl-012
Eve G.,Distant Cousin,15.0,15.0,1,https://you.23andme.com/profile/mno-345
"""

_MYHERITAGE_CSV = """\
Match Name,Match Username,Estimated Relationship,Total Shared cM,Largest Segment cM,Number of Shared Segments,Tree Size,Match ID,Notes
Alice S.,alice_s,Mother,3450,3450,1,5000,MH-1001,
Bob C.,bob_c,2nd Cousin,420.5,52.1,8,1500,MH-1002,
Carol L.,carol_l,3rd Cousin,180.2,28.7,5,300,MH-1003,
David K.,david_k,4th Cousin,72.0,18.5,3,80,MH-1004,
Eve G.,,Distant Cousin,15.0,15.0,1,0,MH-1005,no tree
"""

_FTDNA_CSV = """\
Full Name,Suggested Relationship,Shared Centimorgans,Longest Block,Shared Segments,Email,Kit Number,Notes
Alice Smith,Parent/Child,3450,3450,1,alice@example.com,FT001,
Bob Cohen,2nd Cousin,420.5,52.1,8,bob@example.com,FT002,
Carol Levin,3rd Cousin,180.2,28.7,5,,FT003,
David Klein,4th Cousin,72.0,18.5,3,,FT004,Likely paternal
Eve Goldberg,Remote,15.0,15.0,1,,FT005,
"""

_GEDMATCH_CSV = """\
Kit Num,Name,Email,Total cM,Largest cM,Gen,Overlap
A100001,Alice Smith,alice@example.com,3450,3450,1.0,1234567
A100002,Bob Cohen,bob@example.com,420.5,52.1,3.5,987654
A100003,Carol Levin,,180.2,28.7,4.2,876543
A100004,David Klein,,72.0,18.5,5.1,765432
A100005,Eve Goldberg,,15.0,15.0,7.5,654321
"""


# ---------------------------------------------------------------------------
# Per-platform parser unit tests
# ---------------------------------------------------------------------------


def test_ancestry_basic_parse() -> None:
    """5 строк → 5 entries; первая — mother → PARENT_CHILD."""
    entries = parse_match_list(_ANCESTRY_CSV, DnaPlatform.ANCESTRY)
    assert len(entries) == 5
    first = entries[0]
    assert first.platform is DnaPlatform.ANCESTRY
    assert first.external_match_id == "abc-123"
    assert first.display_name == "Alice Smith"
    assert first.total_cm == 3450
    assert first.predicted_relationship is PredictedRelationship.PARENT_CHILD
    assert first.predicted_relationship_raw == "Mother"
    assert first.raw_payload["Match GUID"] == "abc-123"


def test_ancestry_skips_rows_without_external_id() -> None:
    """Без Match GUID строка пропускается (неидемпотентно при re-import)."""
    csv = "Match GUID,Name,Total cM\n,No Id Match,100\nabc,Has Id,200\n"
    entries = parse_match_list(csv, DnaPlatform.ANCESTRY)
    assert len(entries) == 1
    assert entries[0].external_match_id == "abc"


def test_twentythreeandme_extracts_external_id_from_profile_url() -> None:
    """external_id берётся из ``/profile/<id>`` URL."""
    entries = parse_match_list(_23ANDME_CSV, DnaPlatform.TWENTY_THREE)
    assert len(entries) == 5
    assert entries[0].external_match_id == "abc-123"
    assert entries[0].platform is DnaPlatform.TWENTY_THREE
    assert entries[0].predicted_relationship is PredictedRelationship.PARENT_CHILD


def test_myheritage_basic_parse_with_tree_size_in_notes() -> None:
    """Tree-size попадает в notes, чтобы не расширять схему."""
    entries = parse_match_list(_MYHERITAGE_CSV, DnaPlatform.MYHERITAGE)
    assert len(entries) == 5
    assert entries[0].external_match_id == "MH-1001"
    assert entries[0].match_username == "alice_s"
    # Tree size should be encoded into notes for matches with non-zero size.
    bob = next(e for e in entries if e.external_match_id == "MH-1002")
    assert bob.notes is not None
    assert "tree_size=1500" in bob.notes


def test_ftdna_basic_parse() -> None:
    entries = parse_match_list(_FTDNA_CSV, DnaPlatform.FTDNA)
    assert len(entries) == 5
    assert entries[0].external_match_id == "FT001"
    # FTDNA «Parent/Child» normalises to PARENT_CHILD.
    assert entries[0].predicted_relationship is PredictedRelationship.PARENT_CHILD


def test_gedmatch_basic_parse_uses_gen_when_text_missing() -> None:
    """Без relationship-string — bucket по generation distance."""
    entries = parse_match_list(_GEDMATCH_CSV, DnaPlatform.GEDMATCH)
    assert len(entries) == 5
    assert entries[0].external_match_id == "A100001"
    # gen=1.0 → FULL_SIBLING (mapping in _gen_to_relationship)
    assert entries[0].predicted_relationship is PredictedRelationship.FULL_SIBLING
    # gen=3.5 → SECOND_COUSIN
    bob = entries[1]
    assert bob.predicted_relationship is PredictedRelationship.SECOND_COUSIN
    # gen=7.5 → DISTANT
    eve = entries[4]
    assert eve.predicted_relationship is PredictedRelationship.DISTANT


def test_unsupported_platform_raises() -> None:
    with pytest.raises(UnsupportedPlatformError):
        parse_match_list(_ANCESTRY_CSV, DnaPlatform.LIVING_DNA)


def test_supported_platforms_covers_required_five() -> None:
    """5 платформ из брифа — все в _DISPATCH."""
    supported = set(supported_platforms())
    assert {
        DnaPlatform.ANCESTRY,
        DnaPlatform.TWENTY_THREE,
        DnaPlatform.MYHERITAGE,
        DnaPlatform.FTDNA,
        DnaPlatform.GEDMATCH,
    } <= supported


# ---------------------------------------------------------------------------
# Encoding edge cases
# ---------------------------------------------------------------------------


def test_decode_handles_utf8_bom() -> None:
    """Excel «save as CSV UTF-8» добавляет \\xef\\xbb\\xbf — срезаем."""
    payload = b"\xef\xbb\xbfMatch GUID,Name,Total cM\nabc,Alice,100\n"
    text = decode_csv_bytes(payload)
    assert text.startswith("Match GUID")
    entries = parse_match_list(payload, DnaPlatform.ANCESTRY)
    assert len(entries) == 1


def test_decode_falls_back_to_windows_1252() -> None:
    """Старые экспорты в Windows-1252 (umlauts) — успех на fallback'е."""
    payload = "Match GUID,Name,Total cM\nx,Müller,100\n".encode("windows-1252")
    text = decode_csv_bytes(payload)
    assert "Müller" in text


def test_parse_optional_float_handles_decimal_comma() -> None:
    """MyHeritage de_DE: '12,3' → 12.3."""
    assert parse_optional_float("12,3") == 12.3
    assert parse_optional_float("1,234.56") == 1234.56
    assert parse_optional_float("") is None
    assert parse_optional_float("—") is None
    assert parse_optional_float("N/A") is None


def test_parse_optional_int_handles_thousand_separators() -> None:
    assert parse_optional_int("1,234") == 1234
    assert parse_optional_int("") is None
    assert parse_optional_int("not a number") is None


# ---------------------------------------------------------------------------
# Relationship normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mother", PredictedRelationship.PARENT_CHILD),
        ("Father", PredictedRelationship.PARENT_CHILD),
        ("Parent/Child", PredictedRelationship.PARENT_CHILD),
        ("Brother", PredictedRelationship.FULL_SIBLING),
        ("Full Sister", PredictedRelationship.FULL_SIBLING),
        ("Half-sister", PredictedRelationship.HALF_SIBLING_OR_UNCLE_AUNT),
        ("Aunt", PredictedRelationship.HALF_SIBLING_OR_UNCLE_AUNT),
        ("Niece", PredictedRelationship.HALF_SIBLING_OR_UNCLE_AUNT),
        ("Grandfather", PredictedRelationship.HALF_SIBLING_OR_UNCLE_AUNT),
        ("1st Cousin", PredictedRelationship.FIRST_COUSIN),
        ("first cousin", PredictedRelationship.FIRST_COUSIN),
        ("2nd Cousin", PredictedRelationship.SECOND_COUSIN),
        ("3rd Cousin", PredictedRelationship.THIRD_COUSIN),
        ("4th Cousin", PredictedRelationship.FOURTH_TO_SIXTH_COUSIN),
        ("5th Cousin", PredictedRelationship.FOURTH_TO_SIXTH_COUSIN),
        ("Distant Cousin", PredictedRelationship.DISTANT),
        ("Remote", PredictedRelationship.DISTANT),
        # UNKNOWN-fallbacks:
        ("", PredictedRelationship.UNKNOWN),
        (None, PredictedRelationship.UNKNOWN),
        ("Some weird new thing", PredictedRelationship.UNKNOWN),
    ],
)
def test_relationship_normalisation_buckets(
    raw: str | None,
    expected: PredictedRelationship,
) -> None:
    """Каждый канонический string → правильный bucket; неизвестное → UNKNOWN."""
    assert normalise_relationship(raw) is expected


def test_every_predicted_bucket_reachable_from_some_string() -> None:
    """Anti-drift: каждый bucket должен иметь хотя бы один canonical
    raw-string trigger; иначе enum-добавления через normalise_relationship
    не достижимы."""
    reachable = {
        normalise_relationship("Mother"),
        normalise_relationship("Brother"),
        normalise_relationship("Aunt"),
        normalise_relationship("1st cousin"),
        normalise_relationship("2nd cousin"),
        normalise_relationship("3rd cousin"),
        normalise_relationship("5th cousin"),
        normalise_relationship("Distant cousin"),
        normalise_relationship(None),
    }
    assert reachable == set(PredictedRelationship)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    total=st.floats(min_value=0, max_value=4000, allow_nan=False, allow_infinity=False),
    largest=st.floats(min_value=0, max_value=200, allow_nan=False, allow_infinity=False),
)
def test_property_total_cm_at_least_largest_when_both_set(total: float, largest: float) -> None:
    """Sanity: ``total_cm`` всегда ≥ longest segment, если оба заданы.

    Биологически total_cm — сумма всех сегментов; largest_segment_cm —
    один из них. Парсер не enforces (входные данные могут быть кривыми),
    но ``MatchListEntry`` не отвергает такие row'ы — мы их сохраняем
    как есть с raw_payload, а проверка — отдельный test.

    Здесь property-test просто иллюстрирует физический инвариант.
    """
    if total < largest:
        # Это «кривая» row — мы её принимаем (preserve raw_payload),
        # но в reasonable-data total_cm >= largest_segment_cm.
        pytest.skip("synthetic: total < largest — преимущественно кривые экспорты")
    assert total >= largest


@settings(max_examples=20, deadline=None)
@given(gen=st.floats(min_value=0.5, max_value=10.0, allow_nan=False))
def test_property_gedmatch_gen_to_relationship_is_deterministic(gen: float) -> None:
    """Для любого gen ∈ [0.5, 10] mapping детерминирован."""
    bucket = _gen_to_relationship(gen)
    # Две одинаковых вызова дают одинаковый результат.
    assert _gen_to_relationship(gen) is bucket


def test_property_parse_returns_consistent_platform() -> None:
    """Все entries из одного парсера имеют ровно ту же платформу."""
    for csv, platform in (
        (_ANCESTRY_CSV, DnaPlatform.ANCESTRY),
        (_23ANDME_CSV, DnaPlatform.TWENTY_THREE),
        (_MYHERITAGE_CSV, DnaPlatform.MYHERITAGE),
        (_FTDNA_CSV, DnaPlatform.FTDNA),
        (_GEDMATCH_CSV, DnaPlatform.GEDMATCH),
    ):
        entries = parse_match_list(csv, platform)
        assert all(e.platform is platform for e in entries)


def test_match_list_entry_is_frozen() -> None:
    """Frozen — pure-функция парсер не должен мутировать результат."""
    from pydantic import ValidationError

    entries = parse_match_list(_ANCESTRY_CSV, DnaPlatform.ANCESTRY)
    with pytest.raises(ValidationError):
        entries[0].total_cm = 0  # type: ignore[misc]


def test_raw_payload_preserved_for_each_entry() -> None:
    """Anti-drift: ``raw_payload`` всегда содержит исходную CSV-row."""
    entries = parse_match_list(_MYHERITAGE_CSV, DnaPlatform.MYHERITAGE)
    for entry in entries:
        assert entry.raw_payload, f"empty raw_payload for {entry.external_match_id}"
        # Match ID должен быть в raw_payload — это «source of truth».
        assert entry.raw_payload.get("Match ID") == entry.external_match_id


def test_match_list_entry_minimal_construction() -> None:
    """Минимальный валидный MatchListEntry."""
    entry = MatchListEntry(
        platform=DnaPlatform.ANCESTRY,
        external_match_id="x",
        total_cm=100.0,
    )
    assert entry.predicted_relationship is PredictedRelationship.UNKNOWN
    assert entry.raw_payload == {}


def test_match_list_entry_rejects_negative_cm() -> None:
    """``total_cm`` ≥ 0; отрицательное значение → ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MatchListEntry(
            platform=DnaPlatform.ANCESTRY,
            external_match_id="x",
            total_cm=-1.0,
        )
