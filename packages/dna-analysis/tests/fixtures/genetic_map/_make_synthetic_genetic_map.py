"""Генератор синтетической genetic-map fixture для chr22.

Запускается вручную, не в тестах: см. README.md рядом. Тесты ожидают
точно эти значения, не регенерируйте без обновления assertions.

Privacy: никаких человеческих DNA-данных. Все позиции и cM —
детерминированно сгенерированы из Random(seed=42).
"""

from __future__ import annotations

import random
from pathlib import Path

# Реальный диапазон chr22 в GRCh37 — приблизительно 16 Mb..51 Mb.
_CHR22_START_BP = 16_050_000
_CHR22_END_BP = 51_244_566
_NUM_POINTS = 100
# Средний autosomal rate ~1 cM/Mb с лёгкой джиттер-вариацией.
_BASE_RATE_CM_PER_MB = 1.0


def generate_synthetic_chr22_map() -> str:
    """Возвращает SHAPEIT-style TSV с детерминированными точками."""
    rng = random.Random(42)

    # Равномерно распределённые позиции внутри диапазона.
    span = _CHR22_END_BP - _CHR22_START_BP
    step = span / (_NUM_POINTS - 1)
    positions = [int(_CHR22_START_BP + step * i) for i in range(_NUM_POINTS)]

    # Кумулятивные cM с jitter ±20% вокруг базовой ставки.
    cumulative_cm = 0.0
    cm_values = [0.0]
    for i in range(1, _NUM_POINTS):
        bp_delta = positions[i] - positions[i - 1]
        rate = _BASE_RATE_CM_PER_MB * (0.8 + 0.4 * rng.random())
        cumulative_cm += rate * (bp_delta / 1_000_000)
        cm_values.append(round(cumulative_cm, 6))

    lines = ["position COMBINED_rate(cM/Mb) Genetic_Map(cM)"]
    for i, (pos, cm) in enumerate(zip(positions, cm_values, strict=True)):
        # Локальная rate — производная от соседнего интервала.
        if i == 0:
            rate = _BASE_RATE_CM_PER_MB
        else:
            bp_delta = pos - positions[i - 1]
            rate = (cm - cm_values[i - 1]) / (bp_delta / 1_000_000) if bp_delta else 0.0
        lines.append(f"{pos} {rate:.6f} {cm:.6f}")
    return "\n".join(lines) + "\n"


def main() -> None:
    out_path = Path(__file__).parent / "chr22.txt"
    out_path.write_text(generate_synthetic_chr22_map(), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
