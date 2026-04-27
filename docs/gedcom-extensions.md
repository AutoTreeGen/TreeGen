# GEDCOM Extensions

Документ описывает наши расширения GEDCOM 5.5.5 и обработку проприетарных тегов
основных платформ.

> **Статус:** черновик. Заполняется в Фазе 1.

---

## 1. Канонический формат

**GEDCOM 5.5.5** (<https://gedcom.io>). Round-trip без потерь — обязательное требование.

Обратная совместимость:

- 5.5.1 — читаем, при экспорте конвертируем в 5.5.5.
- 5.5 — читаем (best effort), при экспорте — 5.5.5 + лог потерь.

---

## 2. Проприетарные теги (читаем, маппим в наши структуры)

### 2.1 Ancestry

| Тег | Значение | Маппинг |
|---|---|---|
| `_APID` | Ancestry Person ID | `provenance.ancestry_person_id` |
| `_TID` | Ancestry Tree ID | `provenance.ancestry_tree_id` |
| `_PID` | Ancestry Profile ID | `provenance.ancestry_profile_id` |
| `_MTTAG` | Tag (фото/документ) | `multimedia_object.tags[]` |
| `_LINK` | Ссылка на источник | `citation.url` |

### 2.2 MyHeritage

| Тег | Значение | Маппинг |
|---|---|---|
| `_UPD` | Last update timestamp | `provenance.myheritage_updated_at` |
| `_UID` | Unique ID | `provenance.myheritage_uid` |

### 2.3 Geni

| Тег | Значение | Маппинг |
|---|---|---|
| `_GENI_PROFILE_ID` | Geni profile ID | `provenance.geni_profile_id` |

### 2.4 FamilySearch

| Тег | Значение | Маппинг |
|---|---|---|
| `_FSFTID` | FamilySearch ID | `provenance.familysearch_id` |

> Расширять по мере встреч с реальными файлами разных платформ. Каждый
> непонятный тег — фиксируем в `import_jobs.unrecognized_tags[]` и
> добавляем сюда.

---

## 3. Наши собственные расширения

Префикс — `_ATG_` (AutoTreeGen).

| Тег | Где встречается | Значение |
|---|---|---|
| `_ATG_CONF` | INDI, FAM, EVEN | `confidence_score` (0–1) |
| `_ATG_HYP` | INDI, FAM, EVEN | ID гипотезы (`@H123@`) |
| `_ATG_PROV` | везде | inline provenance JSON |
| `_ATG_TRSL` | NAME, PLAC | альтернативные транслитерации |

При экспорте — опционально (по флагу `--include-extensions`), чтобы файл
оставался совместимым с другими тулами.

---

## 4. Кодировки

Автоопределение в порядке убывания приоритета:

1. BOM (UTF-8/UTF-16).
2. `1 CHAR` запись в HEAD.
3. Эвристика по байтам:
   - ASCII: только < 0x80.
   - UTF-8: валидный multi-byte.
   - ANSEL: характерные escape-последовательности.
   - CP1251 / CP866: статистический анализ частот байтов.

При экспорте — всегда UTF-8.

---

## 5. Даты

Поддерживаемые форматы (см. GEDCOM 5.5.5 spec, §Date):

- Точные: `25 APR 1850`
- Приблизительные: `ABT 1850`, `CAL 1850`, `EST 1850`
- Диапазоны: `BET 1840 AND 1845`, `FROM 1850 TO 1860`
- До/после: `BEF 1850`, `AFT 1850`
- Календари: григорианский (по умолчанию), юлианский (`@#DJULIAN@`),
  иврит (`@#DHEBREW@`), французский республиканский (`@#DFRENCH R@`).
- Иврит-даты с месяцами TSH, CSH, KSL, TVT, SHV, ADR, ADS, NSN, IYR, SVN, TMZ, AAV, ELL.

Все даты сохраняются с:

- `raw` — оригинальная строка GEDCOM,
- `parsed_range` — `[earliest, latest]` (UTC date),
- `uncertainty_days` — оценка неопределённости,
- `calendar` — какой календарь использовался.

---

## 6. Имена

GEDCOM-нотация: `John /Smith/ Jr.` где `/Smith/` — фамилия.

Структурные части (тег NAME с поднодами):

- `GIVN` — given name
- `SURN` — surname
- `NPFX` — prefix (Mr., Dr., …)
- `NSFX` — suffix (Jr., III, …)
- `NICK` — nickname
- `SPFX` — surname prefix (van, de, …)

Дополнительно (наши расширения):

- `_ATG_TRSL` — альтернативные транслитерации (см. §3).

См. ADR-0008 для стратегии транслитерации.

---

## 7. Места

Иерархия в GEDCOM: запятая-разделённый список от мелкого к крупному:
`Slonim, Slonim County, Grodno Governorate, Russian Empire`.

Наша обработка:

- Парсим компоненты, нормализуем через gazetteer.
- Связываем с современным административным делением через `place_aliases`.
- Историческая привязка: «на дату события» — какая страна/империя/губерния.
- Гео-кодинг (lat/lon) — опционально, через подключаемый провайдер.

---

## 8. Source citations (sub-tags)

GEDCOM 5.5.5 §3.5 определяет `SOURCE_CITATION` — ссылку на источник
(`SOUR @S1@`) внутри персоны, семьи или события — со своим набором подтегов.
Phase 1.x сделала их first-class через `gedcom_parser.entities.Citation`.
Раньше высокоуровневое API возвращало только `event.sources_xrefs: tuple[str, ...]`
и теряло `PAGE` / `QUAY` / `EVEN` / `ROLE` / `DATA` / `NOTE` — каждый потребитель
вынужден был спускаться к raw `GedcomRecord`. Теперь это не нужно.

### 8.1 GEDCOM → entity-API маппинг

| GEDCOM tag | Где встречается | Поле `Citation` | Тип |
|---|---|---|---|
| `n SOUR @S1@` | INDI, FAM, любое EVENT | `source_xref="S1"` | `str` (без `@`) |
| `n SOUR <inline-text>` | INDI, FAM, любое EVENT | `inline_text=<text>`, `source_xref=None` | `str` |
| `+1 PAGE …` | под SOUR | `page` | `str \| None` |
| `+1 QUAY 0..3` | под SOUR | `quality` | `int \| None` (`None` если не 0..3) |
| `+1 EVEN <event>` | под SOUR | `event_type` | `str \| None` |
| `+2 ROLE <role>` | под EVEN | `event_role` | `str \| None` |
| `+1 DATA` → `+2 DATE …` | под SOUR | `data_date_raw` | `str \| None` |
| `+1 DATA` → `+2 TEXT …` (×N) | под SOUR | `data_text` (объединено через `\n`) | `str \| None` |
| `+1 NOTE @N1@` (×N) | под SOUR | `notes_xrefs` | `tuple[str, ...]` |
| `+1 NOTE <inline>` (×N) | под SOUR | `notes_inline` | `tuple[str, ...]` |
| `+1 OBJE @O1@` (×N) | под SOUR | `objects_xrefs` | `tuple[str, ...]` |

`Citation` — frozen Pydantic-модель (`extra="forbid"`).

### 8.2 Контейнеры

| Контейнер | Поле |
|---|---|
| `Event` | `citations: tuple[Citation, ...]` |
| `Person` | `citations: tuple[Citation, ...]` (для `1 SOUR …` прямо под INDI) |
| `Family` | `citations: tuple[Citation, ...]` (для `1 SOUR …` прямо под FAM) |

Поле `*.sources_xrefs: tuple[str, ...]` сохраняется в API для обратной
совместимости — содержит только xref-цели тех `SOUR`, у которых value был
`@…@`. Inline-источники (`1 SOUR Family bible`) в `sources_xrefs` **не**
попадают, но видны через `citations[i].inline_text`.

### 8.3 Source record (sub-tags верхнего уровня)

Запись `0 @S1@ SOUR …` маппится в `gedcom_parser.entities.Source` со следующими
полями:

| GEDCOM tag | Поле `Source` | Тип |
|---|---|---|
| `1 TITL …` | `title` | `str \| None` |
| `1 AUTH …` | `author` | `str \| None` |
| `1 ABBR …` | `abbreviation` | `str \| None` |
| `1 PUBL …` | `publication` | `str \| None` |
| `1 REPO @R1@` | `repository_xref` (без `@`) | `str \| None` |
| `1 TEXT …` | `text` | `str \| None` |

### 8.4 Пример

GEDCOM-фрагмент:

```text
0 @I1@ INDI
1 NAME John /Smith/
1 BIRT
2 DATE 1 JAN 1850
2 SOUR @S1@
3 PAGE p. 42
3 QUAY 3
3 EVEN BIRT
4 ROLE FATH
3 DATA
4 DATE 12 MAR 1900
4 TEXT first excerpt
4 TEXT second excerpt
3 NOTE @N1@
3 NOTE Inline observation about evidence

0 @S1@ SOUR
1 TITL Lithuanian Census 1897
1 AUTH Imperial Office
1 ABBR LCens1897
1 PUBL 1898
1 REPO @R-VILN@
```

Python-доступ:

```python
from gedcom_parser import parse_document_file

doc = parse_document_file("tree.ged")
person = doc.persons["I1"]
event = person.events[0]                 # BIRT
citation = event.citations[0]            # один SOUR
assert citation.source_xref == "S1"
assert citation.page == "p. 42"
assert citation.quality == 3
assert citation.event_type == "BIRT"
assert citation.event_role == "FATH"
assert citation.data_date_raw == "12 MAR 1900"
assert citation.data_text == "first excerpt\nsecond excerpt"
assert citation.notes_xrefs == ("N1",)
assert citation.notes_inline == ("Inline observation about evidence",)

source = doc.sources["S1"]
assert source.title == "Lithuanian Census 1897"
assert source.abbreviation == "LCens1897"
assert source.repository_xref == "R-VILN"
```

### 8.5 Сценарии и edge cases

- `QUAY` за пределами `0..3` или нечисловой — `quality=None`, цитата
  сохраняется. Round-trip величины — через AST (`GedcomRecord`).
- `EVEN` без `ROLE` — `event_type` заполнен, `event_role=None`.
- `DATA` с одним `TEXT` — `data_text` содержит этот один текст без `\n`-обвеса.
- `OBJE` под citation — отдельные xref-объекты (мультимедиа, привязанные
  к самой ссылке, а не к персоне).
- `Source` без `TITL`, только с `ABBR` — встречается у Geni и старых FTM-экспортов.
  `title=None`, `abbreviation` заполнен.

---

## 9. Известные «грязные» паттерны

Реальные GEDCOM-файлы содержат:

- Битые xref-ссылки (`@I999@` без соответствующей `0 @I999@ INDI` записи).
- Циклы (A — родитель B, B — родитель A).
- Дубликаты (одна персона импортирована дважды с разными ID).
- Неконсистентные кодировки (UTF-8 в файле объявленном как ANSEL).
- Превышение длины строки (255 символов в спеке) без CONT/CONC.
- Проприетарные теги без документации.

Стратегия: парсить best-effort, всё подозрительное — в отчёт валидатора.
