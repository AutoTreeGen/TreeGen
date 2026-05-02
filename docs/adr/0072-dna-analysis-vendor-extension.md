# ADR-0072: dna-analysis vendor coverage extension (Phase 16.1)

- **Status:** Proposed
- **Date:** 2026-05-02
- **Authors:** AutoTreeGen
- **Tags:** `dna`, `parsers`, `phase-16`, `consolidation`

## Контекст

Phase 16.1 в roadmap-dispatch описывался как «Universal Raw DNA Parser» —
foundation для всего Phase 16.x DNA Pro Tools cluster. Первоначальный
brief предлагал создать новый пакет `packages/dna-parser/` с собственной
моделью данных (`Snp{rsid, chromosome, position, allele1, allele2,
build_version}`).

Однако `packages/dna-analysis/` уже содержит:

- Рабочую инфраструктуру парсеров (`parsers/base.py`, `parsers/__init__.py`)
  с ABC `BaseDnaParser` и dispatch по `detect()`.
- Полные парсеры для Ancestry v2 (169 LOC) и 23andMe v5 (171 LOC) —
  реализованы в Phase 6.0 с privacy-логированием по ADR-0012.
- Доменную модель `DnaTest`/`Snp`/`Genotype`/`Chromosome`/`ReferenceBuild`
  (Pydantic, frozen, валидация по enum).
- Stubs для MyHeritage и FTDNA, явно помеченные как «Phase 6.1 TODO».
- Test infrastructure: synthetic-fixture-generators, conftest, privacy guards
  (rs1..rsN — не реальные dbSNP).

Создание `packages/dna-parser/` дублировало бы 340 LOC рабочих парсеров,
ввело бы конкурирующую доменную модель и потребовало бы migration-PR для
deprecation существующих импортов в `dna_analysis.matching`,
`dna_analysis.clustering`, `dna_analysis.cli` и тестах.

## Рассмотренные варианты

### Вариант A — Greenfield `packages/dna-parser/`

- ❌ Дублирует 340 LOC рабочих Ancestry/23andMe парсеров.
- ❌ Конкурирующая модель `Snp{allele1, allele2, build_version}` vs
  существующая `Snp{rsid, chromosome, position, genotype}` с canonical
  Genotype-enum.
- ❌ Migration-PR в Phase 16.2+: переписать импорты в matching/clustering/CLI.
- ❌ Privacy-инфраструктура (sha256-prefix logging, fixture-генераторы)
  должна быть переписана с нуля.
- ✅ Чистый старт без legacy-наследия Phase 6.0.

### Вариант B — Extension в `packages/dna-analysis/parsers/` *(выбран)*

- ✅ Реализация двух stubs (MyHeritage, FTDNA) — самодостаточная задача без
  миграции.
- ✅ Существующая модель и privacy-конвенции автоматически распространяются
  на новых вендоров.
- ✅ Public API стабилен: `from dna_analysis.parsers import MyHeritageParser,
  FamilyTreeDnaParser` — те же символы, что и до Phase 16.1.
- ✅ Demo для Geoffrey kit (2026-05-06) укладывается в окно: только парсеры
  - тесты, не migration-сага.
- ❌ Сохраняется наследование от Phase 6.0 (наименование пакета
  `dna-analysis`, не `dna-parser`).

### Вариант C — Создать `packages/dna-parser/` + adapter в `dna-analysis`

- ❌ Удваивает поверхность: новый пакет + adapter, который потребует
  поддержки в обоих местах.
- ❌ Не решает migration-проблему, только откладывает её.

## Решение

Выбран **Вариант B**. Phase 16.1 определяется как «complete vendor
coverage» (полнота покрытия), а не «greenfield foundation». Конкретный
объём:

1. Реализовать `packages/dna-analysis/src/dna_analysis/parsers/myheritage.py`
   и `family_tree_dna.py` — заменить stubs на полноценные парсеры с тем
   же контрактом (`BaseDnaParser`, `DnaTest`, `Genotype` enum,
   privacy-logging).
2. MyHeritage — quoted CSV с `# MyHeritage DNA raw data` сигнатурой,
   определение build из header (`build 37` / `build 38`), default GRCh37.
3. FTDNA — plain CSV (`RSID,CHROMOSOME,POSITION,RESULT`) без comment-блока,
   detection требует отсутствия MyHeritage-сигнатуры (тот же CSV header
   используется обоими, иначе парсеры пересекутся).
4. Synthetic fixtures (`synthetic_myheritage.csv`, `synthetic_ftdna.csv`)
   с rs1..rs100 (privacy guard уже в `test_fixtures.py`).
5. Полные unit-тесты per vendor: detection, parsing, normalization
   (lex-sorted heterozygotes), no-call handling, X/Y/MT chromosomes,
   privacy guards (raw values не утекают в логи и сообщения исключений).

Public API не меняется. Все символы, импортируемые в Phase 6.0
(`AncestryParser`, `TwentyThreeAndMeParser`, `MyHeritageParser`,
`FamilyTreeDnaParser`, `BaseDnaParser`), остаются на тех же местах.

## Последствия

**Положительные:**

- Vendor coverage 4/4 на момент Phase 16.1 завершения (было 2/4 + 2 stubs).
- Все existing call-sites в `dna_analysis.matching` /
  `dna_analysis.clustering` / `dna_analysis.cli` продолжают работать
  без изменений.
- Privacy-конвенции Phase 6.0 (ADR-0012) автоматически наследуются.

**Отрицательные / стоимость:**

- Имя пакета `dna-analysis` остаётся, что может смутить новых участников,
  ожидающих `dna-parser` по brief'у Phase 16.1. Документация в
  `parsers/__init__.py` явно указывает «Phase 16.1 (vendor coverage
  extension, ADR-0072)», чтобы привязать ADR к коду.

**Риски:**

- FTDNA detection полагается на отсутствие MyHeritage-сигнатуры в
  comment-блоке — если в будущем FTDNA добавит свой comment-блок с
  совпадающей подстрокой, detection потеряет точность. Митигация:
  unit-тест `test_parser_does_not_match_myheritage_header` ловит
  пересечения сигнатур.

**Что нужно сделать в коде:**

- Реализация `myheritage.py` и `family_tree_dna.py` (выполнено в этом PR).
- Synthetic fixtures и определение детерминизма (выполнено).
- Unit-тесты per vendor (выполнено).
- Никаких миграций, никаких изменений `models.py` / `__init__.py` верхнего
  уровня — public API стабилен.

## Что НЕ входит в Phase 16.1 (отдельные PR'ы)

- **HG37→HG38 liftover.** Парсеры определяют build по header'у, но liftover
  как отдельная функция (Phase 16.1-pt2). Pre-2018 23andMe / archival
  Ancestry kit'ы остаются на GRCh37 до отдельной задачи.
- **Parquet storage.** Изначальный brief предлагал хранить распарсенные
  SNP-таблицы как parquet. Эта задача — отдельная (Phase 16.3, services/
  layer); парсеры остаются pure-functions от content к `DnaTest`.
- **GEDmatch.** Платформа-агрегатор, не primary upload target. Пользователи
  загружают на GEDmatch raw-данные с других платформ. Если в будущем
  появится конкретный use-case (специфический формат GEDmatch download),
  это новый ADR.
- **services/dna-service/.** API endpoints для загрузки/хранения DNA-данных —
  Phase 6.1 / Phase 16.3, не часть 16.1.

## Когда пересмотреть

- При появлении новых vendor-форматов (LivingDNA, Nebula, etc.) — каждый
  получает свой парсер по тому же образцу.
- При переходе на parquet/columnar storage — модель `DnaTest`/`Snp` должна
  быть совместима с pyarrow schema; если несовместима, ADR-update.
- Если liftover (Phase 16.1-pt2) потребует расширения `Snp` дополнительными
  полями (например, `original_build`), это будет breaking change для
  публичной модели.

## Ссылки

- Связанные ADR: ADR-0012 (DNA privacy & architecture), ADR-0063
  (DNA autoclusters and endogamy), ADR-0070 (polymorphic merge refs —
  параллельный PR).
- Phase 6.0 baseline: `packages/dna-analysis/src/dna_analysis/parsers/`.
- Phase 16.1 demo target: 2026-05-06, Geoffrey kit (largest GED-corpus
  reference).
