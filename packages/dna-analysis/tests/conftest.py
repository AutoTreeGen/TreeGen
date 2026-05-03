"""Pytest-фикстуры для dna-analysis.

Все генераторы синтетических DNA-данных — в `tests/_generators.py`,
чтобы их можно было импортировать из тестов напрямую (для проверок
детерминизма / regen). См. ADR-0012 §«Privacy guards в коде».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ._generators import (
    generate_synthetic_23andme,
    generate_synthetic_ancestry,
    generate_synthetic_ftdna,
    generate_synthetic_livingdna,
    generate_synthetic_myheritage,
)


@pytest.fixture
def synthetic_23andme_content() -> str:
    """Свежесгенерированный синтетический 23andMe файл (100 SNP)."""
    return generate_synthetic_23andme()


@pytest.fixture
def synthetic_ancestry_content() -> str:
    """Свежесгенерированный синтетический Ancestry v2 файл (100 SNP)."""
    return generate_synthetic_ancestry()


@pytest.fixture
def synthetic_myheritage_content() -> str:
    """Свежесгенерированный синтетический MyHeritage файл (100 SNP)."""
    return generate_synthetic_myheritage()


@pytest.fixture
def synthetic_ftdna_content() -> str:
    """Свежесгенерированный синтетический FTDNA Family Finder файл (100 SNP)."""
    return generate_synthetic_ftdna()


@pytest.fixture
def synthetic_livingdna_content() -> str:
    """Свежесгенерированный синтетический LivingDNA файл (100 SNP)."""
    return generate_synthetic_livingdna()


@pytest.fixture
def fixtures_dir() -> Path:
    """Путь к директории с pre-generated синтетическими fixtures."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def synthetic_23andme_file(fixtures_dir: Path) -> str:
    """Содержимое pre-generated tests/fixtures/synthetic_23andme.txt."""
    return (fixtures_dir / "synthetic_23andme.txt").read_text(encoding="utf-8")


@pytest.fixture
def synthetic_ancestry_file(fixtures_dir: Path) -> str:
    """Содержимое pre-generated tests/fixtures/synthetic_ancestry.txt."""
    return (fixtures_dir / "synthetic_ancestry.txt").read_text(encoding="utf-8")


@pytest.fixture
def synthetic_myheritage_file(fixtures_dir: Path) -> str:
    """Содержимое pre-generated tests/fixtures/synthetic_myheritage.csv."""
    return (fixtures_dir / "synthetic_myheritage.csv").read_text(encoding="utf-8")


@pytest.fixture
def synthetic_ftdna_file(fixtures_dir: Path) -> str:
    """Содержимое pre-generated tests/fixtures/synthetic_ftdna.csv."""
    return (fixtures_dir / "synthetic_ftdna.csv").read_text(encoding="utf-8")


@pytest.fixture
def synthetic_livingdna_file(fixtures_dir: Path) -> str:
    """Содержимое pre-generated tests/fixtures/synthetic_livingdna.txt."""
    return (fixtures_dir / "synthetic_livingdna.txt").read_text(encoding="utf-8")
