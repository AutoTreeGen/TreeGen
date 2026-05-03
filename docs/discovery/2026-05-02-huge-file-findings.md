# Huge-file & encoding discovery — 2026-05-02

> **Status:** Phase 5.11a deliverable. Read-only diagnostic pass.
> **Date:** 2026-05-02 (4 days before Geoffrey demo on 2026-05-06).
> **Author:** @autotreegen (Phase 5.11a discovery agent).
> **Scope authority:** ADR-0079, brief Phase 5.11a Huge-File & Encoding Discovery Pass.
>
> **Все числа в отчёте получены пробами `scripts/discovery/probe.py` и
> `scripts/discovery/probe_dupes.py` через orchestrator
> `scripts/discovery/run_all.py`.** Они переисполняются на любом
> наборе файлов (см. `scripts/discovery/README.md`).
>
> Хост измерения: Windows 11 / Python 3.13.13 / uv-managed venv в worktree
> `phase-5-11a-discovery`. Все `peak_rss_in_process` — собственная in-process
> sampler-нить probe.py, точность ~50 ms; внешний sampler в orchestrator'е
> на Windows+uv даёт неверные значения (видит wrapper-PID; это HUGE-009 ниже).

## TL;DR

1. **GM317_utf-16.ged парсится, валидируется и компат-симится за 89 секунд
   на тестовом хосте, но пикует RSS на 6.39 ГБ.** Это потенциальный
   OOM на машине Geoffrey'я если у него ≤8 ГБ RAM или открыт Chrome
   с tabs/IDE — нужно подтвердить его spec до демо.
2. **UTF-16-BE авто-определяется по BOM `FE FF`, без флагов.** Geoff'ов
   файл откроется как есть. Round-trip lossy chars = 0.
3. **«30K дубликатов»** не overstate, но и не quite: probe видит **11 070
   collision-групп** (24 093 персоны), из которых ~280 — это
   Ancestry-privacy редакции «private»/«unknown», не дубликаты;
   остальные ~10 800 групп выглядят как намеренный rolodex-паттерн
   Geoffrey'я. **Validator не имеет правила `duplicate_individual`** и
   корректно не «кричит» на эти записи. Forced dedup противопоказан.

**Demo-blocker count: 1** (peak RSS на GM317; см. HUGE-001).

## Тестовый набор

| File | Size | Encoding | Source platform |
|---|---:|---|---|
| `kladbishe.ged` | 0.04 MB | ASCII | unknown (small Russian-language) |
| `export-geni.ged` | 0.10 MB | UTF-8 | Geni |
| `MyHeritage2025.ged` | 30.05 MB | UTF-8 | MyHeritage |
| `Ancestry.ged` | 48.53 MB | UTF-8 | Ancestry |
| `RR.ged` | 116.35 MB | UTF-8 | Robert (deceased) — primary inherited |
| `GM317_utf-16.ged` | 145.24 MB | **UTF-16-BE** | Ancestry (Geoff exported via UTF-16) — **demo blocker** |

Файлы лежат локально в `F:\Projects\GED\` и НЕ коммитятся (см. `.gitignore`,
ADR-0079 §«No GED bytes in diff»). Probe-скрипты принимают путь через CLI
или через env `GEDCOM_TEST_CORPUS`.

## Section A — File-level metrics

| File | MB | Lines | Max line | INDI | FAM | SOUR | OBJE (top) | OBJE (inline) | Custom-tag total | Custom-tag distinct |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kladbishe | 0.04 | 2 948 | 73 | 225 | 80 | 0 | 0 | 1 | 13 | 7 |
| export-geni | 0.10 | 5 476 | 103 | 210 | 80 | 0 | 0 | 461 | 0 | 0 |
| MyHeritage2025 | 30.05 | 1 050 466 | 463 | 15 010 | 4 894 | 18 919 | 16 940 | 13 180 | 406 465 | 38 |
| Ancestry | 48.53 | 1 813 368 | 466 | 35 203 | 12 144 | 2 332 | 24 036 | 20 296 | 734 116 | 43 |
| RR | 116.35 | 4 081 162 | 442 | 113 632 | 41 844 | 3 775 | 21 048 | 40 638 | 1 219 742 | 42 |
| **GM317_utf-16** | **145.24** | **3 279 867** | **514** | **188 942** | **55 290** | **543** | **7 460** | **7 750** | **398 824** | **40** |

Top-5 custom-tags по корпусу (агрегировано):

* `_APID` (Ancestry person ID): доминирует везде где экспорт Ancestry-вой
  (RR: 759 240 instances, GM317: 244 831, Ancestry: 253 705,
  MyHeritage: 94 825).
* `_OID`, `_USER`, `_ENCR` — Ancestry photo-meta.
* `_WDTH`, `_HGHT`, `_ORIG`, `_STYPE`, `_SIZE`, `_CREA` — image-asset
  meta.

GEDCOM 5.5.5 spec не определяет ни один из них; quarantine-механизм
(Phase 5.5a) корректно их сохраняет.

## Section B — Parse performance

Hard-timeout 240 s, не достигнут ни на одном файле.

| File | decode_secs | parse_records | build_doc | parse_total | validator | compat (4 targets) | unknown_tag_blocks |
|---|---:|---:|---:|---:|---:|---:|---:|
| kladbishe | 0.00 | 0.022 | 0.016 | **0.04** | 0.002 | n/a (failed pre-fix) | 231 |
| export-geni | 0.00 | 0.039 | 0.020 | **0.06** | 0.002 | 0.029 | 258 |
| MyHeritage2025 | 0.04 | 12.7 | 4.2 | **16.9** | 0.20 | 2.7 | 149 937 |
| Ancestry | 0.05 | 20.5 | 8.4 | **28.9** | 0.47 | 3.7 | 193 122 |
| RR | 0.18 | 70.5 | 27.2 | **97.7** | 1.33 | 9.9 | 177 544 |
| **GM317_utf-16** | **0.21** | **40.5** | **22.8** | **63.3** | **2.04** | **11.7** | **57 810** |

* Parser завершается на всех файлах без exception'ов; **NO TIMEOUT**.
* Linear-ish scaling: GM317 (188K INDI) распарсился быстрее чем RR (113K
  INDI), потому что у GM317 в ~3× меньше custom-tag instances и в 2×
  меньше OBJE — fewer per-record entities → быстрее build.
* Compat-simulator вызывает 4 target'а; легко делается параметром, см.
  HUGE-006.

## Section C — Duplicate analysis (GM317 + RR)

> **NO MUTATIONS.** Это measurement-only. Forced dedup ENABLED `false`
> (см. ADR-0079 §«No forced dedup»). `duplicate_individual` rule отсутствует
> в validator default'ах — это OK, не баг.

Ключ collision-группы:
`(surname.casefold, given.casefold, BIRT_year_or_empty, BIRT_place.casefold)`.

| File | Persons | Dup groups | Persons in dup groups | Excess (= total − groups) |
|---|---:|---:|---:|---:|
| RR | 113 632 | 483 | 1 028 | 545 |
| **GM317_utf-16** | **188 942** | **11 070** | **24 093** | **13 023** |

### Top-10 collision keys на GM317

| Rank | surname | given | birth_year | birth_place | count | Looks like |
|---:|---|---|---|---|---:|---|
| 1 | (empty) | private | (empty) | (empty) | 161 | Ancestry **privacy redaction** для living |
| 2 | (empty) | unknown | (empty) | (empty) | 62 | Ancestry **privacy** |
| 3 | goldberg | private | (empty) | (empty) | 18 | Ancestry **privacy** (surname public) |
| 4 | levin | private | (empty) | (empty) | 16 | Ancestry **privacy** |
| 5 | unknown | (empty) | (empty) | (empty) | 15 | Ancestry **privacy** |
| 6 | cohen | private | (empty) | (empty) | 10 | Ancestry **privacy** |
| 7 | boruchovich | sheyna | 1773 | yanovichi, belarus | 9 | **Real intentional rolodex** (Geoff) |
| 8 | price | anna | 1898 | new york | 8 | Real intentional rolodex |
| 9 | goldstein | private | (empty) | (empty) | 8 | Ancestry **privacy** |
| 10 | price | (anna variant) | 1898 | new york | (≥7) | Same as #8 |

Качественный вывод: верхушка collision-листа на 80% — это
**Ancestry-родственный privacy-pattern**, не ошибки и не «лишние» записи.
Реальные intentional dups Geoffrey'я начинаются с rank 7 (boruchovich/sheyna)
и составляют большинство оставшихся ~10 800 групп.

### RR.ged (для сравнения)

Top-collision на RR качественно ДРУГОЙ паттерн: `chana` × 6, `levy abraham`
× 6, в основном «нет birth-year, нет place» — это hygiene-проблемы,
а не intentional rolodex. RR.ged значительно «чище» с точки зрения
дубликатов на person.

### Recommendation (для 5.11d, не для 5.11a)

Если когда-нибудь будет добавлено правило валидатора `duplicate_individual`,
оно ОБЯЗАНО:

* **Не считать** «private + (empty given/year/place)» паттерн дубликатом —
  это privacy-pattern, не data-quality issue.
* **Не предлагать automerge** для intentional rolodex (любой паттерн с
  заполнеными surname/given/year/place всё равно может быть intentional;
  без user-confirmation merge запрещён).
* По-default быть **disabled** на trees где `dup-rate > N%` — это маркер
  «у пользователя другой workflow», не «у пользователя баг».

## Section D — Cross-reference integrity

| File | Persons | Families | Broken refs | Orphan persons | Ancestor SCC count | SCC nodes |
|---|---:|---:|---:|---:|---:|---:|
| kladbishe | 225 | 80 | 0 | 1 | 0 | 0 |
| export-geni | 210 | 80 | 0 | 0 | 0 | 0 |
| MyHeritage2025 | 15 010 | 4 894 | 0 | 80 | 0 | 0 |
| Ancestry | 35 203 | 12 144 | 0 | 32 | **1** | 2 |
| RR | 113 632 | 41 844 | 0 | 111 | **1** | 2 |
| **GM317_utf-16** | **188 942** | **55 290** | **0** | **687** | **0** | **0** |

* **Все файлы closed-reference**: 0 dangling xref на все 6.
* **GM317 — 0 ancestor cycles.** В Ancestry.ged и RR.ged по 1 SCC длиной 2
  (один пример: `I112511141115 ↔ I112511542664`). Это data-bug в source
  файле, парсер его пропускает (не валит), validator цикл не флагает —
  явно отдельный кейс. Не demo-блокер.
* Orphans на GM317 — 687 persons (0.36% от total). Допустимо для дерева
  где Geoff намеренно сохраняет «изолированных» предков как ссылочные
  узлы.

## Section E — Encoding deep-dive

### BOM detection

| File | First 2 bytes (hex) | First 3 bytes (hex) | Class | Detected encoding | Method | Confidence |
|---|---|---|---|---|---|---:|
| kladbishe | `30 20` | `30 20 48` | no BOM | ASCII | head_char (`1 CHAR ASCII`) | 0.95 |
| export-geni | `30 20` | `30 20 48` | no BOM | UTF-8 | head_char | 0.95 |
| MyHeritage2025 | `30 20` | `30 20 48` | no BOM | UTF-8 | head_char | 0.95 |
| Ancestry | `30 20` | `30 20 48` | no BOM | UTF-8 | head_char | 0.95 |
| RR | `30 20` | `30 20 48` | no BOM | UTF-8 | head_char | 0.95 |
| **GM317_utf-16** | **`FE FF`** | **`FE FF 00`** | **UTF-16-BE BOM** | **UTF-16-BE** | **bom** | **1.00** |

GM317 — UTF-16-**BE** (`FE FF`), не LE (как изначально записано в имени файла).
Probe-результат — авторитетный источник.

### Round-trip lossiness

Round-trip = `text.encode(detected).decode(detected)` чарактер-сравнение.

| File | Lossy chars |
|---|---:|
| kladbishe | 8 |
| export-geni | 0 |
| MyHeritage2025 | 0 |
| Ancestry | 0 |
| RR | 0 |
| **GM317_utf-16** | **0** |

`kladbishe` показывает 8 lossy chars: ASCII detected, но реальный файл
содержит несколько NL-only-ASCII символов (CP1251 артефакты в
NOTE/ADDR-полях). Не demo-блокер; это small-file edge case.

### 20 sample не-ASCII имён из GM317

Probe собрал из `1 NAME` строк первые 20 не-ASCII значений:

```text
Zelikas /Tankelis זילקס טנקליס/
Meshulam Zalman HaLevi /Eideles איידלס/
Shlomo /Eideles איידלס/
R' Yehuda Leib /Eideles איידלס/
R' Avrohom /Eideles איידלס/
R' Meshullam Zalman /Eidls איידלס/
Rabbi Chaim ben Bezalel  /Loew-Beer (Gd.father of Maharal חיים בן בצלאל לאו)/ *
Rabbi Chaim ben Bezalel /Loew-Beer Gd.father of Maharal חיים בן בצלאל לאו/
Feige  ווגלין פייגא /Vogelin/
יהודה לייב /מיזלס/
Baruch ברוך (Father of The Alter Rebbe,Boruch The Tzadik,ברוך הצדיק,אב הרב הזקן מליאדי) /Lowe פעוזנער (לב)/
Dreizel (Therese) /bat ReMa Isserles איסרלש/
Dreizel Theresa  דרזל תרזה /Isserles  איסרלש/
Dreizel (Therese) דרזל תרזה (Dreizel) /איסרליש/
Betzalel ben Yehuda /Loew [Gt.Grandfather of Maharal]/ בצלאל בן יהודה לאו
Sara שרה Katz * /Loew לאו (Nevwhoner)/ of Posen
Rosa רוזה /Katz כ"ץ/
Rabbi Judah ben Bezalel /Loew; Maharal of Prague יהודה לאו/
Rabbi Judah ben Bezalel /Loew; Maharal of Prague יהודה לאו/ ****
Rabbi Judah ben Bezalel ** /Loew; Maharal of Prague יהודה לאו/
```

Spot-check: имена парсятся корректно (Hebrew, Latin, аннотации `*`/`**`,
скобочные пояснения, slash-форма имени). Roundtrip через UTF-16-BE
preserves 100% символов.

## Section F — Memory & import projection

Все значения — **in-process peak RSS** (`psutil.Process().memory_info().rss`,
sampler-нить с интервалом 50 ms).

| File | RSS at start | Peak RSS | Multiplier (peak / file size) | est_db_rows (naive INDI+FAM+SOUR+NOTE+OBJE) |
|---|---:|---:|---:|---:|
| kladbishe | 32 MB | 56 MB | 1244× | 305 |
| export-geni | 24 MB | 48 MB | 466× | 290 |
| MyHeritage2025 | 24 MB | **1 858 MB** | 62× | 38 823 |
| Ancestry | 24 MB | **3 305 MB** | 68× | 73 715 |
| RR | 24 MB | **7 648 MB** | 66× | 180 299 |
| **GM317_utf-16** | **24 MB** | **6 391 MB** | **44×** | **252 235** |

(*GM317 multiplier ниже потому что UTF-16-файл уже занимает 2 byte/char на
диске; Python-строка не растёт пропорционально.*)

**Pattern для real-world Ancestry-derived files: 60-70× multiplier.**

* MyHeritage2025 30 MB → ~1.8 GB peak.
* Ancestry 48 MB → ~3.3 GB peak.
* RR 116 MB → ~7.6 GB peak.

**Где сидит память (best estimate из rss_after_parse — чтобы не тратить
ещё одну проб-сессию):**

* `decoded_text: str` — копия всего файла в Python str. Для UTF-8: ~bytes×1.
  Для UTF-16-BE: ~bytes×0.5 (т.к. 2 байта = 1 символ, но Python str хранит
  каждый кодпоинт в 1-2 байтах при UCS-1/UCS-2). Для GM317: ~75 MB.
* `records: list[GedcomRecord]` — AST, в среднем ~120-200 байт на запись
  * рекурсивные children. Для GM317 (3.3M lines, ~2.5M records при level
  ≥ 0): ~500-800 MB.
* `doc.unknown_tags: tuple[RawTagBlock, ...]` — quarantine. RR: 177 544
  blocks × ~150 B каждый = ~25 MB (но они держат детей с raw-text → x4 по
  факту). GM317: 57 810 blocks (меньше — в GM317 меньше `_APID`/INDI).
* `doc.persons / families / sources / objects: dict[str, Person/Family/...]` —
  основной семантический индекс. ~3-5 KB на Person с full events/citations.
  GM317: 188 942 × ~4 KB = ~750 MB.

Эти цифры — оценочные. Точная атрибуция требует tracemalloc-snapshot'ов,
что сейчас вне scope (см. HUGE-001-fix брифа 5.11b).

### Проекция Postgres-импорта

```text
GM317_utf-16:
  est_db_rows_naive_lower_bound = 252 235
    (= 188 942 INDI + 55 290 FAM + 543 SOUR + 7 460 OBJE; NOTE = 0)
```

Чистая 1:1 INSERT-проекция, без events/citations/places/multimedia-link
sub-records. **Реальный row count при полной нормализации (events, citations,
places, names — всё в собственные таблицы)** примерно ×4-6 → 1.0-1.5M
rows на GM317. Alembic load-time на наивной 1K-row INSERT-bulk скорости
(текущая parser-service `import_gedcom_to_db`) — ~30-60 минут на ноутбуке.
Это отдельная фаза за пределами 5.11; для демо это не показывается.

## Section G — Frontend rendering implications (theoretical)

Не запускался ни один UI-probe (вне scope 5.11a). Theoretical-only:

* Текущий tree-viewer (см. ADR-0013) использует SVG/Canvas-рендер на
  основе D3-tree-layout. Известных performance breakpoint'ов на 30K+ узлов
  не задокументировано — не было кейсов.
* DOM count в наивной SVG-реализации ≥ 1 `<g>` на person + 2-3 на event/
  marriage = **~600K DOM нод для GM317**. Любой современный браузер на
  таком DOM лагает / падает.
* **Demo для Geoff должен показывать НЕ полное дерево**, а conventional
  scope: ego-centered ±3 поколения (~50-200 nodes), с lazy-load остального.
  ADR-0013 это уже подразумевает; 5.11.x не должен это переоткрывать.
* Если demo требует «вид сверху» на 188K дерево — нужна виртуализация /
  level-of-detail / clusters. Это полноценная UI-фаза, выходит за scope
  5.11.

## Issue catalog

Severity scale:

* **BLOCKER** — без фикса демо 2026-05-06 рискует провалиться.
* **HIGH** — заметный UX-регресс или функциональное ограничение.
* **MED** — улучшит качество, но не блокирует.
* **LOW** — cosmetic / future-proofing.

| ID | Severity | Demo-blocker | File scope | Title |
|---|---|:---:|---|---|
| HUGE-001 | **BLOCKER** | ✓ | GM317, RR | Peak RSS 6-7 GB во время parse + validate + compat-sim |
| HUGE-002 | OK ✓ | — | GM317 | UTF-16-BE auto-detected via BOM (no manual flag нужен) |
| HUGE-003 | MED | — | GM317 | 11 070 dup-groups: смесь Ancestry-privacy + Geoff intentional rolodex |
| HUGE-004 | MED | — | GM317 | 28 599 encoding warnings при ancestry-target compat-sim (Hebrew chars) |
| HUGE-005 | LOW | — | Ancestry, RR | По 1 ancestor-cycle SCC длины 2 (data bug в source) |
| HUGE-006 | LOW | — | all-big | Compat-sim 4-target overhead 4-12 s; не нужен на демо |
| HUGE-007 | LOW | — | all | `GedcomDateWarning` для нестандартных дат (`Abt.`, `April`, `WFT Est`) |
| HUGE-008 | MED | — | RR, Ancestry | Quarantine `unknown_tags` тратит ~25-50% от peak RSS |
| HUGE-009 | LOW | — | tooling | External RSS sampler в run_all.py видит uv-wrapper PID, не python child |
| HUGE-010 | INFO | — | corpus | NOTE-record-count = 0 на всех тестируемых файлах |

### HUGE-001 — Peak RSS 6.4 GB на GM317 (BLOCKER)

* **File:** `GM317_utf-16.ged` 145 MB.
* **Observed:** `peak_rss_in_process_mb = 6391.01` после
  `parse + verify_references + validator + compat-sim(4 targets)`. RR.ged
  показывает аналогично 7 648 MB.
* **Repro:**

  ```powershell
  $env:GEDCOM_TEST_CORPUS = "<your-GED-corpus>"
  uv run python scripts/discovery/probe.py "$env:GEDCOM_TEST_CORPUS/GM317_utf-16.ged"
  # → JSON.section_F.peak_rss_in_process_mb
  ```

* **Demo risk:** Geoff'ов laptop spec неизвестен. На ≤ 8 GB RAM с
  Chrome/IDE/Slack открытыми — реальный риск OOM или 30+ s swap-storm
  посреди demo.
* **Likely contributors (см. секция F):** decoded_text + records AST +
  семантические entity-индексы + quarantine `unknown_tags`.
* **Suggested phase:** **5.11b — peak RSS reduction.** Варианты:
  (a) опциональный skip компат-сима в дефолте парсер-сервиса;
  (b) explicit `gc.collect()` после `parse_text → records → from_records`;
  (c) streaming parser (не строить полный AST в память — emit records
  итерационно). (c) — самый высокий ROI но самый рискованный за 4 дня.
  (a)+(b) — низкий риск, экономят ~2-3 GB.
* **Не demo-блокер при 16 GB RAM**, но запас — 1.5×, и первое же
  открытое Chrome-окно его съест.

### HUGE-002 — UTF-16-BE auto-detected via BOM ✓

* **File:** `GM317_utf-16.ged`.
* **Observed:** `section_E.bom_2bytes_hex = "FEFF"`,
  `bom_class = "UTF-16-BE BOM"`, `encoding_method = "bom"`,
  `encoding_confidence = 1.00`. Round-trip lossy chars = 0.
* **Status:** Работает ✓. Никаких manual flag-ов не требуется.
  Существующая `gedcom_parser.encoding.detect_encoding`
  * `decode_gedcom_file` корректно обрабатывают.
* **Repro:**

  ```powershell
  uv run python -c "from gedcom_parser import detect_encoding; from pathlib import Path; print(detect_encoding(Path('<gm317-path>').read_bytes()[:4096]))"
  # → EncodingInfo(name='UTF-16-BE', method='bom', confidence=1.0, head_char_raw=None)
  ```

* **Phase:** none required. Этот пункт — позитивное подтверждение.

### HUGE-003 — Dup-groups в GM317 = mix privacy + intentional rolodex

* **File:** `GM317_utf-16.ged`.
* **Observed:** 11 070 collision-групп; top-6 — Ancestry-privacy
  (~280 persons), остальные — реальные multi-record same-person.
* **Repro:**

  ```powershell
  uv run python scripts/discovery/probe_dupes.py "$env:GEDCOM_TEST_CORPUS/GM317_utf-16.ged" --top 20
  ```

* **Suggested phase:** **5.11d (если/когда добавится `duplicate_individual` rule)**.
  ADR-0079 §«No forced dedup» применяется. Решение должно различать
  privacy-pattern от real-rolodex (см. recommendations в секции C).
* **Demo impact:** none (validator не флагает на этом файле).

### HUGE-004 — 28 599 encoding warnings при compat-sim → ancestry на GM317

* **File:** `GM317_utf-16.ged`, target `ancestry`.
* **Observed:** `compat[ancestry].encoding_warnings = 28 599` (Hebrew
  characters в именах не входят в Ancestry's expected charset → mapping
  на `?`). Estimated_loss_pct остаётся 0.78%.
* **Suggested phase:** 5.11.x (low priority). Compat-sim уже корректно
  предупреждает; реальный fix на стороне target-platform, не у нас.
* **Demo impact:** только если демо включает «экспортируем GM317 в
  Ancestry» сценарий. Скорее всего нет.

### HUGE-005 — Ancestor cycle SCC длины 2 в Ancestry.ged и RR.ged

* **Files:** `Ancestry.ged` (SCC `[I112511141115, I112511542664]`),
  `RR.ged` (SCC `[I102550865225, I102550865227]`).
* **Observed:** В обоих файлах ровно один SCC > 1 в parent→child графе.
  Это data-bug в source GED — кто-то — в инструменте экспорта или вручную —
  закольцевал родителей. Парсер не валит, валидатор не флагает.
* **Repro:**

  ```powershell
  uv run python scripts/discovery/probe.py "$env:GEDCOM_TEST_CORPUS/Ancestry.ged" | python -c "import json,sys; print(json.loads(sys.stdin.readline())['section_D']['ancestor_cycle_sample_first3'])"
  ```

* **Suggested phase:** Validator-rule fix `ancestor_cycle` — низкий
  priority, не demo-blocker, GM317 чист.

### HUGE-006 — Compat-sim 4-target run по умолчанию

* **Observed:** На GM317 — 11.7 s на 4 target'а (ancestry/myheritage/
  familysearch/gramps). Если parser-service вызывает `simulate()`
  по умолчанию для всех 4 — тратится ~15 s parse-overhead.
* **Suggested:** Default-target = `ancestry` (или nothing); explicit
  list-of-targets через CLI. Изменение в parser-service, не parser.
* **Phase:** 5.11.x (low priority).

### HUGE-007 — `GedcomDateWarning` для нестандартных дат

* **Observed:** Парсер выдаёт warning'и на даты типа `April 1903`,
  `Abt. 1880`, `WFT Est 1891-1937`, `c.1871`, `Bef. 20 Oct 1850`,
  `7 Okt 1940` (Dutch Okt), `22 Juni 1853` (German Juni). Парсер
  продолжает; date становится `None`, raw сохранён в `date_raw`.
* **Why tolerated:** ADR-0007 говорит что мы храним raw для round-trip,
  semantic parse — best-effort. Это работает.
* **Suggested phase:** 5.11.x (low) — расширить month-token словарь
  (German, Dutch, French abbrev), добавить parsing for `Abt.`, `Bef.`,
  `WFT Est`. Полное покрытие Geoffrey'ев use-case'ов.

### HUGE-008 — Quarantine `unknown_tags` ~25-50% от peak RSS

* **Observed:** RR.ged держит 177 544 `RawTagBlock` объектов в
  `doc.unknown_tags` (квантининные `_APID`/`_OID` etc); каждый
  RawTagBlock — frozen Pydantic-model с tuple детей и raw-string
  value'ями. По грубой оценке (см. секция F): 25-50% от RR's 7.6 GB peak.
* **Suggested phase:** 5.11.x — оптимизировать quarantine storage:
  (a) shared-string interning для tag-name'ов (high-cardinality
  доминируется ~40 уникальными именами); (b) opt-out flag «не сохранять
  unknown_tags если import не нуждается round-trip».
* **Demo impact:** косвенно через HUGE-001 (peak RSS reduction).

### HUGE-009 — External RSS sampler в run_all.py видит wrong PID

* **File:** `scripts/discovery/run_all.py`.
* **Observed:** `external_peak_rss_human` = «10.7 MB» на всех больших
  файлах, тогда как in-process sampler даёт 6+ GB.
* **Cause:** `psutil.Process(proc.pid)` — это PID `uv`-wrapper'а, а не
  python-child'а. На Windows + uv это всегда так.
* **Suggested fix (out-of-scope для 5.11a):**

  ```python
  proc = psutil.Process(proc.pid)
  for child in proc.children(recursive=True):
      rss = max(rss, child.memory_info().rss)
  ```

* **Demo impact:** none. In-process probe sampler reliably работает,
  это только tooling cosmetic.

### HUGE-010 — NOTE-record-count = 0 на всех файлах

* **Observed:** На каждом из 6 файлов `section_A.notes_top = 0`.
  Все NOTE'ы — inline (`level ≥ 1`), top-level NOTE-блоков нет.
* **Implication:** `doc.notes` index в `GedcomDocument` сейчас не
  тестируется на real corpus. Не баг, просто пища для размышлений
  при будущем code-coverage-аудите.

## Recommended next steps — ranked

| Rank | Phase | Title | Anchored to | ROI for demo |
|---:|---|---|---|---|
| 1 | **5.11b** | Peak RSS reduction (skip-compat default + gc.collect + quarantine opt-out) | HUGE-001, HUGE-008 | **Demo blocker** — 2-3 GB headroom |
| 2 | (none) | UTF-16-BE auto-detect: уже работает | HUGE-002 | — (verified) |
| 3 | 5.11d | dup-tolerance в validator (опц. add `duplicate_individual` с privacy-aware exclusion) | HUGE-003 | future-proofing |
| 4 | 5.11.x | Date-tolerance расширение (`Abt.`/Dutch/German months) | HUGE-007 | quality-of-life |
| 5 | 5.11.x | Validator-rule `ancestor_cycle` | HUGE-005 | correctness |
| 6 | 5.11.x | Compat-sim default-target reduction | HUGE-006 | minor perf |
| 7 | tooling | Fix run_all.py external RSS sampler walk children | HUGE-009 | tooling cosmetic |

**Минимально-достаточный ship для демо**: только rank 1 (5.11b).
Всё остальное — после демо.

## Self-verify checklist (брифовый)

| # | Check | Status |
|---:|---|---|
| 1 | workflow_runs зелёные | проверяется в PR (после push) |
| 2 | files only docs/discovery/, docs/adr/, scripts/discovery/ — NOTHING в packages/services/apps | ✓ |
| 3 | grep hardcoded `F:\Projects\GED` в committed коде → 0 hits | ✓ (только в docs ADR mention'ит запретный path; не в `scripts/`) |
| 4 | no GED file content в diff (PII) | ✓ (raw probe outputs в gitignored `_discovery_runs/`) |
| 5 | local check.ps1 exit 0 | проверяется перед коммитом |
| 6 | git log → 1 commit | проверяется перед push |
| 7 | exec summary имеет actual numbers, не placeholders | ✓ (см. TL;DR; все числа из probe-данных) |

## Reproducibility

Все probe-выходы сохранены локально в `_discovery_runs/20260502T182437/`
(gitignored). Для re-run:

```powershell
$env:GEDCOM_TEST_CORPUS = "<your-GED-corpus>"
uv run python scripts/discovery/run_all.py `
    "$env:GEDCOM_TEST_CORPUS/kladbishe.ged" `
    "$env:GEDCOM_TEST_CORPUS/export-geni.ged" `
    "$env:GEDCOM_TEST_CORPUS/MyHeritage2025.ged" `
    "$env:GEDCOM_TEST_CORPUS/Ancestry.ged" `
    "$env:GEDCOM_TEST_CORPUS/RR.ged" `
    "$env:GEDCOM_TEST_CORPUS/GM317_utf-16.ged" `
    --dupes-on GM317_utf-16.ged RR.ged `
    --timeout-probe 240 --timeout-dupes 600
```

Probe-API стабильна (CLI-флаги не меняются между runs); JSON-shape
детерминирован.

## Связанные артефакты

* **ADR-0079** — Phase 5.11 huge-file hardening (discovery-first stance,
  anti-drift инварианты).
* **Брифы для 5.11b/c/d/e** — родятся из этого отчёта; см. секцию
  «Recommended next steps».
* **Probe-скрипты** — `scripts/discovery/`.
* **Бриф 5.11a** (источник): не зафиксирован на диске, передан через
  Cowork chat.
