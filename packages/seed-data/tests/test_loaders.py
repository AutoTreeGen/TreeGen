"""Smoke tests for JSON loaders (Phase 22.1b).

Не требуют БД — pure parsing. Покрывают:

* Canonical seeds (committed, всегда доступны):
  - country v1 (14 countries)
  - fabrication patterns (61 patterns)
* Synthetic mini-fixtures (для shape coverage local-only seeds):
  - synthetic_surname_clusters.json (10 clusters + 3 transliteration entries)
  - synthetic_places.json (5 places, edge case empty lat_lon)
  - synthetic_country_extension.json (1 country)
"""

from __future__ import annotations

from pathlib import Path

from seed_data.config import canonical_paths
from seed_data.loaders import (
    load_countries,
    load_fabrication_patterns,
    load_places,
    load_surnames,
)

# ---------------------------------------------------------------------------
# Canonical seeds (committed)
# ---------------------------------------------------------------------------


def test_canonical_country_v1_loads() -> None:
    """14 countries from canonical v1 file."""
    path = canonical_paths()["country_v1"]
    assert path.exists(), f"canonical seed missing: {path}"
    countries = load_countries(path)
    assert len(countries) == 14
    # Most entries are ISO 3166-1 alpha-2; v1 file has at least one historical
    # composite code (e.g. "AT-GAL"). Validator allows up to 8 chars.
    assert all(2 <= len(c.iso2) <= 8 for c in countries)
    assert all(c.country for c in countries)
    # All v1 records have v2_batch=None.
    assert all(c.v2_batch is None for c in countries)


def test_canonical_fabrication_loads() -> None:
    """61 fabrication patterns across 7 categories."""
    path = canonical_paths()["fabrication"]
    assert path.exists(), f"canonical seed missing: {path}"
    patterns = load_fabrication_patterns(path)
    assert len(patterns) == 61
    categories = {p.category for p in patterns}
    assert categories == {
        "rabbinical_fantasy",
        "royal_descent_fake",
        "pedigree_collapse",
        "same_name_different_person",
        "patronymic_confusion",
        "cross_religious_confusion",
        "holocaust_reconstruction_error",
    }
    # All confidence_when_flagged values fall in (0, 1].
    for p in patterns:
        assert p.confidence_when_flagged is None or 0 < p.confidence_when_flagged <= 1


# ---------------------------------------------------------------------------
# Synthetic mini-fixtures (CI coverage of local-only file shapes)
# ---------------------------------------------------------------------------


def test_synthetic_surname_clusters_loads(synthetic_surname_path: Path) -> None:
    clusters, translit = load_surnames(synthetic_surname_path)
    assert len(clusters) == 10
    # First cluster sanity-check.
    first = clusters[0]
    assert first.canonical == "Doe"
    assert first.community_scope == "Anglophone"
    assert first.rank_within_scope == 1
    assert first.variants_latin == ["Doe", "Doh", "Doh-Smith"]
    # Cross-script cluster.
    test_cohen = next(c for c in clusters if c.canonical == "TestCohen")
    assert test_cohen.variants_cyrillic == ["ТестКоэн"]
    assert test_cohen.variants_hebrew == ["טסטכהן"]
    assert test_cohen.variants_yiddish == ["טעסטקאהן"]
    # Transliteration table.
    assert len(translit) == 3
    russian_ivanov = next(t for t in translit if t.source_form == "ТестИванов")
    assert russian_ivanov.target_forms == {
        "latin_bgn": ["TestIvanov"],
        "latin_iso9": ["TestIvanov"],
    }


def test_synthetic_places_loads(synthetic_places_path: Path) -> None:
    places = load_places(synthetic_places_path)
    assert len(places) == 5
    brest = places[0]
    assert brest.old_name == "TestBrest-Litovsk"
    assert brest.modern_country == "TestBelarus"
    assert brest.lat == 52.0976
    assert brest.lon == 23.7341
    assert brest.coordinate_precision == "exact_or_high_confidence"
    # Edge case: empty lat_lon → None coords.
    no_coords = next(p for p in places if p.old_name == "TestPlaceNoCoords")
    assert no_coords.lat is None
    assert no_coords.lon is None
    assert no_coords.coordinate_precision == "approximate_seed"


def test_synthetic_country_extension_loads(synthetic_country_extension_path: Path) -> None:
    countries = load_countries(synthetic_country_extension_path, v2_batch="ext_synthetic")
    assert len(countries) == 1
    assert countries[0].iso2 == "ZZ"
    assert countries[0].country == "TestSyntheticland"
    assert countries[0].v2_batch == "ext_synthetic"
    # raw_data preserves all original fields.
    assert countries[0].raw_data["AutoTreeGen_schema_notes"].startswith("Fixture-only")


def test_country_loader_uppercases_iso2() -> None:
    """Defensive: ISO 3166-1 alpha-2 must be upper case in DB."""
    # canonical v1 may already be upper, but validator should preserve consistency.
    countries = load_countries(canonical_paths()["country_v1"])
    for c in countries:
        assert c.iso2 == c.iso2.upper()
