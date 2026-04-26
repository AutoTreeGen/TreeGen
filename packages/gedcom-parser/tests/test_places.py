"""Тесты модуля ``gedcom_parser.places`` и интеграции в ``Event.place``."""

from __future__ import annotations

import pytest
from gedcom_parser.entities import Event
from gedcom_parser.parser import parse_text
from gedcom_parser.places import (
    ParsedPlace,
    PlaceVariant,
    parse_coordinate,
    parse_place_levels,
)
from pydantic import ValidationError

# -----------------------------------------------------------------------------
# parse_place_levels
# -----------------------------------------------------------------------------


class TestParsePlaceLevels:
    def test_three_levels(self) -> None:
        assert parse_place_levels("Slonim, Grodno Governorate, Russian Empire") == (
            "Slonim",
            "Grodno Governorate",
            "Russian Empire",
        )

    def test_single_level(self) -> None:
        assert parse_place_levels("Vilnius") == ("Vilnius",)

    def test_strips_inner_whitespace(self) -> None:
        # Реальные файлы: разные пробелы вокруг запятых.
        assert parse_place_levels("Slonim,Grodno , Russia") == ("Slonim", "Grodno", "Russia")

    def test_drops_empty_segments(self) -> None:
        # Двойная запятая часто бывает в кривых экспортах.
        assert parse_place_levels("Slonim,, Russia") == ("Slonim", "Russia")

    def test_empty_and_none(self) -> None:
        assert parse_place_levels(None) == ()
        assert parse_place_levels("") == ()
        assert parse_place_levels("   ") == ()

    def test_unicode(self) -> None:
        assert parse_place_levels("Слоним, Минская губерния, Российская империя") == (
            "Слоним",
            "Минская губерния",
            "Российская империя",
        )


# -----------------------------------------------------------------------------
# parse_coordinate
# -----------------------------------------------------------------------------


class TestParseCoordinate:
    @pytest.mark.parametrize(
        ("value", "kind", "expected"),
        [
            ("N51.5074", "lat", 51.5074),
            ("S33.8688", "lat", -33.8688),
            ("E151.2093", "long", 151.2093),
            ("W0.1278", "long", -0.1278),
            ("n51.5074", "lat", 51.5074),  # lowercase
            ("N 51.5074", "lat", 51.5074),  # whitespace
            ("51.5074", "lat", 51.5074),  # no prefix
            ("-0.1278", "long", -0.1278),
            ("+34.05", "lat", 34.05),
        ],
    )
    def test_basic_forms(self, value: str, kind: str, expected: float) -> None:
        assert parse_coordinate(value, kind) == pytest.approx(expected)  # type: ignore[arg-type]

    def test_empty_returns_none(self) -> None:
        assert parse_coordinate(None, "lat") is None
        assert parse_coordinate("", "lat") is None
        assert parse_coordinate("   ", "lat") is None

    def test_garbage_returns_none(self) -> None:
        assert parse_coordinate("foo", "lat") is None
        assert parse_coordinate("N", "lat") is None

    def test_wrong_axis_letter(self) -> None:
        # E51 не должно матчить как широта.
        assert parse_coordinate("E51", "lat") is None
        # N51 не должно матчить как долгота.
        assert parse_coordinate("N51", "long") is None


# -----------------------------------------------------------------------------
# PlaceVariant model
# -----------------------------------------------------------------------------


class TestPlaceVariantModel:
    def test_construct(self) -> None:
        v = PlaceVariant(value="Wilno", kind="phonetic", type_="polish")
        assert v.value == "Wilno"
        assert v.kind == "phonetic"
        assert v.type_ == "polish"

    def test_frozen(self) -> None:
        v = PlaceVariant(value="X", kind="romanized")
        with pytest.raises(ValidationError):
            v.value = "Y"  # type: ignore[misc]


# -----------------------------------------------------------------------------
# ParsedPlace.from_record и интеграция в Event
# -----------------------------------------------------------------------------


class TestParsedPlaceFromRecord:
    def test_simple_place(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 PLAC Slonim, Grodno Governorate, Russian Empire\n"
        indi = parse_text(text)[0]
        plac = indi.find("BIRT").find("PLAC")  # type: ignore[union-attr]
        place = ParsedPlace.from_record(plac)  # type: ignore[arg-type]
        assert place.raw == "Slonim, Grodno Governorate, Russian Empire"
        assert place.levels == ("Slonim", "Grodno Governorate", "Russian Empire")
        assert place.latitude is None
        assert place.longitude is None

    def test_place_with_map(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 BIRT\n"
            "2 PLAC Vilnius, Lithuania\n"
            "3 MAP\n"
            "4 LATI N54.6872\n"
            "4 LONG E25.2797\n"
        )
        indi = parse_text(text)[0]
        plac = indi.find("BIRT").find("PLAC")  # type: ignore[union-attr]
        place = ParsedPlace.from_record(plac)  # type: ignore[arg-type]
        assert place.latitude == pytest.approx(54.6872)
        assert place.longitude == pytest.approx(25.2797)

    def test_place_with_form(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 PLAC New York, NY, USA\n3 FORM City, State, Country\n"
        indi = parse_text(text)[0]
        plac = indi.find("BIRT").find("PLAC")  # type: ignore[union-attr]
        place = ParsedPlace.from_record(plac)  # type: ignore[arg-type]
        assert place.form == "City, State, Country"

    def test_place_with_variants(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 BIRT\n"
            "2 PLAC Wilno, Poland\n"
            "3 FONE Vilnius\n"
            "4 TYPE lithuanian\n"
            "3 ROMN Vilna\n"
            "4 TYPE yiddish\n"
        )
        indi = parse_text(text)[0]
        plac = indi.find("BIRT").find("PLAC")  # type: ignore[union-attr]
        place = ParsedPlace.from_record(plac)  # type: ignore[arg-type]
        assert len(place.variants) == 2
        assert place.variants[0].kind == "phonetic"
        assert place.variants[0].value == "Vilnius"
        assert place.variants[0].type_ == "lithuanian"
        assert place.variants[1].kind == "romanized"
        assert place.variants[1].value == "Vilna"


class TestEventPlaceIntegration:
    def test_event_place_populated(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 PLAC Slonim, Russian Empire\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.place_raw == "Slonim, Russian Empire"
        assert event.place is not None
        assert event.place.levels == ("Slonim", "Russian Empire")

    def test_event_without_place(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 DATE 1850\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.place_raw is None
        assert event.place is None

    def test_event_place_with_coords(self) -> None:
        text = "0 @I1@ INDI\n1 DEAT\n2 PLAC London, UK\n3 MAP\n4 LATI N51.5074\n4 LONG W0.1278\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("DEAT"))  # type: ignore[arg-type]
        assert event.place is not None
        assert event.place.latitude == pytest.approx(51.5074)
        assert event.place.longitude == pytest.approx(-0.1278)


class TestParsedPlaceModel:
    def test_frozen(self) -> None:
        p = ParsedPlace(raw="X", levels=("X",))
        with pytest.raises(ValidationError):
            p.raw = "Y"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ParsedPlace(raw="X", unknown="bad")  # type: ignore[call-arg]
