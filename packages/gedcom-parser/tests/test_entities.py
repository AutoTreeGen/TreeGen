"""Тесты семантических моделей (модуль ``gedcom_parser.entities``)."""

from __future__ import annotations

import pytest
from gedcom_parser.entities import (
    Citation,
    Event,
    Family,
    Header,
    MultimediaObject,
    Name,
    Note,
    Person,
    Repository,
    Source,
    Submitter,
)
from gedcom_parser.parser import parse_text
from pydantic import ValidationError


class TestNameFromValue:
    """Базовое расщепление ``/Surname/``-нотации."""

    def test_given_and_surname(self) -> None:
        text = "0 @I1@ INDI\n1 NAME John /Smith/\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.value == "John /Smith/"
        assert name.given == "John"
        assert name.surname == "Smith"
        assert name.suffix is None or name.suffix == ""

    def test_only_surname(self) -> None:
        text = "0 @I1@ INDI\n1 NAME /Smith/\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        # given пустой → None.
        assert name.given is None
        assert name.surname == "Smith"

    def test_only_given(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Plato\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.given == "Plato"
        assert name.surname is None

    def test_with_suffix(self) -> None:
        text = "0 @I1@ INDI\n1 NAME John /Smith/ Jr.\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.given == "John"
        assert name.surname == "Smith"
        assert name.suffix == "Jr."

    def test_subtags_take_precedence(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 NAME Ioann /Kuznetsov/\n"
            "2 GIVN John\n"
            "2 SURN Smith\n"
            "2 NICK Jack\n"
            "2 NPFX Dr.\n"
            "2 NSFX III\n"
        )
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        # Подтеги имеют приоритет над расщеплением value.
        assert name.given == "John"
        assert name.surname == "Smith"
        assert name.nickname == "Jack"
        assert name.prefix == "Dr."
        assert name.suffix == "III"


class TestEventFromRecord:
    def test_birt_with_date_and_place(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 DATE 1 JAN 1850\n2 PLAC Slonim, Russian Empire\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.tag == "BIRT"
        assert event.date_raw == "1 JAN 1850"
        assert event.place_raw == "Slonim, Russian Empire"

    def test_event_with_xref_notes(self) -> None:
        text = "0 @I1@ INDI\n1 DEAT\n2 NOTE @N1@\n2 SOUR @S1@\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("DEAT"))  # type: ignore[arg-type]
        assert event.notes_xrefs == ("N1",)
        assert event.sources_xrefs == ("S1",)

    def test_inline_note_skipped(self) -> None:
        # Inline-заметка `1 NOTE текст` не является xref'ом — её игнорируем.
        text = "0 @I1@ INDI\n1 BIRT\n2 NOTE just an inline note\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.notes_xrefs == ()


class TestPersonFromRecord:
    def test_minimal_person(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        i1 = next(r for r in records if r.xref_id == "I1")
        person = Person.from_record(i1)
        assert person.xref_id == "I1"
        assert person.sex == "M"
        assert len(person.names) == 1
        assert person.names[0].surname == "Smith"
        assert person.families_as_spouse == ("F1",)
        assert person.families_as_child == ()
        # Одно событие — BIRT.
        assert len(person.events) == 1
        assert person.events[0].tag == "BIRT"

    def test_indi_without_xref_raises(self) -> None:
        # Защитный кейс: AST в принципе допускает INDI без xref (например,
        # при ошибке в файле). При сборке семантической сущности это —
        # ValidationError или ValueError.
        text = "0 INDI\n1 NAME Anonymous\n"
        rec = parse_text(text)[0]
        with pytest.raises((ValueError, ValidationError)):
            Person.from_record(rec)


class TestFamilyFromRecord:
    def test_minimal_family(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        f1 = next(r for r in records if r.xref_id == "F1")
        family = Family.from_record(f1)
        assert family.xref_id == "F1"
        assert family.husband_xref == "I1"
        assert family.wife_xref == "I2"
        assert family.children_xrefs == ("I3",)

    def test_marr_event(self) -> None:
        text = "0 @F1@ FAM\n1 HUSB @I1@\n1 WIFE @I2@\n1 MARR\n2 DATE 1 JAN 1880\n2 PLAC Vilnius\n"
        f1 = parse_text(text)[0]
        family = Family.from_record(f1)
        assert len(family.events) == 1
        assert family.events[0].tag == "MARR"
        assert family.events[0].date_raw == "1 JAN 1880"


class TestSimpleEntities:
    def test_source(self) -> None:
        text = (
            "0 @S1@ SOUR\n"
            "1 TITL Lithuanian Census 1897\n"
            "1 AUTH Imperial Office\n"
            "1 PUBL 1898\n"
            "1 REPO @R1@\n"
        )
        rec = parse_text(text)[0]
        src = Source.from_record(rec)
        assert src.xref_id == "S1"
        assert src.title == "Lithuanian Census 1897"
        assert src.author == "Imperial Office"
        assert src.publication == "1898"
        assert src.repository_xref == "R1"

    def test_note(self) -> None:
        text = "0 @N1@ NOTE Some scholarly observation\n"
        rec = parse_text(text)[0]
        note = Note.from_record(rec)
        assert note.xref_id == "N1"
        assert note.text == "Some scholarly observation"

    def test_multimedia_object(self) -> None:
        text = "0 @O1@ OBJE\n1 FILE photos/grave.jpg\n2 FORM jpg\n1 TITL Tombstone in Slonim\n"
        rec = parse_text(text)[0]
        obj = MultimediaObject.from_record(rec)
        assert obj.file == "photos/grave.jpg"
        assert obj.format_ == "jpg"
        assert obj.title == "Tombstone in Slonim"

    def test_repository(self) -> None:
        text = "0 @R1@ REPO\n1 NAME Vilnius Archive\n1 ADDR Tilto 12\n"
        rec = parse_text(text)[0]
        repo = Repository.from_record(rec)
        assert repo.xref_id == "R1"
        assert repo.name == "Vilnius Archive"
        assert repo.address_raw == "Tilto 12"

    def test_submitter(self) -> None:
        text = "0 @U1@ SUBM\n1 NAME Vladimir\n"
        rec = parse_text(text)[0]
        subm = Submitter.from_record(rec)
        assert subm.xref_id == "U1"
        assert subm.name == "Vladimir"

    def test_header(self, minimal_ged_text: str) -> None:
        head_rec = parse_text(minimal_ged_text)[0]
        header = Header.from_record(head_rec)
        assert header.gedcom_version == "5.5.5"
        assert header.gedcom_form == "LINEAGE-LINKED"
        assert header.char == "UTF-8"
        assert header.source_system == "AutoTreeGen"
        assert header.source_version == "0.1.0"
        assert header.submitter_xref == "U1"
        assert header.date_raw == "25 APR 2026"


class TestFrozen:
    """Сущности — frozen: попытка переприсвоить поле должна валиться."""

    def test_person_is_frozen(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        i1 = next(r for r in records if r.xref_id == "I1")
        person = Person.from_record(i1)
        with pytest.raises(ValidationError):
            person.sex = "F"  # type: ignore[misc]

    def test_event_is_frozen(self) -> None:
        event = Event(tag="BIRT", date_raw="1 JAN 1850")
        with pytest.raises(ValidationError):
            event.date_raw = "2 JAN 1850"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Event(tag="BIRT", unknown_field="x")  # type: ignore[call-arg]


class TestCitations:
    """SOURCE_CITATION sub-tags exposed via :class:`Citation`."""

    def test_citation_with_page_and_quay(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR @S1@\n3 PAGE p. 42\n3 QUAY 3\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert len(event.citations) == 1
        c = event.citations[0]
        assert c.source_xref == "S1"
        assert c.inline_text is None
        assert c.page == "p. 42"
        assert c.quality == 3

    def test_citation_with_xref_and_inline_notes(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 DEAT\n"
            "2 SOUR @S1@\n"
            "3 NOTE @N1@\n"
            "3 NOTE Inline observation about evidence\n"
        )
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("DEAT"))  # type: ignore[arg-type]
        c = event.citations[0]
        assert c.notes_xrefs == ("N1",)
        assert c.notes_inline == ("Inline observation about evidence",)

    def test_citation_with_event_and_role(self) -> None:
        text = "0 @I1@ INDI\n1 DEAT\n2 SOUR @S1@\n3 EVEN BIRT\n4 ROLE FATH\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("DEAT"))  # type: ignore[arg-type]
        c = event.citations[0]
        assert c.event_type == "BIRT"
        assert c.event_role == "FATH"

    def test_citation_with_data_date_and_text(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 BIRT\n"
            "2 SOUR @S1@\n"
            "3 DATA\n"
            "4 DATE 12 MAR 1900\n"
            "4 TEXT first excerpt\n"
            "4 TEXT second excerpt\n"
        )
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        c = event.citations[0]
        assert c.data_date_raw == "12 MAR 1900"
        assert c.data_text == "first excerpt\nsecond excerpt"

    def test_multiple_citations_per_event(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 BIRT\n"
            "2 SOUR @S1@\n"
            "3 PAGE p. 42\n"
            "2 SOUR @S2@\n"
            "3 PAGE folio 7\n"
            "3 QUAY 2\n"
        )
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert len(event.citations) == 2
        assert event.citations[0].source_xref == "S1"
        assert event.citations[0].page == "p. 42"
        assert event.citations[1].source_xref == "S2"
        assert event.citations[1].page == "folio 7"
        assert event.citations[1].quality == 2

    def test_citation_with_inline_source_text(self) -> None:
        # SOUR может быть inline-текстом, без xref. ``source_xref`` тогда None.
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR Family bible kept by Anna\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        c = event.citations[0]
        assert c.source_xref is None
        assert c.inline_text == "Family bible kept by Anna"

    def test_quay_out_of_range_becomes_none(self) -> None:
        # Невалидное QUAY (за пределами 0..3) → quality=None, цитата не теряется.
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR @S1@\n3 QUAY 99\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        c = event.citations[0]
        assert c.source_xref == "S1"
        assert c.quality is None

    def test_quay_non_digit_becomes_none(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR @S1@\n3 QUAY high\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.citations[0].quality is None

    def test_citation_obje_xrefs(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR @S1@\n3 OBJE @O1@\n3 OBJE @O2@\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.citations[0].objects_xrefs == ("O1", "O2")

    def test_person_level_citations(self) -> None:
        # SOUR может быть прямым ребёнком INDI (не только под событием).
        text = "0 @I1@ INDI\n1 NAME John /Smith/\n1 SOUR @S1@\n2 PAGE general bio\n"
        indi = parse_text(text)[0]
        person = Person.from_record(indi)
        assert len(person.citations) == 1
        assert person.citations[0].source_xref == "S1"
        assert person.citations[0].page == "general bio"

    def test_family_level_citations(self) -> None:
        text = "0 @F1@ FAM\n1 HUSB @I1@\n1 WIFE @I2@\n1 SOUR @S1@\n2 PAGE marriage register\n"
        f1 = parse_text(text)[0]
        family = Family.from_record(f1)
        assert len(family.citations) == 1
        assert family.citations[0].page == "marriage register"

    def test_backwards_compat_sources_xrefs_still_populated(self) -> None:
        # Существующее API `sources_xrefs` должно по-прежнему работать.
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR @S1@\n3 PAGE p. 1\n2 SOUR @S2@\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.sources_xrefs == ("S1", "S2")
        # И параллельно citations покрывают то же.
        assert tuple(c.source_xref for c in event.citations) == ("S1", "S2")

    def test_backwards_compat_inline_sour_skipped_in_sources_xrefs(self) -> None:
        # Inline-source без xref не попадает в `sources_xrefs` (так было),
        # но виден через `citations` (через inline_text).
        text = "0 @I1@ INDI\n1 BIRT\n2 SOUR Family bible\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.sources_xrefs == ()
        assert len(event.citations) == 1
        assert event.citations[0].inline_text == "Family bible"

    def test_citation_is_frozen(self) -> None:
        c = Citation(source_xref="S1", page="p. 1")
        with pytest.raises(ValidationError):
            c.page = "p. 2"  # type: ignore[misc]
