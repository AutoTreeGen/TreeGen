"""Тесты GeneticMap loader + interpolation.

Все данные — синтетические, сгенерированные через
`tests/fixtures/genetic_map/_make_synthetic_genetic_map.py`. Никаких
человеческих DNA-данных. См. ADR-0014 о выборе HapMap GRCh37 как
production-источника и о причинах синтетической fixture в CI.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from dna_analysis.genetic_map import GeneticMap, GeneticMapError


@pytest.fixture
def fixture_map_dir(fixtures_dir: Path) -> Path:
    """Каталог с одним chr22.txt fixture-файлом."""
    return fixtures_dir / "genetic_map"


@pytest.fixture
def loaded_map(fixture_map_dir: Path) -> GeneticMap:
    return GeneticMap.from_directory(fixture_map_dir)


def test_loader_reads_chromosome_files(loaded_map: GeneticMap) -> None:
    """from_directory подбирает все chr*.txt из каталога."""
    assert loaded_map.chromosomes == frozenset({22})


def test_loader_skips_non_chromosome_files(tmp_path: Path) -> None:
    """README.md и другие файлы игнорируются, не падают."""
    (tmp_path / "README.md").write_text("docs", encoding="utf-8")
    (tmp_path / "chr5.txt").write_text(
        "position COMBINED_rate(cM/Mb) Genetic_Map(cM)\n100 1.0 0.0\n200 1.0 0.0001\n",
        encoding="utf-8",
    )
    map_obj = GeneticMap.from_directory(tmp_path)
    assert map_obj.chromosomes == frozenset({5})


def test_loader_raises_when_directory_missing(tmp_path: Path) -> None:
    with pytest.raises(GeneticMapError, match="not found"):
        GeneticMap.from_directory(tmp_path / "does_not_exist")


def test_loader_raises_when_directory_empty(tmp_path: Path) -> None:
    with pytest.raises(GeneticMapError, match="no chr"):
        GeneticMap.from_directory(tmp_path)


def test_loader_raises_on_invalid_cm_value(tmp_path: Path) -> None:
    (tmp_path / "chr1.txt").write_text(
        "position COMBINED_rate(cM/Mb) Genetic_Map(cM)\n100 1.0 not_a_number\n",
        encoding="utf-8",
    )
    with pytest.raises(GeneticMapError, match="invalid cM value"):
        GeneticMap.from_directory(tmp_path)


def test_loader_raises_on_non_increasing_positions(tmp_path: Path) -> None:
    (tmp_path / "chr1.txt").write_text(
        "position COMBINED_rate(cM/Mb) Genetic_Map(cM)\n200 1.0 0.0\n100 1.0 0.0001\n",
        encoding="utf-8",
    )
    with pytest.raises(GeneticMapError, match="strictly increasing"):
        GeneticMap.from_directory(tmp_path)


def test_physical_to_genetic_at_first_known_point(loaded_map: GeneticMap) -> None:
    """Точное совпадение с первой точкой → её cM = 0.0."""
    assert loaded_map.physical_to_genetic(22, 16_050_000) == pytest.approx(0.0)


def test_physical_to_genetic_at_last_known_point(loaded_map: GeneticMap) -> None:
    """Точное совпадение с последней точкой → её cumulative cM."""
    # Из chr22.txt: 51244566 → 34.852737
    assert loaded_map.physical_to_genetic(22, 51_244_566) == pytest.approx(34.852737, rel=1e-5)


def test_physical_to_genetic_clamps_left_extrapolation(loaded_map: GeneticMap) -> None:
    """Position раньше первой точки → cM первой точки (clamped)."""
    assert loaded_map.physical_to_genetic(22, 1_000) == pytest.approx(0.0)


def test_physical_to_genetic_clamps_right_extrapolation(loaded_map: GeneticMap) -> None:
    """Position позже последней точки → cM последней точки."""
    assert loaded_map.physical_to_genetic(22, 999_999_999) == pytest.approx(34.852737, rel=1e-5)


def test_physical_to_genetic_interpolates_between_points(loaded_map: GeneticMap) -> None:
    """Точка ровно между двумя соседними → среднее их cM."""
    # Из chr22.txt: 16050000 → 0.0; 16405500 → 0.375326. Mid =16227750 → ~0.187663
    midpoint = (16_050_000 + 16_405_500) // 2
    expected = (0.0 + 0.375326) / 2
    actual = loaded_map.physical_to_genetic(22, midpoint)
    assert actual == pytest.approx(expected, rel=1e-3)


def test_physical_to_genetic_is_monotonically_increasing(loaded_map: GeneticMap) -> None:
    """cM должен расти с position (recombination rate >= 0 везде)."""
    positions = [16_050_000 + i * 350_000 for i in range(100)]
    cms = [loaded_map.physical_to_genetic(22, p) for p in positions]
    assert all(cms[i] <= cms[i + 1] for i in range(len(cms) - 1))


def test_physical_to_genetic_raises_for_unknown_chromosome(loaded_map: GeneticMap) -> None:
    with pytest.raises(GeneticMapError, match="not loaded"):
        loaded_map.physical_to_genetic(1, 100_000)


def test_physical_to_genetic_raises_for_zero_position(loaded_map: GeneticMap) -> None:
    with pytest.raises(GeneticMapError, match="position must be positive"):
        loaded_map.physical_to_genetic(22, 0)


def test_construction_rejects_empty_maps() -> None:
    with pytest.raises(GeneticMapError, match="no chromosomes"):
        GeneticMap({})


def test_construction_rejects_chromosome_with_no_points() -> None:
    with pytest.raises(GeneticMapError, match="empty point list"):
        GeneticMap({22: []})


def test_loader_does_not_log_raw_positions(
    fixture_map_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy guard: loader логирует только агрегаты, не позиции."""
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.genetic_map"):
        GeneticMap.from_directory(fixture_map_dir)

    log_text = "\n".join(record.message for record in caplog.records)
    # Genetic map содержит публичные reference positions, не user data,
    # но мы всё равно держим logs aggregate-only — на случай если кто-то
    # подаст user-specific map в будущем (имputed map, custom population).
    # Большие числа — вероятно позиции (16050000+). Sample-check:
    # ни одна строка лога не содержит 8+-значных чисел.
    assert not re.search(r"\b\d{8,}\b", log_text), f"position-like number leaked: {log_text!r}"
