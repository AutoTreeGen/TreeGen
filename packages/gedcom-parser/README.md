# gedcom-parser

Парсер формата [GEDCOM 5.5.5](https://gedcom.io) с автоопределением кодировки
и AST-моделью для последующего семантического разбора.

## Состояние

**Этап 1/14:** реализованы декодер кодировок (UTF-8/UTF-16/CP1251/CP1252/
CP437/CP866/MAC-ROMAN, ANSEL — с warning-fallback на latin1), лексер со
склейкой CONT/CONC, AST-парсер уровневой иерархии, CLI с командами
`stats`/`parse`. Семантический разбор (нормализация дат, имён, мест),
валидатор, round-trip writer — следующие итерации (см. `ROADMAP.md` → Фаза 1).

## Программный API

```python
from gedcom_parser import parse_file

records, encoding = parse_file("tree.ged")
print(f"Encoding: {encoding.name} ({encoding.confidence:.0%})")

for r in records:
    print(r.tag, r.xref_id or "")

# Поиск
head = records[0]
char = head.find("CHAR")        # GedcomRecord | None
all_names = head.find_all("NAME")
sour_value = head.get_value("SOUR", default="<unknown>")

# Обход поддерева
for node in head.walk():
    print("  " * node.level, node.tag, node.value)
```

Семантический слой:

```python
from gedcom_parser import parse_document_file

doc = parse_document_file("tree.ged")
person = doc.persons["I1"]

# Citation sub-tags (PAGE / QUAY / EVEN / ROLE / DATA / NOTE / OBJE)
# доступны через event.citations / person.citations / family.citations.
# См. docs/gedcom-extensions.md §8 для полного маппинга GEDCOM → API.
for citation in person.events[0].citations:
    print(citation.source_xref, citation.page, citation.quality)

# Source record sub-tags (TITL / AUTH / ABBR / PUBL / REPO / TEXT) — на Source.
source = doc.sources["S1"]
print(source.title, source.abbreviation, source.repository_xref)
```

Альтернативные точки входа:

```python
from gedcom_parser import iter_lines, parse_text, parse_bytes, decode_gedcom

# Только лексер (CONT/CONC уже склеены).
for line in iter_lines(text):
    print(line.level, line.tag, line.value)

# Текст уже декодирован.
records = parse_text(text)

# Сырые байты — encoding определяется автоматически.
records, info = parse_bytes(raw_bytes)

# Только декодирование.
text, info = decode_gedcom(raw_bytes)
```

## CLI

```powershell
# Сводка по файлу
uv run gedcom-tool stats path/to/tree.ged

# Полный AST в JSON
uv run gedcom-tool parse path/to/tree.ged
uv run gedcom-tool parse path/to/tree.ged --compact -o tree.json
```

## Тесты

```powershell
uv run pytest packages/gedcom-parser -v

# Только быстрые
uv run pytest packages/gedcom-parser -m "not slow and not integration"

# На личном GED-файле (Ztree.ged в корне репо, в .gitignore)
uv run pytest packages/gedcom-parser -m gedcom_real
```

### Корпус реальных GED-файлов

`tests/test_smoke_corpus.py` параметризуется списком `*.ged` из папки,
заданной переменной окружения `GEDCOM_TEST_CORPUS` (по умолчанию
`D:/Projects/GED`). Корпус включает экспорты от Ancestry, MyHeritage, Geni
разных лет, кодировок (UTF-8, UTF-16, ANSEL, CP1251) и размеров — до
150 МБ. Если папки нет, тесты пропускаются.

```powershell
# Нестандартный путь
GEDCOM_TEST_CORPUS=/path/to/ged-files uv run pytest packages/gedcom-parser -m gedcom_real
```

## Lenient-режим

`parse_text` / `parse_bytes` / `parse_file` / `iter_lines` принимают
`lenient: bool = True`. В lenient-режиме (по умолчанию) парсер **не падает**
на двух типичных проблемах реальных файлов:

- **Продолжение значения без `CONT`/`CONC`** (часто у MyHeritage и старых
  Geni): строка приклеивается к value предыдущей записи через `\n`.
- **Прыжок уровня** (например, 3 → 5 без промежуточного 4): узел
  привязывается к верхушке стека.

Каждое такое событие сопровождается `GedcomLenientWarning`. В строгом
режиме (`lenient=False`) обе ситуации — исключения (`GedcomLexerError` /
`GedcomParseError`).
