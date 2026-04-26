"""DTO для основных сущностей: Person, Name, Family, Event, Place, Source."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import Field

from shared_models.enums import (
    DateCalendar,
    DateQualifier,
    EventType,
    NameType,
    Sex,
    SourceType,
)
from shared_models.schemas.common import SchemaBase, SoftTimestamps, StatusFields

# ---- Name ----------------------------------------------------------------


class NameBase(SchemaBase):
    """Общие поля имени."""

    given_name: str | None = None
    surname: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    nickname: str | None = None
    patronymic: str | None = None
    maiden_surname: str | None = None
    name_type: NameType = NameType.BIRTH
    script: str | None = None
    romanized: str | None = None
    sort_order: int = 0


class NameCreate(NameBase):
    """Создание имени (привязка к persona — через path или person_id)."""

    person_id: uuid.UUID


class NameUpdate(SchemaBase):
    """PATCH для имени."""

    given_name: str | None = None
    surname: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    nickname: str | None = None
    patronymic: str | None = None
    maiden_surname: str | None = None
    name_type: NameType | None = None
    script: str | None = None
    romanized: str | None = None
    sort_order: int | None = None


class NameRead(NameBase, SoftTimestamps):
    """Read-схема имени."""

    id: uuid.UUID
    person_id: uuid.UUID


# ---- Person --------------------------------------------------------------


class PersonBase(StatusFields):
    """Общие поля персоны."""

    sex: Sex = Sex.UNKNOWN
    gedcom_xref: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class PersonCreate(PersonBase):
    """Создание персоны (tree_id берётся из path или контекста)."""

    tree_id: uuid.UUID
    names: list[NameBase] = Field(default_factory=list)


class PersonUpdate(SchemaBase):
    """PATCH для персоны."""

    sex: Sex | None = None
    status: str | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    gedcom_xref: str | None = None
    provenance: dict[str, Any] | None = None


class PersonRead(PersonBase, SoftTimestamps):
    """Read-схема персоны."""

    id: uuid.UUID
    tree_id: uuid.UUID
    version_id: int
    merged_into_person_id: uuid.UUID | None = None
    names: list[NameRead] = Field(default_factory=list)


# ---- Family --------------------------------------------------------------


class FamilyBase(StatusFields):
    """Общие поля семьи."""

    husband_id: uuid.UUID | None = None
    wife_id: uuid.UUID | None = None
    gedcom_xref: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class FamilyCreate(FamilyBase):
    """Создание семьи."""

    tree_id: uuid.UUID


class FamilyUpdate(SchemaBase):
    """PATCH для семьи."""

    husband_id: uuid.UUID | None = None
    wife_id: uuid.UUID | None = None
    status: str | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)


class FamilyRead(FamilyBase, SoftTimestamps):
    """Read-схема семьи."""

    id: uuid.UUID
    tree_id: uuid.UUID
    version_id: int


# ---- Event ---------------------------------------------------------------


class EventBase(StatusFields):
    """Общие поля события."""

    event_type: EventType
    custom_type: str | None = None
    place_id: uuid.UUID | None = None
    date_raw: str | None = None
    date_start: dt.date | None = None
    date_end: dt.date | None = None
    date_qualifier: DateQualifier | None = None
    date_calendar: DateCalendar | None = None
    description: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class EventCreate(EventBase):
    """Создание события."""

    tree_id: uuid.UUID


class EventUpdate(SchemaBase):
    """PATCH для события."""

    event_type: EventType | None = None
    custom_type: str | None = None
    place_id: uuid.UUID | None = None
    date_raw: str | None = None
    date_start: dt.date | None = None
    date_end: dt.date | None = None
    date_qualifier: DateQualifier | None = None
    date_calendar: DateCalendar | None = None
    description: str | None = None


class EventRead(EventBase, SoftTimestamps):
    """Read-схема события."""

    id: uuid.UUID
    tree_id: uuid.UUID
    version_id: int


# ---- Place ---------------------------------------------------------------


class PlaceAliasBase(SchemaBase):
    """Общие поля алиаса места."""

    name: str
    language: str | None = None
    script: str | None = None
    romanized: str | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    note: str | None = None


class PlaceAliasCreate(PlaceAliasBase):
    """Создание алиаса места."""

    place_id: uuid.UUID


class PlaceAliasRead(PlaceAliasBase, SoftTimestamps):
    """Read-схема алиаса места."""

    id: uuid.UUID
    place_id: uuid.UUID


class PlaceBase(StatusFields):
    """Общие поля места."""

    canonical_name: str
    country_code_iso: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    settlement: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    historical_period_start: dt.date | None = None
    historical_period_end: dt.date | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class PlaceCreate(PlaceBase):
    """Создание места."""

    tree_id: uuid.UUID
    aliases: list[PlaceAliasBase] = Field(default_factory=list)


class PlaceUpdate(SchemaBase):
    """PATCH для места."""

    canonical_name: str | None = None
    country_code_iso: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    settlement: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class PlaceRead(PlaceBase, SoftTimestamps):
    """Read-схема места."""

    id: uuid.UUID
    tree_id: uuid.UUID
    version_id: int
    aliases: list[PlaceAliasRead] = Field(default_factory=list)


# ---- Source --------------------------------------------------------------


class SourceBase(StatusFields):
    """Общие поля источника."""

    title: str
    author: str | None = None
    publication: str | None = None
    source_type: SourceType = SourceType.OTHER
    repository: str | None = None
    repository_id: str | None = None
    url: str | None = None
    publication_date: dt.date | None = None


class SourceCreate(SourceBase):
    """Создание источника."""

    tree_id: uuid.UUID


class SourceUpdate(SchemaBase):
    """PATCH для источника."""

    title: str | None = None
    author: str | None = None
    publication: str | None = None
    source_type: SourceType | None = None
    repository: str | None = None
    repository_id: str | None = None
    url: str | None = None
    publication_date: dt.date | None = None


class SourceRead(SourceBase, SoftTimestamps):
    """Read-схема источника."""

    id: uuid.UUID
    tree_id: uuid.UUID
    version_id: int
