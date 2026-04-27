# DNA matching — usage guide

> Phase 6.1+ runbook для пользователей. Архитектурный контекст —
> ADR-0014 (matching algorithm + reference data); privacy-режим —
> ADR-0012 (privacy & architecture).

---

## Что это

`dna-analysis match` сравнивает два сырых DNA-файла (от Ancestry, 23andMe
и других платформ), находит **shared DNA segments**, переводит их в
**centiMorgans (cM)** и предлагает **диапазон возможных родств**
по статистике [Shared cM Project 4.0](https://dnapainter.com/tools/sharedcmv4)
(Bettinger, CC-BY 4.0).

Зачем это нужно отдельно от сервисов:

- **Cross-platform.** Ancestry-аккаунт не видит MyHeritage-матчей; AutoTreeGen
  сравнивает любые два raw-файла независимо от провайдера.
- **Локально, без передачи DNA третьим сторонам.** Phase 6.1 — pure
  function в вашей оперативной памяти, без сетевых вызовов и без
  persistence. См. ADR-0014 §«Privacy guards».
- **Полный evidence trail.** Output JSON содержит каждый сегмент
  (chromosome, start_bp, end_bp, num_snps, cm_length) и rationale для
  предсказанного родства — Phase 6.4+ привяжет это к evidence-graph
  дерева.

---

## Как получить raw DNA-файл

Для всех платформ скачивание raw данных — **бесплатно** для владельца
kit'а (требуется только подтверждение по email). Phase 6.1 поддерживает
два формата; ещё два — в Phase 6.x.

### 23andMe (поддерживается)

1. <https://you.23andme.com> → **Profile → Browse Raw Data → Download**.
2. Подтвердить пароль и аккаунт.
3. Скачать `.zip`, внутри один `.txt` файл (~25 МБ).
4. Распаковать `.zip` — получите файл вида `genome_FirstName_LastName_v5_FullBuilds_*.txt`.

> Поддерживается только формат **v5** (chip Illumina GSA, GRCh37). Старые
> версии v3/v4 пока не парсятся (Phase 6.x).

### AncestryDNA (поддерживается)

1. <https://www.ancestry.com/dna/> → **Settings → Privacy → Download
   Raw DNA Data**.
2. Подтвердить email-confirmation (приходит письмо, ссылка живёт 7 дней).
3. Скачать `.zip` (~10 МБ), внутри один `.txt`.
4. Распаковать — файл вида `AncestryDNA_*.txt`.

> Поддерживается **v2** (GRCh37). Если у вас более старая v1 — поможет
> Phase 6.x.

### MyHeritage (Phase 6.x — пока не поддерживается)

`packages/dna-analysis` имеет stub-парсер; попытка использования вернёт
`UnsupportedFormatError`. Реализация — Phase 6.x. Пока используйте
GEDmatch (после Phase 6.6) или экспорт kit'а в Ancestry/23andMe-совместимый
формат через сторонние конвертеры.

### FamilyTreeDNA (Phase 6.x)

Аналогично — stub. Family Finder (autosomal) поддержим в Phase 6.x.

---

## Как запустить matching

### Установка

CLI ставится автоматически вместе с пакетом `dna-analysis`:

```bash
uv sync --all-packages
uv run dna-analysis --help
```

### Genetic map (нужна один раз)

Для конвертации physical bp → cM нужна HapMap GRCh37 recombination
map (~50 MB, public domain). Скачивается отдельно:

```bash
uv run python packages/dna-analysis/scripts/download_genetic_map.py
```

Файлы кладутся в `packages/dna-analysis/data/genetic_maps/hapmap_grch37/`
и не коммитятся в git (см. ADR-0014). Скрипт идемпотентный — повторные
запуски проверяют sha256 и не качают заново.

### Сравнение двух файлов

```bash
uv run dna-analysis match \
    /path/to/genome_yourname_v5.txt \
    /path/to/AncestryDNA_uncle.txt \
    --genetic-map packages/dna-analysis/data/genetic_maps/hapmap_grch37/
```

Опции:

- `--min-cm 7.0` — минимальная длина сегмента в cM (industry default).
  Снижение даёт больше шума, повышение — теряете distant cousins.
- `--min-snps 500` — минимум SNP'ов в сегменте. Снижение увеличивает
  false positives на cross-platform сравнениях.

Output — JSON в stdout. Перенаправьте в файл, если нужно сохранить:

```bash
uv run dna-analysis match a.txt b.txt --genetic-map ... > match-report.json
```

---

## Как читать output

Пример:

```json
{
  "test_a": {
    "provider": "23andme",
    "version": "v5",
    "reference_build": "GRCh37",
    "snp_count": 712450
  },
  "test_b": {
    "provider": "ancestry",
    "version": "v2",
    "reference_build": "GRCh37",
    "snp_count": 681203
  },
  "shared_segments": [
    {
      "chromosome": 1,
      "start_bp": 12345678,
      "end_bp": 87654321,
      "num_snps": 12480,
      "cm_length": 78.234
    }
  ],
  "total_shared_cm": 875.32,
  "longest_segment_cm": 78.23,
  "relationship_predictions": [
    {
      "label": "1st cousin / Great-grandparent / ...",
      "probability": 0.62,
      "cm_range": [396, 1397],
      "source": "Shared cM Project 4.0 (Bettinger, CC-BY 4.0)"
    },
    {
      "label": "1st cousin once removed / ...",
      "probability": 0.38,
      "cm_range": [102, 979],
      "source": "Shared cM Project 4.0 (Bettinger, CC-BY 4.0)"
    }
  ],
  "warnings": [
    "Cross-platform comparison (23andme vs ancestry): different chips overlap by ~50-70% rsids; distant relatives may be missed (Phase 6.5 imputation)."
  ]
}
```

### `total_shared_cm`

Сумма длин всех найденных сегментов в cM. Это **главное число** для
оценки родства:

| Total cM | Likely родство |
|---|---|
| 0–7 | Unrelated / noise |
| 7–50 | Distant cousin (5C+, often noisy) |
| 50–300 | 2nd / 3rd cousin |
| 300–1500 | 1st cousin / great-aunt-uncle / great-grandparent |
| 1500–2500 | Grandparent / aunt-uncle / half-sibling / niece-nephew |
| 2500–3400 | Parent / child или full sibling |
| 3400+ | Identical twin / same person |

### `relationship_predictions`

Ranked список возможных родств. **Важно понимать что это density, а не
posterior probability:**

- `"probability": 0.62` для "1st cousin" **не** значит «62% шанс что это
  1st cousin». Это **относительная плотность** Shared cM Project
  distribution в данной точке. Для true posterior нужен prior из
  генеалогического дерева — Phase 6.4.
- Если в результате три кандидата — это значит **по cM-данным
  биологически неразличимо**, какой именно. Без phasing (Phase 6.4)
  parent-child от full-sibling не отличить.
- **Используйте verbal labels в человеческом формате:** «вероятно 1C
  или 1C1R» вместо «62% 1C».

### `shared_segments`

Каждый сегмент — `chromosome`, `start_bp`, `end_bp`, `num_snps`,
`cm_length`. Полезно для:

- **Chromosome painter** (Phase 6.3) — визуализация.
- **Triangulation** (Phase 6.2) — сравнение с третьим тестом по тем же
  сегментам.
- **Самопроверка:** длинный single segment = вероятнее close родственник;
  множество коротких = endogamy / distant.

### `warnings`

CLI явно сигналит про known limitations алгоритма:

- **Cross-platform** — 23andMe и Ancestry используют разные chip, общих
  rsid'ов ~50-70%. Distant cousins (5C+) могут потеряться.
- **Reference build mismatch** — позиции на GRCh37 vs GRCh38 не
  выравниваются. Phase 6.1 поддерживает только GRCh37.
- **Endogamy hint** — total > 200 cM с множеством коротких сегментов =
  возможна еврейская / Roma / Amish endogamy. Real cM завышен в 1.5-2x.
  Adjustment factor — Phase 6.2+.

---

## Privacy reminder

- **Phase 6.1 — никакой persistence.** Файлы читаются в RAM, парсятся,
  matching выполняется, JSON выводится в stdout. После выхода CLI данных
  нет нигде.
- **Никогда не коммитьте raw DNA-файлы в git.** `.gitignore` блокирует
  `*.dna`, `*.dna.csv`, `*.dna.zip`, `*_dna_*.csv`,
  `**/dna-data/`, `**/dna_kits/`, `packages/dna-analysis/test_data/real/`,
  `packages/dna-analysis/data/genetic_maps/`. Не пытайтесь обойти.
- **Cross-user matching между разными пользователями AutoTreeGen** —
  Phase 6.2, требует обоюдного opt-in через `dna_consent` table
  (см. ADR-0014, future ADR-0015).
- **Logs** содержат только агрегаты (segment count, total cM). Никаких
  rsid / genotypes / positions. Регрессия проверяется тестами
  (`test_*_does_not_log_raw_values`).
- **JSON output** содержит chromosome / bp / cM / count для сегментов;
  индивидуальные SNP-данные не утекают.

---

## Troubleshooting

**`UnsupportedFormatError: no parser recognised the format of ...`**

- Файл не v5 (23andMe) или v2 (Ancestry)? Phase 6.1 поддерживает только
  эти два.
- Файл сжатый или открыт в Excel и пересохранён? Excel портит TSV
  формат — нужен оригинал из ZIP.
- Файл MyHeritage / FTDNA / Living DNA — Phase 6.x.

**`GeneticMapError: no chr*.txt files found in ...`**

- Не запустили `download_genetic_map.py`?
- Передали неправильный путь в `--genetic-map`? Должен быть **каталог**,
  не файл.

**`shared_segments: []` для близких родственников**

- Cross-platform overlap слишком мал? Попробуйте снизить `--min-snps`
  (но не ниже 200 — растут false positives).
- Reference build несоответствие? Оба файла должны быть GRCh37.

**Скрипт работает > 2 минут на полных тестах (~700k SNP)**

- Pure Python алгоритм — ожидаемо для первой версии. Phase 6.x перейдёт
  на NumPy structured arrays если станет узким местом.

---

## Ссылки

- ADR-0014 — DNA matching algorithm + reference data sources.
- ADR-0012 — DNA processing privacy & architecture.
- ADR-0009 — Genealogy integration strategy (DNA gap).
- CLAUDE.md §3.5 — Privacy by design.
- ROADMAP §10 — Phase 6 DNA Analysis Service.
- [Shared cM Project 4.0](https://dnapainter.com/tools/sharedcmv4) — Blaine Bettinger, CC-BY 4.0.
- [HapMap GRCh37 genetic map](https://github.com/joepickrell/1000-genomes-genetic-maps) — Pickrell, public domain.
- [GDPR Art. 9](https://gdpr-info.eu/art-9-gdpr/) — special categories of personal data.
