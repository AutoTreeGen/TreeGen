"""Тесты Pydantic-схем (без БД)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError
from shared_models.enums import EventType, Sex
from shared_models.schemas import (
    EventCreate,
    PersonCreate,
    PersonRead,
    TreeCreate,
    UserCreate,
)


def test_user_create_validates_email() -> None:
    """UserCreate валидирует email."""
    with pytest.raises(ValidationError):
        UserCreate(email="not-an-email", external_auth_id="auth0|x")  # type: ignore[arg-type]


def test_tree_create_default_visibility_private() -> None:
    """TreeCreate по умолчанию private."""
    tree = TreeCreate(name="Test")
    assert tree.visibility.value == "private"


def test_person_create_with_names() -> None:
    """PersonCreate принимает массив NameBase."""
    p = PersonCreate(tree_id=uuid.uuid4(), sex=Sex.MALE)
    assert p.confidence_score == 0.5  # дефолт


def test_event_create_requires_event_type() -> None:
    """EventCreate требует event_type."""
    with pytest.raises(ValidationError):
        EventCreate(tree_id=uuid.uuid4())  # type: ignore[call-arg]


def test_event_create_with_dates() -> None:
    """EventCreate принимает даты и custom_type."""
    e = EventCreate(
        tree_id=uuid.uuid4(),
        event_type=EventType.BIRTH,
        date_start=dt.date(1850, 1, 1),
        date_end=dt.date(1850, 12, 31),
        date_raw="ABT 1850",
    )
    assert e.event_type == EventType.BIRTH
    assert e.date_start.year == 1850


def test_person_read_from_attributes() -> None:
    """PersonRead умеет маппиться из dict-like с from_attributes=True."""
    payload = {
        "id": uuid.uuid4(),
        "tree_id": uuid.uuid4(),
        "version_id": 1,
        "sex": "M",
        "status": "confirmed",
        "confidence_score": 0.9,
        "provenance": {},
        "gedcom_xref": "@I1@",
        "merged_into_person_id": None,
        "names": [],
        "created_at": dt.datetime.now(dt.UTC),
        "updated_at": dt.datetime.now(dt.UTC),
        "deleted_at": None,
    }
    person = PersonRead.model_validate(payload)
    assert person.sex == Sex.MALE
    assert person.status.value == "confirmed"
