"""Тесты модуля ``gedcom_parser.names`` и интеграции в ``Name``."""

from __future__ import annotations

import pytest
from gedcom_parser.entities import Name
from gedcom_parser.names import (
    NameVariant,
    detect_patronymic,
    split_compound_surname,
)
from gedcom_parser.parser import parse_text
from pydantic import ValidationError

# -----------------------------------------------------------------------------
# detect_patronymic
# -----------------------------------------------------------------------------


class TestDetectPatronymic:
    @pytest.mark.parametrize(
        ("given", "expected_given", "expected_patro"),
        [
            ("Иван Иванович", "Иван", "Иванович"),
            ("Сергей Сергеевич", "Сергей", "Сергеевич"),
            ("Мария Петровна", "Мария", "Петровна"),
            ("Анна Сергеевна", "Анна", "Сергеевна"),
            ("Дарья Никитична", "Дарья", "Никитична"),
            ("Елена Ильинична", "Елена", "Ильинична"),
        ],
    )
    def test_common_russian_patronymics(
        self, given: str, expected_given: str, expected_patro: str
    ) -> None:
        new_given, patro = detect_patronymic(given)
        assert new_given == expected_given
        assert patro == expected_patro

    def test_no_patronymic_in_western_name(self) -> None:
        new_given, patro = detect_patronymic("John")
        assert new_given == "John"
        assert patro is None

    def test_no_patronymic_in_two_western_names(self) -> None:
        new_given, patro = detect_patronymic("John Paul")
        assert new_given == "John Paul"
        assert patro is None

    def test_empty_input(self) -> None:
        assert detect_patronymic(None) == (None, None)
        assert detect_patronymic("") == ("", None)

    def test_only_patronymic_returned(self) -> None:
        # Имя — только отчество (вырожденный случай). given становится None.
        new_given, patro = detect_patronymic("Иванович")
        assert new_given is None
        assert patro == "Иванович"

    def test_short_word_not_misidentified(self) -> None:
        # "Ович" слишком короткое (стебель < 2 символов), не должно матчить.
        _new_given, patro = detect_patronymic("Ович")
        assert patro is None

    def test_case_insensitive(self) -> None:
        new_given, patro = detect_patronymic("иван иванович")
        assert new_given == "иван"
        assert patro == "иванович"


# -----------------------------------------------------------------------------
# split_compound_surname
# -----------------------------------------------------------------------------


class TestSplitCompoundSurname:
    def test_simple(self) -> None:
        assert split_compound_surname("Smith") == ("Smith",)

    def test_double(self) -> None:
        assert split_compound_surname("Petrov-Sidorov") == ("Petrov", "Sidorov")

    def test_triple(self) -> None:
        assert split_compound_surname("Иванов-Петров-Сидоров") == (
            "Иванов",
            "Петров",
            "Сидоров",
        )

    def test_strips_inner_whitespace(self) -> None:
        assert split_compound_surname("Petrov - Sidorov") == ("Petrov", "Sidorov")

    def test_drops_empty_segments(self) -> None:
        assert split_compound_surname("Smith--Jones") == ("Smith", "Jones")

    def test_none_and_empty(self) -> None:
        assert split_compound_surname(None) == ()
        assert split_compound_surname("") == ()
        assert split_compound_surname("   ") == ()


# -----------------------------------------------------------------------------
# NameVariant model
# -----------------------------------------------------------------------------


class TestNameVariantModel:
    def test_construct(self) -> None:
        v = NameVariant(value="Yitzhak", kind="romanized", type_="hebrew")
        assert v.value == "Yitzhak"
        assert v.kind == "romanized"
        assert v.type_ == "hebrew"

    def test_frozen(self) -> None:
        v = NameVariant(value="X", kind="phonetic")
        with pytest.raises(ValidationError):
            v.value = "Y"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            NameVariant(value="X", kind="phonetic", extra="bad")  # type: ignore[call-arg]

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            NameVariant(value="X", kind="other")  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Интеграция в Name.from_record
# -----------------------------------------------------------------------------


class TestNameIntegration:
    def test_patronymic_from_givn_subtag(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Иван Иванович /Петров/\n2 GIVN Иван Иванович\n2 SURN Петров\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.given == "Иван"
        assert name.patronymic == "Иванович"
        assert name.surname == "Петров"
        assert name.surnames == ("Петров",)

    def test_compound_surname(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Анна /Петрова-Сидорова/\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.surname == "Петрова-Сидорова"
        assert name.surnames == ("Петрова", "Сидорова")

    def test_patronymic_and_compound_surname(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Анна Петровна /Иванова-Сидорова/\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.given == "Анна"
        assert name.patronymic == "Петровна"
        assert name.surnames == ("Иванова", "Сидорова")

    def test_western_name_no_patronymic(self) -> None:
        # Регрессия: западные имена не должны получать ложного patronymic.
        text = "0 @I1@ INDI\n1 NAME John /Smith/\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.given == "John"
        assert name.patronymic is None
        assert name.surnames == ("Smith",)

    def test_no_surname_means_empty_surnames_tuple(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Plato\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert name.surname is None
        assert name.surnames == ()

    def test_fone_variant(self) -> None:
        text = "0 @I1@ INDI\n1 NAME יצחק /כהן/\n2 FONE Yitzhak Cohen\n3 TYPE hebrew\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert len(name.variants) == 1
        v = name.variants[0]
        assert v.kind == "phonetic"
        assert v.value == "Yitzhak Cohen"
        assert v.type_ == "hebrew"

    def test_romn_variant(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Иван /Петров/\n2 ROMN Ivan Petrov\n3 TYPE GOST\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert len(name.variants) == 1
        v = name.variants[0]
        assert v.kind == "romanized"
        assert v.value == "Ivan Petrov"
        assert v.type_ == "GOST"

    def test_multiple_variants_preserve_order(self) -> None:
        text = (
            "0 @I1@ INDI\n"
            "1 NAME Иван /Петров/\n"
            "2 FONE [ɪˈvan ˈpʲetrəf]\n"
            "3 TYPE IPA\n"
            "2 ROMN Ivan Petrov\n"
            "3 TYPE ISO9\n"
        )
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert [v.kind for v in name.variants] == ["phonetic", "romanized"]
        assert name.variants[0].type_ == "IPA"
        assert name.variants[1].type_ == "ISO9"

    def test_variant_without_type(self) -> None:
        text = "0 @I1@ INDI\n1 NAME Иван /Петров/\n2 ROMN Ivan Petrov\n"
        indi = parse_text(text)[0]
        name = Name.from_record(indi.find("NAME"))  # type: ignore[arg-type]
        assert len(name.variants) == 1
        assert name.variants[0].type_ is None
