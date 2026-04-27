"""Idempotent скачивание HapMap GRCh37 genetic map.

Один раз вызванный — заполняет `packages/dna-analysis/data/genetic_maps/hapmap_grch37/`
файлами `chr1.txt`..`chr22.txt`. Повторные вызовы — no-op (проверяет
sha256 каждого файла, заново скачивает только при mismatch / отсутствии).

В CI **не запускается** — данные ~50 МБ, network egress, не нужно для
unit-тестов (см. ADR-0014: тестовая fixture в `tests/fixtures/genetic_map/`).

Usage:
    uv run python packages/dna-analysis/scripts/download_genetic_map.py

См. ADR-0014 о выборе HapMap GRCh37 как reference (public domain).
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import Final

# Reference URL: https://github.com/joepickrell/1000-genomes-genetic-maps
# По соображениям воспроизводимости держим snapshot с pinned commit
# (или mirror на GCS/MinIO в Phase 6.2). В Phase 6.1 — direct GitHub.
_BASE_URL: Final = (
    "https://raw.githubusercontent.com/joepickrell/1000-genomes-genetic-maps/"
    "master/interpolated_OMNI/"
)

# sha256 каждого файла — фиксируем для tamper-detect. Хеши вычисляются
# при первом успешном downloads run и сюда коммитятся; пустая строка
# означает "ещё не зафиксирован, downloads-only mode".
_SHA256: Final[dict[int, str]] = dict.fromkeys(range(1, 23), "")

_TARGET_DIR: Final = Path(__file__).resolve().parents[1] / "data" / "genetic_maps" / "hapmap_grch37"


def _expected_sha(content: bytes, chrom: int) -> bool:
    expected = _SHA256.get(chrom)
    if not expected:
        return True  # ещё не зафиксирован — пропускаем verify (first run)
    actual = hashlib.sha256(content).hexdigest()
    return actual == expected


def _download_one(chrom: int, *, force: bool = False) -> Path:
    target = _TARGET_DIR / f"chr{chrom}.txt"
    if target.exists() and not force:
        existing = target.read_bytes()
        if _expected_sha(existing, chrom):
            print(f"chr{chrom}: ok (cached)")
            return target
        print(f"chr{chrom}: sha mismatch, re-downloading")

    url = f"{_BASE_URL}chr{chrom}.OMNI.interpolated_genetic_map.gz"
    print(f"chr{chrom}: downloading {url}")
    # Network egress — допустимо в этом standalone скрипте (НЕ в lib-коде).
    with urllib.request.urlopen(url) as resp:
        content = resp.read()

    if not _expected_sha(content, chrom):
        msg = f"sha256 mismatch for chr{chrom}; refusing to overwrite"
        raise RuntimeError(msg)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    print(f"chr{chrom}: wrote {len(content):,} bytes")
    return target


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in args
    for chrom in range(1, 23):
        try:
            _download_one(chrom, force=force)
        except Exception as exc:
            print(f"chr{chrom}: FAILED — {exc}", file=sys.stderr)
            return 1
    print(f"\ndone. data in {_TARGET_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
