"""JSON loaders → typed records.

Pure parsing — no DB writes, no side-effects. Each loader returns a list
of Pydantic records ready для ingest.upsert_*. Каждый record несёт
``raw_data`` для full-fidelity reservation в ``raw_data`` jsonb-колонке.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Country directory
# ---------------------------------------------------------------------------


class CountryRecord(BaseModel):
    """One country row из autotreegen_country_reference_database*.json."""

    # Mostly ISO 3166-1 alpha-2 (UA, BY, …) but the v1 file includes
    # historical composite codes like "AT-GAL" (Austria-Galicia) — relax
    # to 8 chars to accept those without losing the canonical iso2 case.
    iso2: str = Field(min_length=2, max_length=8)
    country: str = Field(min_length=1, max_length=200)
    v2_batch: str | None = Field(default=None, max_length=64)
    raw_data: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


def load_countries(path: Path, *, v2_batch: str | None = None) -> list[CountryRecord]:
    """Parse country JSON (v1 list-of-dicts shape)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"Expected list at {path}, got {type(raw).__name__}"
        raise ValueError(msg)
    out: list[CountryRecord] = []
    for entry in raw:
        out.append(
            CountryRecord(
                iso2=str(entry["iso2"]).upper(),
                country=str(entry["country"]),
                v2_batch=v2_batch,
                raw_data=entry,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Surname clusters + transliteration table
# ---------------------------------------------------------------------------


class SurnameClusterRecord(BaseModel):
    """One cluster из surname_variant_clusters block."""

    canonical: str = Field(min_length=1, max_length=200)
    community_scope: str = Field(min_length=1, max_length=64)
    rank_within_scope: int | None = None
    variants_latin: list[str] = Field(default_factory=list)
    variants_cyrillic: list[str] | None = None
    variants_hebrew: list[str] | None = None
    variants_yiddish: list[str] | None = None
    raw_data: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class TransliterationRecord(BaseModel):
    """One entry из transliteration_tables block."""

    source_form: str = Field(min_length=1, max_length=200)
    target_forms: dict[str, Any]
    raw_data: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


def load_surnames(
    path: Path,
) -> tuple[list[SurnameClusterRecord], list[TransliterationRecord]]:
    """Parse surname clusters JSON. Returns (clusters, transliteration_entries)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        msg = f"Expected dict at {path}, got {type(doc).__name__}"
        raise ValueError(msg)
    raw_clusters = doc.get("surname_variant_clusters", [])
    raw_translit = doc.get("transliteration_tables", {})

    clusters: list[SurnameClusterRecord] = []
    for entry in raw_clusters:
        clusters.append(
            SurnameClusterRecord(
                canonical=str(entry["canonical"]),
                community_scope=str(entry["community_scope"]),
                rank_within_scope=entry.get("rank_within_scope"),
                variants_latin=list(entry.get("variants_latin") or []),
                variants_cyrillic=entry.get("variants_cyrillic") or None,
                variants_hebrew=entry.get("variants_hebrew") or None,
                variants_yiddish=entry.get("variants_yiddish") or None,
                raw_data=entry,
            )
        )

    translit_entries: list[TransliterationRecord] = []
    if isinstance(raw_translit, dict):
        for source_form, target_forms in raw_translit.items():
            # target_forms могут быть dict или list — оборачиваем в dict
            # для homogenous storage.
            normalized_targets: dict[str, Any]
            if isinstance(target_forms, dict):
                normalized_targets = dict(target_forms)
            else:
                normalized_targets = {"variants": target_forms}
            translit_entries.append(
                TransliterationRecord(
                    source_form=str(source_form),
                    target_forms=normalized_targets,
                    raw_data={"source_form": source_form, "target_forms": target_forms},
                )
            )

    return clusters, translit_entries


# ---------------------------------------------------------------------------
# Fabrication patterns
# ---------------------------------------------------------------------------


class FabricationPatternRecord(BaseModel):
    """One pattern из autotreegen_fabrication_detection_patterns.json."""

    pattern_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    detection_rule: str = Field(min_length=1)
    confidence_when_flagged: float | None = None
    raw_data: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


def load_fabrication_patterns(path: Path) -> list[FabricationPatternRecord]:
    """Parse fabrication-detection JSON (list-of-dicts)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"Expected list at {path}, got {type(raw).__name__}"
        raise ValueError(msg)
    out: list[FabricationPatternRecord] = []
    for entry in raw:
        out.append(
            FabricationPatternRecord(
                pattern_id=str(entry["pattern_id"]),
                category=str(entry["category"]),
                description=str(entry["description"]),
                detection_rule=str(entry["detection_rule"]),
                confidence_when_flagged=entry.get("confidence_when_flagged"),
                raw_data=entry,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Place lookup
# ---------------------------------------------------------------------------


class PlaceLookupRecord(BaseModel):
    """One place из autotreegen_eastern_europe_place_lookup_506_merged.json."""

    old_name: str = Field(min_length=1, max_length=200)
    modern_country: str = Field(min_length=1, max_length=64)
    old_name_local: str | None = Field(default=None, max_length=200)
    modern_name: str = Field(min_length=1, max_length=200)
    lat: float | None = None
    lon: float | None = None
    coordinate_precision: str | None = Field(default=None, max_length=32)
    raw_data: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


def load_places(path: Path) -> list[PlaceLookupRecord]:
    """Parse place lookup JSON. lat_lon stored as list[2] in source — split."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"Expected list at {path}, got {type(raw).__name__}"
        raise ValueError(msg)
    out: list[PlaceLookupRecord] = []
    for entry in raw:
        lat_lon = entry.get("lat_lon") or [None, None]
        lat = float(lat_lon[0]) if len(lat_lon) > 0 and lat_lon[0] is not None else None
        lon = float(lat_lon[1]) if len(lat_lon) > 1 and lat_lon[1] is not None else None
        out.append(
            PlaceLookupRecord(
                old_name=str(entry["old_name"]),
                modern_country=str(entry["modern_country"]),
                old_name_local=entry.get("old_name_local"),
                modern_name=str(entry["modern_name"]),
                lat=lat,
                lon=lon,
                coordinate_precision=entry.get("coordinate_precision"),
                raw_data=entry,
            )
        )
    return out


__all__ = [
    "CountryRecord",
    "FabricationPatternRecord",
    "PlaceLookupRecord",
    "SurnameClusterRecord",
    "TransliterationRecord",
    "load_countries",
    "load_fabrication_patterns",
    "load_places",
    "load_surnames",
]
