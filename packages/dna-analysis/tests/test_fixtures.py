"""Тесты на детерминизм синтетических fixture-файлов.

Если генератор перестал быть детерминированным — pre-generated файлы
в tests/fixtures/ разойдутся со свежей генерацией, и регрессионные
тесты парсеров (Tasks 3 и 4) перестанут быть стабильными.

Также явно проверяем privacy-инвариант: в fixture'ах НЕТ реальных
dbSNP rsids (используется паттерн rs1..rsN, не реальные числовые id
из dbSNP, которые в реальности часто 7-9 знаков).
"""

from __future__ import annotations

from pathlib import Path

from ._generators import (
    generate_synthetic_23andme,
    generate_synthetic_ancestry,
    generate_synthetic_ftdna,
    generate_synthetic_livingdna,
    generate_synthetic_myheritage,
)


def test_23andme_generator_is_deterministic(fixtures_dir: Path) -> None:
    """Свежая генерация совпадает с pre-generated файлом."""
    fresh = generate_synthetic_23andme(100)
    committed = (fixtures_dir / "synthetic_23andme.txt").read_text(encoding="utf-8")
    assert fresh == committed, (
        "Synthetic 23andMe generator drifted; regenerate fixture via "
        "`tests/_generators.generate_synthetic_23andme`."
    )


def test_ancestry_generator_is_deterministic(fixtures_dir: Path) -> None:
    """Свежая генерация совпадает с pre-generated файлом."""
    fresh = generate_synthetic_ancestry(100)
    committed = (fixtures_dir / "synthetic_ancestry.txt").read_text(encoding="utf-8")
    assert fresh == committed


def test_23andme_fixture_uses_synthetic_rsids(synthetic_23andme_file: str) -> None:
    """Privacy guard: fixture использует rs1..rsN, не реальные dbSNP id."""
    data_lines = [line for line in synthetic_23andme_file.splitlines() if not line.startswith("#")]
    assert data_lines, "fixture must contain SNP lines"
    for line in data_lines:
        rsid = line.split("\t", 1)[0]
        # Реальные dbSNP rsid — 4-9 знаков (rs6, rs1234567); синтетические
        # rs1..rs100 укладываются в 1-3 знаков после `rs`. Защищаемся именно
        # от случайной подмены реальным dump'ом.
        assert rsid.startswith("rs"), f"rsid must start with 'rs': {rsid!r}"
        suffix = rsid[2:]
        assert suffix.isdigit(), f"rsid suffix must be numeric: {rsid!r}"
        assert 1 <= int(suffix) <= 100, f"synthetic fixture must use rs1..rs100, got {rsid!r}"


def test_ancestry_fixture_uses_synthetic_rsids(synthetic_ancestry_file: str) -> None:
    """Privacy guard: fixture использует rs1..rsN, не реальные dbSNP id."""
    data_lines = [line for line in synthetic_ancestry_file.splitlines() if not line.startswith("#")]
    assert data_lines, "fixture must contain SNP lines"
    for line in data_lines:
        rsid = line.split("\t", 1)[0]
        assert rsid.startswith("rs")
        suffix = rsid[2:]
        assert suffix.isdigit()
        assert 1 <= int(suffix) <= 100


def test_myheritage_generator_is_deterministic(fixtures_dir: Path) -> None:
    """Свежая генерация совпадает с pre-generated файлом."""
    fresh = generate_synthetic_myheritage(100)
    committed = (fixtures_dir / "synthetic_myheritage.csv").read_text(encoding="utf-8")
    assert fresh == committed, (
        "Synthetic MyHeritage generator drifted; regenerate fixture via "
        "`tests/_generators.generate_synthetic_myheritage`."
    )


def test_ftdna_generator_is_deterministic(fixtures_dir: Path) -> None:
    """Свежая генерация совпадает с pre-generated файлом."""
    fresh = generate_synthetic_ftdna(100)
    committed = (fixtures_dir / "synthetic_ftdna.csv").read_text(encoding="utf-8")
    assert fresh == committed, (
        "Synthetic FTDNA generator drifted; regenerate fixture via "
        "`tests/_generators.generate_synthetic_ftdna`."
    )


def test_myheritage_fixture_uses_synthetic_rsids(synthetic_myheritage_file: str) -> None:
    """Privacy guard: fixture использует rs1..rsN, не реальные dbSNP id."""
    data_lines = [
        line
        for line in synthetic_myheritage_file.splitlines()
        if line and not line.startswith("#") and not line.startswith("RSID")
    ]
    assert data_lines, "fixture must contain SNP lines"
    for line in data_lines:
        # MyHeritage CSV — quoted: "rs1","21","30885207","--"
        rsid = line.split(",", 1)[0].strip('"')
        assert rsid.startswith("rs"), f"rsid must start with 'rs': {rsid!r}"
        suffix = rsid[2:]
        assert suffix.isdigit(), f"rsid suffix must be numeric: {rsid!r}"
        assert 1 <= int(suffix) <= 100, f"synthetic fixture must use rs1..rs100, got {rsid!r}"


def test_ftdna_fixture_uses_synthetic_rsids(synthetic_ftdna_file: str) -> None:
    """Privacy guard: fixture использует rs1..rsN, не реальные dbSNP id."""
    data_lines = [
        line for line in synthetic_ftdna_file.splitlines() if line and not line.startswith("RSID")
    ]
    assert data_lines, "fixture must contain SNP lines"
    for line in data_lines:
        rsid = line.split(",", 1)[0].strip('"')
        assert rsid.startswith("rs")
        suffix = rsid[2:]
        assert suffix.isdigit()
        assert 1 <= int(suffix) <= 100


def test_livingdna_generator_is_deterministic(fixtures_dir: Path) -> None:
    """Свежая генерация совпадает с pre-generated файлом."""
    fresh = generate_synthetic_livingdna(100)
    committed = (fixtures_dir / "synthetic_livingdna.txt").read_text(encoding="utf-8")
    assert fresh == committed, (
        "Synthetic LivingDNA generator drifted; regenerate fixture via "
        "`tests/_generators.generate_synthetic_livingdna`."
    )


def test_livingdna_fixture_uses_synthetic_rsids(synthetic_livingdna_file: str) -> None:
    """Privacy guard: fixture использует rs1..rsN, не реальные dbSNP id."""
    data_lines = [
        line for line in synthetic_livingdna_file.splitlines() if not line.startswith("#")
    ]
    assert data_lines, "fixture must contain SNP lines"
    for line in data_lines:
        rsid = line.split("\t", 1)[0]
        assert rsid.startswith("rs"), f"rsid must start with 'rs': {rsid!r}"
        suffix = rsid[2:]
        assert suffix.isdigit(), f"rsid suffix must be numeric: {rsid!r}"
        assert 1 <= int(suffix) <= 100, f"synthetic fixture must use rs1..rs100, got {rsid!r}"
