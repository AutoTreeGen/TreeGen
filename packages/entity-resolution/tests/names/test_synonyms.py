"""Tests for ICP-anchor synonym loader (Phase 15.10)."""

from __future__ import annotations

import pytest
from entity_resolution.names import load_icp_synonyms
from entity_resolution.names.synonyms import canonical_form


def test_canonical_form_lowercase_strip() -> None:
    assert canonical_form("  Levitin  ") == "levitin"


def test_canonical_form_unidecode_cyrillic() -> None:
    # Левитин через unidecode → Levitin → lower → levitin.
    assert canonical_form("Левитин") == "levitin"


def test_canonical_form_unidecode_polish() -> None:
    assert canonical_form("Łukasz") == "lukasz"


def test_canonical_form_unidecode_german() -> None:
    assert canonical_form("Müller") == "muller"


class TestLoadIcpSynonyms:
    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        load_icp_synonyms.cache_clear()

    def test_returns_dict_with_canonical_keys(self) -> None:
        idx = load_icp_synonyms()
        assert isinstance(idx, dict)
        assert idx, "ICP synonyms file must not be empty"

    def test_levitin_anchor_includes_cyrillic_and_hebrew(self) -> None:
        idx = load_icp_synonyms()
        anchor = idx[canonical_form("Levitin")]
        # Должны быть и Latin, и Cyrillic, и Hebrew variants.
        assert any("Levitin" in v for v in anchor)
        assert any(any(0x0400 <= ord(c) <= 0x04FF for c in v) for v in anchor)
        assert any(any(0x0590 <= ord(c) <= 0x05FF for c in v) for v in anchor)

    def test_lookup_via_cyrillic_form(self) -> None:
        """Cyrillic форма должна резолвиться в тот же anchor что и Latin."""
        idx = load_icp_synonyms()
        latin_set = idx[canonical_form("Levitin")]
        cyrillic_set = idx[canonical_form("Левитин")]
        assert latin_set == cyrillic_set

    def test_minimum_thirty_anchors(self) -> None:
        """V1 contract: ≥30 distinct anchor groups."""
        idx = load_icp_synonyms()
        # Каждая anchor-группа дублирует variants -> мап содержит больше
        # entries чем groups; считаем уникальные frozenset'ы.
        groups = set(idx.values())
        assert len(groups) >= 30, f"V1 contract requires ≥30 anchor groups, found {len(groups)}"

    def test_cache_returns_same_object(self) -> None:
        a = load_icp_synonyms()
        b = load_icp_synonyms()
        assert a is b  # @lru_cache(maxsize=1) → identical object
