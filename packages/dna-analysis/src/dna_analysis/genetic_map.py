"""Загрузчик HapMap GRCh37 genetic map (recombination rates).

Используется matching pipeline для конвертации physical bp → genetic cM
(см. ADR-0014). Карта живёт в памяти процесса; на каждой autosomal
хромосоме держим отсортированный список (position_bp, cumulative_cM).

Формат входного файла (per chromosome) — SHAPEIT/HapMap style TSV:

    position COMBINED_rate(cM/Mb) Genetic_Map(cM)
    72434    8.131                0
    78032    8.064                0.045
    ...

Header (первая строка с буквами) — опционален, но если есть, начинается
с нечисленного префикса. Всё остальное игнорируется до первой строки
с тремя числовыми колонками.

Privacy: работает с public domain reference data, никаких user-specific
DNA здесь нет (cм. ADR-0014).
"""

from __future__ import annotations

import bisect
import logging
import re
from pathlib import Path
from typing import Final

_LOG: Final = logging.getLogger(__name__)

# 1..22 — autosomal. X/Y/MT не входят в Phase 6.1 (см. ADR-0014).
_AUTOSOMAL_CHROMOSOMES: Final = tuple(range(1, 23))

# Ожидаемый паттерн имени файла: chr1.txt, chr10.txt, ...
# Альтернативные расширения (.gz, .map) не поддерживаем — decompress
# должен происходить в download-скрипте.
_CHR_FILE_PATTERN: Final = re.compile(r"^chr(\d+)\.txt$")


class GeneticMapError(Exception):
    """Ошибка загрузки или использования genetic map."""


class GeneticMap:
    """In-memory recombination rate map, GRCh37.

    Хранит per-chromosome отсортированный список (position_bp,
    cumulative_cM). Lookup делает binary search и линейную интерполяцию
    между двумя соседними точками.

    Attributes:
        chromosomes: Множество хромосом, для которых загружены данные.
    """

    def __init__(self, maps: dict[int, list[tuple[int, float]]]) -> None:
        if not maps:
            msg = "genetic map has no chromosomes loaded"
            raise GeneticMapError(msg)
        for chrom, points in maps.items():
            if not points:
                msg = f"chromosome {chrom} has empty point list"
                raise GeneticMapError(msg)
            if any(points[i][0] >= points[i + 1][0] for i in range(len(points) - 1)):
                msg = f"chromosome {chrom} positions are not strictly increasing"
                raise GeneticMapError(msg)
        self._maps: Final = maps
        # Кэш отдельных списков положений для bisect (избегаем list comp на каждый call).
        self._positions_cache: Final[dict[int, list[int]]] = {
            chrom: [pos for pos, _ in points] for chrom, points in maps.items()
        }

    @property
    def chromosomes(self) -> frozenset[int]:
        """Хромосомы, доступные в этой карте."""
        return frozenset(self._maps.keys())

    @classmethod
    def from_directory(cls, source_dir: Path) -> GeneticMap:
        """Загружает карту из каталога с файлами chr1.txt..chr22.txt.

        Файлы для отсутствующих хромосом просто пропускаются (полезно для
        test-фикстур из одной хромосомы). Если ни одной chr*.txt не
        найдено — поднимаем GeneticMapError.

        Args:
            source_dir: Каталог с файлами chr1.txt..chr22.txt в SHAPEIT/HapMap
                формате.

        Raises:
            GeneticMapError: Если каталог пустой или формат невалидный.
        """
        if not source_dir.is_dir():
            msg = f"genetic map directory not found: {source_dir}"
            raise GeneticMapError(msg)

        maps: dict[int, list[tuple[int, float]]] = {}
        for path in sorted(source_dir.iterdir()):
            match = _CHR_FILE_PATTERN.match(path.name)
            if match is None:
                continue
            chrom = int(match.group(1))
            if chrom not in _AUTOSOMAL_CHROMOSOMES:
                continue
            maps[chrom] = _load_chromosome_file(path)

        if not maps:
            msg = f"no chr*.txt files found in {source_dir}"
            raise GeneticMapError(msg)

        _LOG.debug(
            "loaded genetic map: %d chromosomes, %d total points",
            len(maps),
            sum(len(points) for points in maps.values()),
        )
        return cls(maps)

    def physical_to_genetic(self, chromosome: int, position: int) -> float:
        """Конвертирует физическую позицию (bp) в генетическую (cM).

        Алгоритм:
          - position раньше первой точки карты → возвращаем cM первой
            точки (clamped extrapolation; recombination rate уходит в 0
            на концах хромосом, см. ADR-0014).
          - position позже последней точки → возвращаем cM последней
            точки (то же clamping).
          - position между двумя соседними точками → линейная
            интерполяция cM.
          - position точно совпадает с одной из точек → её cM.

        Args:
            chromosome: 1..22 (autosomal). X/Y/MT — Phase 6.4.
            position: Physical position в bp, > 0.

        Raises:
            GeneticMapError: Если хромосома не загружена в карте.
        """
        if chromosome not in self._maps:
            msg = f"chromosome {chromosome} not loaded in genetic map"
            raise GeneticMapError(msg)
        if position <= 0:
            msg = "position must be positive"
            raise GeneticMapError(msg)

        points = self._maps[chromosome]
        positions = self._positions_cache[chromosome]

        # Clamp left.
        if position <= points[0][0]:
            return points[0][1]
        # Clamp right.
        if position >= points[-1][0]:
            return points[-1][1]

        # Точное совпадение или интерполяция между points[idx-1] и points[idx].
        idx = bisect.bisect_left(positions, position)
        if positions[idx] == position:
            return points[idx][1]

        prev_pos, prev_cm = points[idx - 1]
        next_pos, next_cm = points[idx]
        # Линейная интерполяция.
        ratio = (position - prev_pos) / (next_pos - prev_pos)
        return prev_cm + (next_cm - prev_cm) * ratio


def _load_chromosome_file(path: Path) -> list[tuple[int, float]]:
    """Парсит файл chrN.txt в список (position, cumulative_cM).

    Поддерживает SHAPEIT/HapMap-формат:
        position COMBINED_rate(cM/Mb) Genetic_Map(cM)
        72434    8.131                0
        ...

    Header-строка (начинается с нечислового символа) пропускается.
    Разделители — whitespace (любой). Пустые строки игнорируются.
    """
    points: list[tuple[int, float]] = []
    with path.open(encoding="utf-8") as fh:
        for line_idx, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            # Header: первое поле не парсится как int.
            try:
                position = int(parts[0])
            except ValueError:
                if line_idx == 1:
                    continue
                msg = f"invalid position at {path.name}:{line_idx}"
                raise GeneticMapError(msg) from None
            try:
                cumulative_cm = float(parts[2])
            except ValueError as exc:
                msg = f"invalid cM value at {path.name}:{line_idx}"
                raise GeneticMapError(msg) from exc
            points.append((position, cumulative_cm))

    if not points:
        msg = f"no data rows in {path.name}"
        raise GeneticMapError(msg)
    return points
