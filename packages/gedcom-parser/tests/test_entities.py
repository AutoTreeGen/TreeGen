"""Тесты семантических моделей (модуль ``gedcom_parser.entities``)."""

from __future__ import annotations

import pytest
from gedcom_parser.entities import (
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
