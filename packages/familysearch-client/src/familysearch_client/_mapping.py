"""GEDCOM-X JSON → Pydantic-модели.

Маппер выделен в отдельный модуль, чтобы:

1. ``models.py`` остался чистым Pydantic'ом без FamilySearch-специфичной
   нормализации (URI ↔ короткое имя, parts → given/surname).
2. Phase 5.1+ маппер переиспользовался для других ресурсов
   (Relationship, Family, Pedigree).

GEDCOM-X JSON shape — см. документацию FamilySearch:
https://developers.familysearch.org/main/docs/gedcom-x
"""

from __future__ import annotations

from typing import Any

from .models import FsFact, FsGender, FsName, FsPedigreeNode, FsPerson, FsRelationship

_GEDCOMX_PREFIX = "http://gedcomx.org/"


def _strip_gedcomx_prefix(value: str) -> str:
    """Снимает ``http://gedcomx.org/`` префикс с type-URI.

    ``http://gedcomx.org/Male`` → ``Male``. Если префикса нет — возвращает
    как есть; FamilySearch иногда отдаёт уже короткое имя для proprietary
    типов.
    """
    if value.startswith(_GEDCOMX_PREFIX):
        return value[len(_GEDCOMX_PREFIX) :]
    return value


def _gender_from_payload(payload: dict[str, Any] | None) -> FsGender:
    """Конвертирует GEDCOM-X gender object в :class:`FsGender`."""
    if not payload:
        return FsGender.UNKNOWN
    raw = payload.get("type")
    if not isinstance(raw, str):
        return FsGender.UNKNOWN
    short = _strip_gedcomx_prefix(raw).upper()
    if short == "MALE":
        return FsGender.MALE
    if short == "FEMALE":
        return FsGender.FEMALE
    return FsGender.UNKNOWN


def _name_from_payload(payload: dict[str, Any]) -> FsName:
    """Конвертирует GEDCOM-X Name (с nameForms) в :class:`FsName`.

    GEDCOM-X хранит ``nameForms[]`` для разных скриптов/языков. На Phase 5.0
    берём первую форму; multi-script support — Phase 5.1+.
    """
    preferred = bool(payload.get("preferred", False))
    forms = payload.get("nameForms") or []
    if not forms:
        return FsName(preferred=preferred)
    form = forms[0]
    full_text = form.get("fullText") if isinstance(form.get("fullText"), str) else None
    given: str | None = None
    surname: str | None = None
    for part in form.get("parts") or []:
        part_type = _strip_gedcomx_prefix(str(part.get("type", "")))
        value = part.get("value")
        if not isinstance(value, str):
            continue
        if part_type == "Given":
            given = value
        elif part_type == "Surname":
            surname = value
    return FsName(
        full_text=full_text,
        given=given,
        surname=surname,
        preferred=preferred,
    )


def _fact_from_payload(payload: dict[str, Any]) -> FsFact:
    """Конвертирует GEDCOM-X Fact в :class:`FsFact`."""
    raw_type = payload.get("type", "")
    fact_type = _strip_gedcomx_prefix(str(raw_type)) if raw_type else ""
    date = payload.get("date") or {}
    place = payload.get("place") or {}
    return FsFact(
        type=fact_type,
        date_original=date.get("original") if isinstance(date.get("original"), str) else None,
        place_original=place.get("original") if isinstance(place.get("original"), str) else None,
    )


def parse_person(payload: dict[str, Any]) -> FsPerson:
    """Конвертирует один GEDCOM-X Person объект в :class:`FsPerson`.

    Args:
        payload: dict для одной person'ы (НЕ обёртка ``{"persons": [...]}``).
    """
    person_id = payload.get("id")
    if not isinstance(person_id, str) or not person_id:
        msg = "GEDCOM-X person payload missing 'id'"
        raise ValueError(msg)
    names = tuple(_name_from_payload(n) for n in (payload.get("names") or []))
    facts = tuple(_fact_from_payload(f) for f in (payload.get("facts") or []))
    living_raw = payload.get("living")
    living = bool(living_raw) if isinstance(living_raw, bool) else None
    return FsPerson(
        id=person_id,
        gender=_gender_from_payload(payload.get("gender")),
        names=names,
        facts=facts,
        living=living,
    )


def parse_person_response(payload: dict[str, Any]) -> FsPerson:
    """Парсит ответ ``/platform/tree/persons/{id}``.

    FamilySearch возвращает обёртку ``{"persons": [<person>, ...]}``; берём
    первого. Если список пуст — :class:`ValueError` (caller обычно ловит
    через NotFoundError на 404, но защищаемся и от пустого 200).
    """
    persons = payload.get("persons") or []
    if not persons:
        msg = "GEDCOM-X response has empty 'persons' array"
        raise ValueError(msg)
    return parse_person(persons[0])


def _resource_id_from_reference(ref: dict[str, Any] | None) -> str | None:
    """Извлекает id из GEDCOM-X ResourceReference (``{"resource": "#KW7S-VQJ"}``).

    ``None`` если поле отсутствует или не строка. Префикс ``#`` (anchor-style)
    отрезаем, как требует GEDCOM-X spec.
    """
    if not ref:
        return None
    resource = ref.get("resource")
    if not isinstance(resource, str):
        return None
    return resource[1:] if resource.startswith("#") else resource


def _ascendancy_number(payload: dict[str, Any]) -> int | None:
    """Извлекает Ahnentafel ``display.ascendancyNumber`` (как int).

    FamilySearch возвращает его строкой (``"1"``, ``"2"``, …). ``None`` —
    если поле отсутствует или не парсится. Для focus-person'а (root)
    ascendancyNumber = ``1``.
    """
    display = payload.get("display") or {}
    raw = display.get("ascendancyNumber")
    if raw is None:
        return None
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def parse_pedigree_response(payload: dict[str, Any]) -> FsPedigreeNode:
    """Парсит ответ ``/platform/tree/persons/{id}/ancestry?generations=N``.

    FamilySearch отдаёт коллекцию persons с Ahnentafel-нумерацией. Из неё
    собираем рекурсивное дерево :class:`FsPedigreeNode` по правилу:
    предок с номером ``n`` имеет отца с номером ``2n`` и мать с номером
    ``2n+1``. Дерево может быть неполным — отсутствующие предки = ``None``.

    Raises:
        ValueError: если в ответе нет root persona (``ascendancyNumber=1``)
            — тогда непонятно, чьи предки вернулись.
    """
    persons_raw = payload.get("persons") or []
    by_number: dict[int, FsPerson] = {}
    for raw in persons_raw:
        if not isinstance(raw, dict):
            continue
        number = _ascendancy_number(raw)
        if number is None:
            continue
        # Используем уже существующий parser — так получим FsPerson с
        # gender / names / facts полностью нормализованными.
        try:
            person = parse_person(raw)
        except ValueError:
            # Person без id в pedigree — пропускаем (FS иногда возвращает
            # «пробел» для отсутствующих ancestors).
            continue
        by_number[number] = person

    if 1 not in by_number:
        msg = "GEDCOM-X pedigree response has no root person (ascendancyNumber=1)"
        raise ValueError(msg)

    return _build_pedigree_node(1, by_number)


def _build_pedigree_node(number: int, by_number: dict[int, FsPerson]) -> FsPedigreeNode:
    """Рекурсивно строит :class:`FsPedigreeNode` по Ahnentafel-нумерации."""
    person = by_number[number]
    father_n = number * 2
    mother_n = number * 2 + 1
    father = _build_pedigree_node(father_n, by_number) if father_n in by_number else None
    mother = _build_pedigree_node(mother_n, by_number) if mother_n in by_number else None
    return FsPedigreeNode(person=person, father=father, mother=mother)


def parse_relationship(payload: dict[str, Any]) -> FsRelationship:
    """Конвертирует GEDCOM-X Relationship в :class:`FsRelationship`."""
    rel_id = payload.get("id")
    if not isinstance(rel_id, str) or not rel_id:
        msg = "GEDCOM-X relationship payload missing 'id'"
        raise ValueError(msg)
    rel_type = _strip_gedcomx_prefix(str(payload.get("type", "")))
    p1 = _resource_id_from_reference(payload.get("person1"))
    p2 = _resource_id_from_reference(payload.get("person2"))
    if p1 is None or p2 is None:
        msg = "GEDCOM-X relationship missing person1 or person2 reference"
        raise ValueError(msg)
    return FsRelationship(id=rel_id, type=rel_type, person1_id=p1, person2_id=p2)
