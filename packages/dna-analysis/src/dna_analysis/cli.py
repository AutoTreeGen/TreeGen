"""CLI `dna-analysis` (Phase 6.1 Task 5).

Entry point — `[project.scripts] dna-analysis = "dna_analysis.cli:cli"`.

Команды:
    dna-analysis match FILE_A FILE_B [--genetic-map DIR] [--min-cm N]
                                     [--min-snps N]

`match` собирает end-to-end pipeline: parse → find_shared_segments →
predict_relationship и выдаёт JSON в stdout. Никакой записи в файлы
по дефолту — пользователь сам решает (`> match.json`).

Privacy (см. ADR-0012 + ADR-0014):
    - JSON output содержит chromosome / start_bp / end_bp / num_snps /
      cm_length для сегментов; никаких rsid / genotypes / positions
      внутри сегментов.
    - В stderr — только агрегаты для пользователя (счётчики SNP,
      количество сегментов). Никаких raw values.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import click

from dna_analysis.errors import UnsupportedFormatError
from dna_analysis.genetic_map import GeneticMap
from dna_analysis.matching import (
    SharedSegment,
    find_shared_segments,
    predict_relationship,
)
from dna_analysis.matching.segments import DEFAULT_MIN_CM, DEFAULT_MIN_SNPS
from dna_analysis.models import DnaTest
from dna_analysis.parsers import (
    AncestryParser,
    BaseDnaParser,
    TwentyThreeAndMeParser,
)

_LOG: Final = logging.getLogger(__name__)

# Все парсеры с реальной реализацией в Phase 6.0/6.1. MyHeritage и
# FTDNA — заглушки, попадут сюда после Phase 6.x.
_AVAILABLE_PARSERS: Final[tuple[type[BaseDnaParser], ...]] = (
    TwentyThreeAndMeParser,
    AncestryParser,
)


@click.group()
def cli() -> None:
    """AutoTreeGen DNA analysis CLI."""


@cli.command()
@click.argument(
    "file_a",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "file_b",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--genetic-map",
    "genetic_map_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory with chr*.txt files (HapMap GRCh37 format).",
)
@click.option(
    "--min-cm",
    type=float,
    default=DEFAULT_MIN_CM,
    show_default=True,
    help="Minimum segment length in centiMorgans.",
)
@click.option(
    "--min-snps",
    type=int,
    default=DEFAULT_MIN_SNPS,
    show_default=True,
    help="Minimum number of SNPs per segment.",
)
def match(
    file_a: Path,
    file_b: Path,
    genetic_map_dir: Path,
    min_cm: float,
    min_snps: int,
) -> None:
    """Compare two raw DNA files; output JSON match report to stdout."""
    test_a = _parse_file(file_a)
    test_b = _parse_file(file_b)
    genetic_map = GeneticMap.from_directory(genetic_map_dir)

    segments = find_shared_segments(test_a, test_b, genetic_map, min_cm=min_cm, min_snps=min_snps)
    total_cm = sum(seg.cm_length for seg in segments)
    longest_cm = max((seg.cm_length for seg in segments), default=0.0)
    relationships = predict_relationship(total_cm, longest_segment_cm=longest_cm)

    report = {
        "test_a": _summarize_test(test_a),
        "test_b": _summarize_test(test_b),
        "shared_segments": [_segment_to_dict(seg) for seg in segments],
        "total_shared_cm": round(total_cm, 2),
        "longest_segment_cm": round(longest_cm, 2),
        "relationship_predictions": [
            {
                "label": r.label,
                "probability": round(r.probability, 4),
                "cm_range": list(r.cm_range),
                "source": r.source,
            }
            for r in relationships
        ],
        "warnings": _collect_warnings(test_a, test_b, total_cm, segments),
    }

    click.echo(json.dumps(report, indent=2, sort_keys=False))


def _parse_file(path: Path) -> DnaTest:
    """Читает файл, выбирает первый подходящий парсер, возвращает DnaTest."""
    content = path.read_text(encoding="utf-8")
    for parser_cls in _AVAILABLE_PARSERS:
        if parser_cls.detect(content):
            return parser_cls().parse(content)
    msg = f"no parser recognised the format of {path.name}"
    raise UnsupportedFormatError(msg)


def _summarize_test(test: DnaTest) -> dict[str, object]:
    """Aggregate-only summary (provider, version, build, count) — без SNP-данных."""
    return {
        "provider": test.provider.value,
        "version": test.version,
        "reference_build": test.reference_build.value,
        "snp_count": len(test.snps),
    }


def _segment_to_dict(seg: SharedSegment) -> dict[str, object]:
    return {
        "chromosome": seg.chromosome,
        "start_bp": seg.start_bp,
        "end_bp": seg.end_bp,
        "num_snps": seg.num_snps,
        "cm_length": round(seg.cm_length, 3),
    }


def _collect_warnings(
    test_a: DnaTest,
    test_b: DnaTest,
    total_cm: float,
    segments: list[SharedSegment],
) -> list[str]:
    """Возвращает список user-facing предупреждений для JSON output."""
    warnings: list[str] = []

    if test_a.provider != test_b.provider:
        warnings.append(
            f"Cross-platform comparison ({test_a.provider.value} vs {test_b.provider.value}): "
            "different chips overlap by ~50-70% rsids; distant relatives "
            "may be missed (Phase 6.5 imputation)."
        )

    if test_a.reference_build != test_b.reference_build:
        warnings.append(
            f"Reference build mismatch: {test_a.reference_build.value} vs "
            f"{test_b.reference_build.value}. Positions may not align."
        )

    # Эндогамия: total cM > 200 при множестве коротких сегментов
    # (см. ADR-0014 §«Risks»).
    short_segments = sum(1 for s in segments if s.cm_length < 15)
    if total_cm > 200 and short_segments >= 5:
        warnings.append(
            "High total cM with many short segments — possible endogamy "
            "(Ashkenazi, Roma, Amish). Total cM may overestimate closeness "
            "by ~1.5-2x; Phase 6.2+ adds an endogamy adjustment factor."
        )

    return warnings
