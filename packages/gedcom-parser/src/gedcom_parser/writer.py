"""GEDCOM 5.5.5 writer (ROADMAP §5.1.11 + §5.5).

Сериализует AST-уровень: список корневых :class:`GedcomRecord` → текст
GEDCOM. Симметричен парсеру:

    >>> records = parse_text("...")
    >>> text = write_records(records)
    >>> parse_text(text)  # эквивалентно records (см. round-trip тесты)

Алгоритм один к одному обратный лексеру:

* Каждый узел эмитится строкой ``LEVEL [@XREF@] TAG [VALUE]``.
* Если ``value`` содержит ``"\\n"`` (склеенные на парсе ``CONT``-дети),
  для каждой следующей подстроки эмитится ``LEVEL+1 CONT chunk``. Пустая
  подстрока (``"a\\n\\nb"``) даёт пустой ``CONT`` без значения.
* ``CONC`` обратно не восстанавливается — на парсе он сливался встык
  без разделителя, и оригинальный split не сохранён. Это документировано
  и не нарушает корректности файла (просто длинная строка).
* Дочерние записи рекурсивно эмитятся с уровнем на 1 больше.

Phase 5.5a добавляет :func:`inject_unknown_tags` — helper для round-trip
через DB: при реконструкции записей из ORM их direct-children'ы не
содержат проприетарных тегов (``_FSFTID`` / ``_UID`` / etc.), которые
семантический слой не consumes. Injector добавляет их обратно из
``GedcomDocument.unknown_tags`` чтобы файл-после-export'а структурно
совпадал с файлом-до-импорта.

Для семантического уровня (:class:`GedcomDocument`) полный writer
``write_document`` живёт здесь же; он строит список records через
``inject_unknown_tags`` и делегирует ``write_records``. Reverse-конвертер
``entity → GedcomRecord`` (нужен на 5.5b для DB-driven export) вне
scope этой фазы.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gedcom_parser.models import GedcomRecord, RawTagBlock


def _emit_record(record: GedcomRecord, level: int, out: list[str]) -> None:
    """Записать узел и его потомков в ``out`` (плоский список строк)."""
    parts: list[str] = [str(level)]
    if record.xref_id is not None:
        parts.append(f"@{record.xref_id}@")
    parts.append(record.tag)

    value = record.value
    if not value:
        out.append(" ".join(parts))
    else:
        # Сплит по \n: первая часть → главная строка, остальные → CONT.
        chunks = value.split("\n")
        first = chunks[0]
        if first:
            parts.append(first)
        out.append(" ".join(parts))
        for cont_chunk in chunks[1:]:
            cont_parts: list[str] = [str(level + 1), "CONT"]
            if cont_chunk:
                cont_parts.append(cont_chunk)
            out.append(" ".join(cont_parts))

    for child in record.children:
        _emit_record(child, level + 1, out)


def write_records(records: Iterable[GedcomRecord], *, line_terminator: str = "\n") -> str:
    """Сериализовать корневые записи в GEDCOM-текст.

    Args:
        records: Корневые ``GedcomRecord`` (вывод
            :func:`gedcom_parser.parser.parse_records` или
            :func:`gedcom_parser.parser.parse_text`).
        line_terminator: Разделитель строк. По умолчанию ``"\\n"`` (LF) —
            рекомендация GEDCOM 5.5.5 для UTF-8 файлов. Для Windows-
            совместимости можно передать ``"\\r\\n"``.

    Returns:
        Готовый GEDCOM-текст с финальным ``line_terminator`` после последней
        строки. Кодирование (encode → bytes) вынесено наружу.
    """
    lines: list[str] = []
    for record in records:
        _emit_record(record, level=0, out=lines)
    return line_terminator.join(lines) + line_terminator


def inject_unknown_tags(
    records: list[GedcomRecord],
    unknown_tags: Iterable[RawTagBlock],
) -> list[GedcomRecord]:
    """Re-inject quarantined tags обратно в records на их места.

    Контракт:

    * ``records`` — список верхнеуровневых ``GedcomRecord`` (например,
      реконструированных из ORM через будущий 5.5b-конвертер; либо
      исходные records после parse, у которых unknown_tags уже были
      срезаны caller'ом — для round-trip-тестов).
    * ``unknown_tags`` — блоки из ``GedcomDocument.unknown_tags``.
    * Каждый блок попадает к record'у с совпадающим ``xref_id``
      (``HEAD`` маппится на record с ``tag="HEAD"``). Если owner не
      найден — блок silently пропускается (orphan; на 5.5b validator
      это поднимет как warning, на 5.5a игнорируем).
    * Children'ы append'ятся в конец ``record.children`` в порядке
      ``unknown_tags``. Phase 5.5a поддерживает только ``path=""``
      (прямой потомок); глубже-вложенные блоки игнорируются.

    Возвращает **новый** список records — копии-родителей с
    обновлёнными children. Записи, не получившие никаких новых
    children'ов, возвращаются как есть.

    Args:
        records: Корневые записи (любой порядок).
        unknown_tags: Quarantined блоки.

    Returns:
        Новый список records той же длины с re-injected children'ами.
    """
    # Группируем blocks по (xref_id|"HEAD"). path != "" пока не
    # поддерживается — это явно документированный TODO для 5.5b.
    by_owner: dict[str, list[GedcomRecord]] = {}
    for block in unknown_tags:
        if block.path:
            # Глубже-вложенные блоки на 5.5a не re-injectим. См. модульный docstring.
            continue
        by_owner.setdefault(block.owner_xref_id, []).append(block.record)

    if not by_owner:
        return list(records)

    out: list[GedcomRecord] = []
    for record in records:
        # Owner-key: HEAD без xref'а; остальные — record.xref_id.
        owner_key = "HEAD" if record.tag == "HEAD" else (record.xref_id or "")
        extras = by_owner.get(owner_key)
        if not extras:
            out.append(record)
            continue
        # Создаём копию с расширенным children'ом. Не мутируем оригинал —
        # caller может полагаться на неизменность (тесты, audit).
        out.append(record.model_copy(update={"children": [*record.children, *extras]}))
    return out


def write_document(
    doc: object,
    *,
    line_terminator: str = "\n",
) -> str:
    """Сериализовать ``GedcomDocument`` в GEDCOM-текст.

    Phase 5.5a: реализовано через delegation на ``write_records`` с
    re-injection unknown_tags в записи, которые caller сам собрал. Так
    как entity → record reverse-конвертер ещё не написан (Phase 5.5b),
    этот writer ожидает, что caller передаёт **уже построенные**
    ``records`` рядом с doc'ом — это обходной путь до полной
    реализации.

    Используется в round-trip тестах:

    .. code-block:: python

        records = parse_text(text)
        doc = GedcomDocument.from_records(records)
        # ... mutate (e.g., strip unknown tags to simulate DB roundtrip) ...
        # then re-inject and write:
        rebuilt = inject_unknown_tags(records, doc.unknown_tags)
        text2 = write_records(rebuilt)

    Args:
        doc: ``GedcomDocument`` (typed как ``object`` чтобы избежать
            импорта document.py на module level и не плодить циклов).
        line_terminator: Разделитель строк, как в ``write_records``.

    Returns:
        GEDCOM-текст.

    Raises:
        NotImplementedError: Полный entity→record reverse-конвертер —
            5.5b. Caller должен пользоваться ``inject_unknown_tags`` +
            ``write_records`` напрямую до тех пор.
    """
    msg = (
        "write_document(doc) requires entity → GedcomRecord reverse converter "
        "which lands in Phase 5.5b. For now, caller should hold the original "
        "records and use inject_unknown_tags(records, doc.unknown_tags) + "
        "write_records(...) directly."
    )
    raise NotImplementedError(msg)


__all__ = ["inject_unknown_tags", "write_document", "write_records"]
