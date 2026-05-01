"""Тесты для catalog-loader: JSON загружается, структура валидна."""

from __future__ import annotations

import pytest
from archive_service.planner.catalog import CatalogArchive, get_catalog, load_catalog


def test_catalog_loads_with_at_least_20_entries() -> None:
    """Catalog существует и содержит минимум 20 архивов (требование 15.5)."""
    catalog = load_catalog()
    assert len(catalog) >= 20
    assert all(isinstance(item, CatalogArchive) for item in catalog)


def test_catalog_includes_lodz_vital_records() -> None:
    """Łódź vital records — обязательная запись (используется в demo + тестах)."""
    catalog = get_catalog()
    lodz = next(
        (a for a in catalog if a.archive_id == "pl-aplodz-vital"),
        None,
    )
    assert lodz is not None
    assert lodz.location_country == "PL"
    assert lodz.location_city == "Łódź"
    assert lodz.time_range_start <= 1880 <= lodz.time_range_end
    # Польша 1815-1915 была в составе Российской Империи: vital records на ru.
    assert "ru" in lodz.languages


def test_catalog_country_codes_uppercase_iso2() -> None:
    """Все ``location_country`` — uppercase, длина 2 (ISO-3166 alpha-2)."""
    catalog = load_catalog()
    for archive in catalog:
        assert archive.location_country.isupper()
        assert len(archive.location_country) == 2


def test_catalog_digitization_levels_valid() -> None:
    """Все ``digitization_level`` ∈ {none, partial, full}."""
    catalog = load_catalog()
    valid = {"none", "partial", "full"}
    for archive in catalog:
        assert archive.digitization_level in valid


def test_catalog_time_ranges_consistent() -> None:
    """``time_range_start <= time_range_end`` для всех записей."""
    catalog = load_catalog()
    for archive in catalog:
        assert archive.time_range_start <= archive.time_range_end, archive.archive_id


def test_catalog_archive_ids_unique() -> None:
    """``archive_id`` — primary key, дубликаты ломают dedup в scorer'е."""
    catalog = load_catalog()
    ids = [a.archive_id for a in catalog]
    assert len(ids) == len(set(ids))


def test_catalog_languages_lowercase_iso639() -> None:
    """Все language-коды нормализованы lowercase + 2-letter ISO-639-1.

    Кроме исключений: 'la' (Latin) — допустимо, в реальности ISO-639-1 'la'.
    """
    catalog = load_catalog()
    for archive in catalog:
        for lang in archive.languages:
            assert lang.islower(), archive.archive_id
            assert 2 <= len(lang) <= 3, (archive.archive_id, lang)


def test_get_catalog_is_cached() -> None:
    """``get_catalog`` использует ``lru_cache`` — два вызова дают один объект."""
    a = get_catalog()
    b = get_catalog()
    assert a is b


def test_catalog_covers_required_regions() -> None:
    """Убеждаемся что catalog покрывает European/Russian/Israeli/American регионы."""
    catalog = load_catalog()
    countries = {a.location_country for a in catalog}
    # Минимально нужны: PL/RU/IL/US — основные регионы для AJ-генеалогии.
    for required in ("PL", "RU", "IL", "US"):
        assert required in countries, f"missing {required}"


def test_catalog_path_exists() -> None:
    """JSON-файл должен быть рядом с модулем (package-data)."""
    from archive_service.planner.catalog import _CATALOG_PATH

    assert _CATALOG_PATH.exists(), f"missing catalog at {_CATALOG_PATH}"
    assert _CATALOG_PATH.suffix == ".json"


@pytest.mark.parametrize(
    "field",
    [
        "archive_id",
        "name",
        "location_country",
        "location_city",
        "time_range_start",
        "time_range_end",
        "languages",
        "digitization_level",
    ],
)
def test_catalog_entry_has_required_field(field: str) -> None:
    """Каждая запись имеет все обязательные поля (дataclass-контракт)."""
    catalog = load_catalog()
    for archive in catalog:
        assert hasattr(archive, field), (archive.archive_id, field)
