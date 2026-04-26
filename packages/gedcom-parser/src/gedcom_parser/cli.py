"""CLI ``gedcom-tool``: команды ``stats``, ``parse``, ``validate``, ``diff``.

Использование (после ``uv sync``)::

    gedcom-tool stats path/to/tree.ged
    gedcom-tool parse path/to/tree.ged --compact -o tree.json
    gedcom-tool validate path/to/tree.ged
    gedcom-tool diff path/to/old.ged path/to/new.ged

Команда ``stats`` выводит человеко-читаемую сводку (кодировка, число записей,
разбивка по тегам, охват дат, разнообразие мест, топ фамилий). ``parse``
сериализует AST в JSON. ``validate`` проверяет xref-связи и показывает
битые ссылки. ``diff`` сравнивает два файла на уровне xref персон и семей.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import typer

from gedcom_parser.document import GedcomDocument
from gedcom_parser.exceptions import GedcomError
from gedcom_parser.models import EncodingInfo, GedcomRecord
from gedcom_parser.parser import parse_document_file, parse_file

app = typer.Typer(
    name="gedcom-tool",
    help="Утилиты для работы с GEDCOM-файлами.",
    add_completion=False,
    no_args_is_help=True,
)


def _load(path: Path) -> tuple[list[GedcomRecord], EncodingInfo]:
    """Прочитать и распарсить файл; на ошибке — печать и Exit(1)."""
    try:
        return parse_file(path)
    except (GedcomError, OSError) as exc:
        # CliRunner от Click мержит stderr в result.output по умолчанию
        # (mix_stderr=True), так что тесты видят это сообщение.
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _load_document(path: Path) -> GedcomDocument:
    """Прочитать как :class:`GedcomDocument`; на ошибке — Exit(1)."""
    try:
        return parse_document_file(path)
    except (GedcomError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def stats(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Показать сводку по GEDCOM-файлу: кодировка, теги, даты, места, фамилии."""
    records, encoding = _load(path)

    counts: Counter[str] = Counter(r.tag for r in records)
    persons = counts.get("INDI", 0)
    families = counts.get("FAM", 0)

    typer.echo(f"Encoding: {encoding.name} (confidence={encoding.confidence:.2f})")
    typer.echo(f"Records:  {len(records)}")
    typer.echo("Tags:")
    for tag, n in counts.most_common():
        typer.echo(f"  {tag:<8} {n}")
    typer.echo(f"Persons:  {persons}")
    typer.echo(f"Families: {families}")

    # Семантический срез: даты, места, фамилии. Парсим заново через
    # parse_document_file (быстро для умеренных файлов; кэширование оставлено
    # на потом, когда CLI будет использоваться на больших корпусах).
    doc = _load_document(path)

    # Все события (персон + семей).
    all_events = [e for p in doc.persons.values() for e in p.events] + [
        e for f in doc.families.values() for e in f.events
    ]
    dated_events = [e for e in all_events if e.date is not None]
    placed_events = [e for e in all_events if e.place is not None]

    typer.echo("Events:")
    typer.echo(f"  total       {len(all_events)}")
    typer.echo(f"  dated       {len(dated_events)}")
    typer.echo(f"  placed      {len(placed_events)}")

    # Охват дат: min/max по date_lower/date_upper всех известных границ.
    date_bounds = [e.date.date_lower for e in dated_events if e.date and e.date.date_lower]
    date_uppers = [e.date.date_upper for e in dated_events if e.date and e.date.date_upper]
    if date_bounds or date_uppers:
        min_date = min(date_bounds) if date_bounds else None
        max_date = max(date_uppers) if date_uppers else None
        typer.echo(f"  date range  {min_date} .. {max_date}")

    # Разнообразие мест: уникальные raw-PLAC значения.
    unique_places = {e.place.raw for e in placed_events if e.place}
    typer.echo(f"  unique PLAC {len(unique_places)}")

    # Топ-5 фамилий по числу персон.
    surnames: Counter[str] = Counter()
    for person in doc.persons.values():
        for name in person.names:
            for sur in name.surnames or ((name.surname,) if name.surname else ()):
                if sur:
                    surnames[sur] += 1
    if surnames:
        typer.echo("Top surnames:")
        for sur, n in surnames.most_common(5):
            typer.echo(f"  {sur:<20} {n}")


@app.command()
def parse(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Записать JSON в файл вместо stdout."
    ),
    compact: bool = typer.Option(False, "--compact", help="Компактный JSON без отступов."),
) -> None:
    """Распарсить файл и вывести AST в JSON."""
    records, encoding = _load(path)

    payload = {
        "encoding": encoding.model_dump(),
        "records": [r.model_dump() for r in records],
    }

    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)

    if output is not None:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)


@app.command()
def validate(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    sample: int = typer.Option(
        10, "--sample", help="Сколько примеров битых ссылок печатать (0 — все)."
    ),
) -> None:
    """Проверить xref-связи документа и вывести отчёт о битых ссылках.

    Exit-код 0 — ссылки целы, 1 — обнаружены битые (пригодно для CI).
    """
    doc = _load_document(path)
    broken = doc.verify_references(warn=False)

    typer.echo(f"Persons:    {len(doc.persons)}")
    typer.echo(f"Families:   {len(doc.families)}")
    typer.echo(f"Sources:    {len(doc.sources)}")
    typer.echo(f"Notes:      {len(doc.notes)}")
    typer.echo(f"Broken refs:{len(broken)}")

    if not broken:
        typer.echo("OK")
        raise typer.Exit(code=0)

    limit = len(broken) if sample == 0 else min(sample, len(broken))
    typer.echo(f"First {limit} broken references:")
    for ref in broken[:limit]:
        typer.echo(
            f"  {ref.owner_kind} {ref.owner_xref} . {ref.field} → "
            f"@{ref.target_xref}@ ({ref.expected_kind} not found)"
        )
    raise typer.Exit(code=1)


@app.command()
def diff(
    file1: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    file2: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    sample: int = typer.Option(
        10, "--sample", help="Сколько добавленных/удалённых xref печатать (0 — все)."
    ),
) -> None:
    """Сравнить два файла на уровне xref персон и семей.

    Считает «добавленным» то, что есть во ``file2`` и нет в ``file1``,
    «удалённым» — наоборот. Содержательное diff одного и того же xref
    (changed-fields) — отдельная подзадача.
    """
    doc1 = _load_document(file1)
    doc2 = _load_document(file2)

    persons1 = set(doc1.persons.keys())
    persons2 = set(doc2.persons.keys())
    families1 = set(doc1.families.keys())
    families2 = set(doc2.families.keys())

    persons_added = sorted(persons2 - persons1)
    persons_removed = sorted(persons1 - persons2)
    families_added = sorted(families2 - families1)
    families_removed = sorted(families1 - families2)

    typer.echo(f"file1: {file1.name} ({len(persons1)} persons, {len(families1)} families)")
    typer.echo(f"file2: {file2.name} ({len(persons2)} persons, {len(families2)} families)")
    typer.echo(f"Persons  added:   {len(persons_added)}")
    typer.echo(f"Persons  removed: {len(persons_removed)}")
    typer.echo(f"Families added:   {len(families_added)}")
    typer.echo(f"Families removed: {len(families_removed)}")

    def _print_sample(label: str, items: list[str], limit: int) -> None:
        if not items:
            return
        typer.echo(label)
        shown = items if limit == 0 else items[:limit]
        for x in shown:
            typer.echo(f"  + {x}" if "added" in label.lower() else f"  - {x}")
        if limit and len(items) > limit:
            typer.echo(f"  ... and {len(items) - limit} more")

    _print_sample("First persons added:", persons_added, sample)
    _print_sample("First persons removed:", persons_removed, sample)
    _print_sample("First families added:", families_added, sample)
    _print_sample("First families removed:", families_removed, sample)


def main() -> None:
    """Точка входа для console-script ``gedcom-tool``."""
    app()


if __name__ == "__main__":
    main()
