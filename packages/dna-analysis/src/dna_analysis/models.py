"""Pydantic-модели для DNA-данных.

Все модели — frozen (immutable), чтобы pure-function парсеры не могли
случайно мутировать результат после возврата. Валидация — на уровне
Pydantic (chromosome 1..25, position > 0, genotype в enum).
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    """Источник DNA-теста.

    Значения соответствуют lowercase brand names на момент Phase 6.0.
    """

    TWENTY_THREE_AND_ME = "23andme"
    ANCESTRY = "ancestry"
    MYHERITAGE = "myheritage"
    FTDNA = "ftdna"
    LIVING_DNA = "livingdna"


class Sex(StrEnum):
    """Inferred biological sex от распарсенного DNA-теста.

    Эвристика — наличие валидных Y-SNP genotype calls (см. `infer_sex`).
    Это **не** self-identified gender пользователя — для медицинских/
    юридических контекстов нужен прямой self-report.
    """

    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class ReferenceBuild(StrEnum):
    """Версия reference human genome.

    23andMe v5, AncestryDNA v2, MyHeritage, FTDNA Family Finder — все
    GRCh37 на момент Phase 6.0. GRCh38 поддержан как enum-значение
    на будущее (23andMe v6, перепрогон Ancestry).
    """

    GRCH37 = "GRCh37"
    GRCH38 = "GRCh38"


class Chromosome(IntEnum):
    """Хромосома SNP-маркера.

    1-22 — autosomal, 23 — X, 24 — Y, 25 — митохондриальная.
    Конвенция совпадает с PLINK / 23andMe / Ancestry raw export.
    """

    CHR_1 = 1
    CHR_2 = 2
    CHR_3 = 3
    CHR_4 = 4
    CHR_5 = 5
    CHR_6 = 6
    CHR_7 = 7
    CHR_8 = 8
    CHR_9 = 9
    CHR_10 = 10
    CHR_11 = 11
    CHR_12 = 12
    CHR_13 = 13
    CHR_14 = 14
    CHR_15 = 15
    CHR_16 = 16
    CHR_17 = 17
    CHR_18 = 18
    CHR_19 = 19
    CHR_20 = 20
    CHR_21 = 21
    CHR_22 = 22
    X = 23
    Y = 24
    MT = 25


class Genotype(StrEnum):
    """Двухаллельный genotype call.

    NN — no-call (включает 23andMe '--', Ancestry '0 0', missing data).
    Гомозиготные и гетерозиготные пары представлены lexicographically
    sorted (AC, не CA), парсеры обязаны нормализовать.
    """

    AA = "AA"
    AC = "AC"
    AG = "AG"
    AT = "AT"
    CC = "CC"
    CG = "CG"
    CT = "CT"
    GG = "GG"
    GT = "GT"
    TT = "TT"
    # Hemizygous (Y / mitochondrial / male X) — single allele.
    A = "A"
    C = "C"
    G = "G"
    T = "T"
    # Insertion / deletion calls (Ancestry/MyHeritage иногда экспортируют).
    II = "II"
    DD = "DD"
    ID = "ID"
    # No-call.
    NN = "NN"


class Snp(BaseModel):
    """Одиночный SNP-вызов из raw DNA-файла."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rsid: str = Field(..., min_length=1, max_length=64)
    chromosome: Chromosome
    position: int = Field(..., gt=0)
    genotype: Genotype


class DnaTest(BaseModel):
    """Распарсенный DNA-тест от одного провайдера.

    Содержит метаданные (provider, version, reference_build) и список
    SNP. Не содержит идентификации пользователя — это уже level
    services/dna-service/ (Phase 6.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Provider
    version: str = Field(..., min_length=1)
    reference_build: ReferenceBuild
    snps: list[Snp] = Field(..., min_length=1)
