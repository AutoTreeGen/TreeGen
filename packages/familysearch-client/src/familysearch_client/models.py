"""Pydantic-модели для GEDCOM-X (Phase 5.0 — минимальный набор).

Полный GEDCOM-X имеет десятки ресурсов. На Phase 5.0 покрываем только то,
что нужно для ``client.get_person()`` и базовой навигации:

- :class:`FsGender`
- :class:`FsName`
- :class:`FsFact`
- :class:`FsPerson`
- :class:`FsRelationship`

Расширение моделей — incremental, по мере добавления методов клиента
(см. ADR-0011 §«Что отложить»).

GEDCOM-X спецификация: https://developers.familysearch.org/main/docs/gedcom-x
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FsGender(StrEnum):
    """GEDCOM-X gender — нормализованный enum.

    FamilySearch отдаёт type как полный URI (``http://gedcomx.org/Male``);
    конвертация в этот enum делается в маппере при разборе ответа.
    """

    MALE = "MALE"
    FEMALE = "FEMALE"
    UNKNOWN = "UNKNOWN"


class FsName(BaseModel):
    """GEDCOM-X Name — имя персоны.

    GEDCOM-X хранит имя как массив форм (latin / cyrillic / ...). Для
    Phase 5.0 берём первую форму как основную; остальные пока игнорируем.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    full_text: str | None = Field(
        default=None,
        description="Полное имя как одна строка (фолбэк, если нет parts).",
    )
    given: str | None = Field(default=None, description="Имя.")
    surname: str | None = Field(default=None, description="Фамилия.")
    preferred: bool = Field(
        default=False,
        description="GEDCOM-X preferred-флаг — основное имя для UI.",
    )


class FsFact(BaseModel):
    """GEDCOM-X Fact — событие/атрибут (рождение, смерть, профессия).

    Для Phase 5.0 нам нужны минимум birth/death; остальные типы остаются
    как ``type`` без отдельной обработки (расширение в Phase 5.1+).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str = Field(
        description=(
            "GEDCOM-X fact type URI или короткое имя (Birth, Death, ...). "
            "Маппер нормализует префикс http://gedcomx.org/."
        )
    )
    date_original: str | None = Field(
        default=None,
        description="Оригинальная дата как написана в источнике.",
    )
    place_original: str | None = Field(
        default=None,
        description="Оригинальное место как написано в источнике.",
    )


class FsPerson(BaseModel):
    """GEDCOM-X Person — минимальный набор полей для Phase 5.0.

    Поля, которые не описаны здесь, GEDCOM-X отбрасывает (``extra="ignore"``).
    Это сознательно: Phase 5.0 — read-only smoke; модели расширяются по мере
    появления endpoint'ов, которые их используют.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(description="FamilySearch Person ID (например, KW7S-VQJ).")
    gender: FsGender = Field(default=FsGender.UNKNOWN)
    names: tuple[FsName, ...] = Field(default_factory=tuple)
    facts: tuple[FsFact, ...] = Field(default_factory=tuple)
    living: bool | None = Field(
        default=None,
        description="Признак living person; FamilySearch может не возвращать.",
    )

    @property
    def display_name(self) -> str:
        """Возвращает имя для отображения.

        Берёт preferred-форму, иначе первую, иначе ID.
        """
        for name in self.names:
            if name.preferred and name.full_text:
                return name.full_text
        for name in self.names:
            if name.full_text:
                return name.full_text
        return self.id


class FsRelationship(BaseModel):
    """GEDCOM-X Relationship — связь между двумя персонами.

    Phase 5.0: только тип + ссылки на участников. Facts (брак-дата,
    место и т.п.) — Phase 5.1+.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    type: str = Field(
        description=("Тип связи: ParentChild, Couple, ... GEDCOM-X URI или короткое имя.")
    )
    person1_id: str = Field(description="ID первой персоны (ResourceReference).")
    person2_id: str = Field(description="ID второй персоны (ResourceReference).")
