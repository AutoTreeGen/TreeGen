"""CLI ``gedcom-tool``: команды ``stats`` и ``parse``.

Использование (после ``uv sync``)::

    gedcom-tool stats path/to/tree.ged
    gedcom-tool parse path/to/tree.ged --compact -o tree.json

Команда ``stats`` выводит человеко-читаемую сводку (кодировка, число записей,
разбивка по тегам). Команда ``parse`` сериализует AST в JSON.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import typer

from gedcom_parser.exceptions import GedcomError
from gedcom_parser.parser import parse_file

app = typer.Typer(
    name="gedcom-tool",
    help="Утилиты для работы с GEDCOM-файлами.",
    add_completion=False,
    no_args_is_help=True,
)


def _load(path: Path) -> tuple[list, object]:
    """Прочитать и распарсить файл; на ошибке — печать и Exit(1)."""
    try:
        return parse_file(path)
    except (GedcomError, OSError) as exc:
        # CliRunner от Click мержит stderr в result.output по умолчанию
        # (mix_stderr=True), так что тесты видят это сообщение.
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def stats(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Показать сводку по GEDCOM-файлу: кодировка, число записей, теги."""
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


@app.command()
def parse(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Записать JSON в файл вместо stdout."
    ),
    compact: bool = typer.Option(
        False, "--compact", help="Компактный JSON без отступов."
    ),
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


def main() -> None:
    """Точка входа для console-script ``gedcom-tool``."""
    app()


if __name__ == "__main__":
    main()
