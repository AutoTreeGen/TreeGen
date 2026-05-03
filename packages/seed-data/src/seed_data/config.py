"""Path resolution для canonical (committed) и local (gitignored) seeds.

Canonical = ship'ятся в wheel'е, всегда доступны (CI и dev).
Local = большие JSON-файлы на dev-машине owner'а; путь через
``--data-dir`` flag или ``SEED_DATA_DIR`` env var. Mirror
``GEDCOM_TEST_CORPUS`` env-var pattern (memory:
``test_corpus_gedcom_files.md``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Path к committed seed files. ``__file__`` =
# ``…/packages/seed-data/src/seed_data/config.py``; data/ родственный src/.
# parents[1] = src/seed_data → parents[2] = src → parents[3] = packages/seed-data.
CANONICAL_DATA_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "data" / "canonical"


# Environment variable consumers могут переопределить.
_SEED_DATA_DIR_ENV: Final[str] = "SEED_DATA_DIR"


def resolve_data_dir(explicit: str | Path | None = None) -> Path | None:
    """Return resolved local-only data dir, or None if unconfigured.

    Priority: explicit arg → ``SEED_DATA_DIR`` env var → None.
    None == only canonical seeds available; CLI / loaders skip
    local-only paths gracefully.
    """
    if explicit is not None:
        return Path(explicit)
    raw = os.environ.get(_SEED_DATA_DIR_ENV)
    if raw:
        return Path(raw)
    return None


# ---------------------------------------------------------------------------
# Source-file dataclasses — fixed filenames per data type, used for both
# canonical and local-only paths.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CountrySource:
    """Country reference source files.

    ``v1_filename`` лежит в canonical (~164 KB).
    ``v2_batch_glob`` matches all v2-batch files в local-only data dir.
    ``ussr_extension_filename`` — отдельный former-USSR extension file.
    """

    v1_filename: str = "autotreegen_country_reference_database.json"
    v2_batch_glob: str = "autotreegen_country_reference_database_v2_batch*.json"
    ussr_extension_filename: str = (
        "autotreegen_country_reference_database_former_ussr_extension.json"
    )


@dataclass(frozen=True, slots=True)
class FabricationSource:
    """Fabrication-detection patterns (canonical, ~55 KB)."""

    filename: str = "autotreegen_fabrication_detection_patterns.json"


@dataclass(frozen=True, slots=True)
class SurnameSource:
    """Surname variant clusters + transliteration (local-only, ~400 KB)."""

    filename: str = "autotreegen_surname_variant_clusters_merged.json"


@dataclass(frozen=True, slots=True)
class PlaceSource:
    """Eastern-European place lookup (local-only, ~800 KB)."""

    filename: str = "autotreegen_eastern_europe_place_lookup_506_merged.json"


def canonical_paths() -> dict[str, Path]:
    """Path к каждому committed canonical файл'у. Always available."""
    return {
        "country_v1": CANONICAL_DATA_DIR / CountrySource().v1_filename,
        "fabrication": CANONICAL_DATA_DIR / FabricationSource().filename,
    }


@dataclass(frozen=True, slots=True)
class LocalSeedPaths:
    """Resolved local-only paths under ``data_dir``.

    ``country_v2_batches`` — sorted list (variable count, glob match).
    Other fields — single :class:`Path` (caller checks ``.exists()``;
    missing = file not present on this machine, skip silently).
    """

    country_v2_batches: list[Path]
    country_ussr_extension: Path
    surname_clusters: Path
    place_lookup: Path


def local_paths(data_dir: Path) -> LocalSeedPaths:
    """Resolve local-only file paths under ``data_dir``.

    Returns typed :class:`LocalSeedPaths` so callers don't deal with
    ``Path | list[Path]`` union narrowing.
    """
    country = CountrySource()
    return LocalSeedPaths(
        country_v2_batches=sorted(data_dir.glob(country.v2_batch_glob)),
        country_ussr_extension=data_dir / country.ussr_extension_filename,
        surname_clusters=data_dir / SurnameSource().filename,
        place_lookup=data_dir / PlaceSource().filename,
    )
