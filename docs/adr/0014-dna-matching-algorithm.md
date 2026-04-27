# ADR-0014: DNA matching algorithm + reference data sources

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `dna`, `matching`, `statistics`, `phase-6`

## Контекст

Phase 6.0 закрылся четырьмя PR'ами: ADR-0012 (privacy & architecture),
scaffold пакета `packages/dna-analysis/`, парсеры 23andMe v5 и
AncestryDNA v2. На выходе у нас типизированный `DnaTest` с SNP-листом
и метаданными, но **полезной фичи для пользователя пока нет.**

Phase 6.1 закрывает первую такую фичу — **shared-segment matching и
relationship prediction между двумя `DnaTest` объектами.** Это базовый
кирпич cousin-matching pipeline и одновременно — главный
дифференциатор AutoTreeGen:

- Ancestry / MyHeritage / FTDNA умеют это **внутри своего walled garden**
  (один аккаунт = матчи только внутри платформы), не дают
  cross-platform, не интегрируют с deep evidence-graph.
- GEDmatch — единственный cross-platform tool, но это community
  service без SLA и с неоднозначной правовой историей (см. ADR-0009 +
  обсуждение Golden State Killer / North Carolina law).
- AutoTreeGen на собственной инфраструктуре + zero-knowledge
  encryption (ADR-0012) + evidence-graph = **локальное cross-platform
  matching с full provenance**, привязанное к дереву пользователя.
  Это редкая комбинация на рынке 2026.

Phase 6.1 делает MVP такого matching'а. Phase 6.2+ добавит persistence,
matching между разными пользователями (с обоюдным consent), IBD2,
phasing, imputation.

Силы давления на решение:

1. **Privacy by design (ADR-0012).** Алгоритм работает с raw SNP-данными
   обоих тестов в оперативной памяти; в логах разрешены только
   агрегаты. Cross-user matching (Phase 6.2+) требует обоюдного opt-in,
   фиксируется здесь как контракт.
2. **Standards в области.** Industry threshold для half-IBD сегментов —
   **7 cM** (Ancestry, MyHeritage, FTDNA сходятся). Меньше 5 cM —
   шум, false positives растут квадратично с длиной генома.
3. **Доступность reference data.** Genetic map (recombination rates),
   Shared cM Project статистика — оба должны быть свободно
   используемыми; иначе мы упрёмся в лицензии.
4. **Cross-platform трудности.** 23andMe и Ancestry используют разные
   chip-ы → перекрытие SNP-ов ~50-70%. False negatives для distant
   relatives неизбежны без imputation (Phase 6.5). MVP это принимает
   как known limitation.

## Рассмотренные варианты

### Вариант A — Half-IBD GERMLINE-style (выбран для MVP)

Алгоритм:

- Для каждой autosomal хромосомы (1-22) — sort SNP по позиции у обоих
  тестов, intersect по rsid.
- Скользящее окно по общим SNP: пара genotypes считается «совпадающей»,
  если у тестов есть **хотя бы один общий аллель** (half-IBD: A,C vs
  C,T → совпадает по C).
- Сегмент расширяется пока совпадения; mismatch — закрытие сегмента.
- Сегмент принимается если: ≥ `min_snps` (default **500**) и
  ≥ `min_cm` (default **7.0**).
- Mismatch tolerance: **0** (для half-IBD строго; tolerance — Phase 6.2).

Плюсы:

- ✅ Простая, детерминированная, тестируемая на синтетике.
- ✅ Industry-standard пороги — результаты сравнимы с DNA Painter,
  GEDmatch one-to-one.
- ✅ Half-IBD достаточен для cousin matching и предков; full-IBD (IBD2)
  нужен только для siblings detection — Phase 6.2.
- ✅ Cross-platform работает: алгоритм consume's intersection общих
  rsid'ов, неважно с какого chip'а пришёл каждый.

Минусы:

- ❌ Half-IBD overestimates близкие родства, underestimates дальние —
  без phasing нельзя различить identical-by-descent от
  identical-by-state на коротких сегментах.
- ❌ Mismatch tolerance = 0 теряет real segments при genotyping errors
  (Ancestry имеет ~0.1% error rate; на 700k SNP это 700 ошибок).
  Mitigation: ставим высокий `min_snps` (500) — изолированные ошибки
  не разбивают сегмент целиком.
- ❌ Cross-platform 23andMe ↔ Ancestry даёт ~50-70% SNP overlap → фактический
  threshold выше 7 cM. Документируем как warning в output.

### Вариант B — Full GERMLINE / iLASH с hash-based seeding

Полный алгоритм с k-mer hash-индексом и chunk-level matching. Используется
в академических pipeline (UK Biobank, 23andMe internal).

- ✅ Быстрее на больших датасетах (миллионы тестов).
- ✅ Поддерживает mismatch tolerance из коробки.
- ❌ Сложнее: ~2000 LOC реализации (vs ~200 для GERMLINE-style),
  больше unit-тестов.
- ❌ Overkill для MVP: AutoTreeGen в Phase 6.1 — pairwise matching
  (один пользователь vs один). Hash-индекс окупается на N×N сравнениях.
- ❌ Phase 6.2 service может перейти на iLASH, если matrices
  размером 10⁴⁺ станут реальностью — отложим.

### Вариант C — Использовать готовую библиотеку

Например, [hap-ibd](https://github.com/browning-lab/hap-ibd) (Java) или
[ibd-ends](https://github.com/browning-lab/ibd-ends).

- ✅ Production-grade, валидирован на UK Biobank.
- ❌ Java зависимость в Python-stack — деплой-проблема, Docker layer
  inflation, дополнительная run-time.
- ❌ Чёрный ящик для evidence-graph: мы не сможем объяснить
  пользователю **почему** парсер дал этот матч, если алгоритм не наш.
- ❌ Privacy: данные уходят в внешний процесс — увеличивает blast
  radius, противоречит pure-functions архитектуре ADR-0012.

### Вариант D — Phase 6.1 без алгоритма (только парсеры)

Отложить matching до Phase 6.2 (с persistence) и в Phase 6.1 добавить
только batch-import / валидацию.

- ✅ Меньше кода, меньше риска.
- ❌ Phase 6.0 уже дала парсеры. Phase 6.1 без полезной фичи —
  потерянный momentum. Пользователь хочет матчи, не валидацию.

## Решение

Принят **Вариант A — half-IBD GERMLINE-style.** Простой алгоритм,
детерминированный, тестируемый на synthetic data, объяснимый
пользователю в evidence-graph контексте. Optimизация (Вариант B) —
Phase 6.2+ если станет узким местом.

### Параметры алгоритма

| Параметр | Default | Источник |
|---|---|---|
| `min_cm` | **7.0** cM | Industry standard (Ancestry/MyHeritage/FTDNA) |
| `min_snps` | **500** | Снижает false positives на cross-platform |
| Mismatch tolerance | **0** | Phase 6.1 строго; tolerance — Phase 6.2 |
| Hemizygous (X/Y/MT) | пропускаем | Phase 6.1 — только autosomal 1-22 |
| Reference build | GRCh37 | Соответствует 23andMe v5 + Ancestry v2 |

### Источник genetic map

**HapMap GRCh37 / hg19 genetic map** —
[joepickrell/1000-genomes-genetic-maps](https://github.com/joepickrell/1000-genomes-genetic-maps)
(public domain, derived from HapMap II + Phase 3 data).

- Лицензия: public domain, source data из HapMap (NIH, public funding).
- Покрытие: chromosomes 1-22, ~10M positions с recombination rates.
- Альтернатива: deCODE map (более точная для northern Europe, но
  требует регистрации). Future option, не блокирующая.
- Тестовая фикстура: урезанная карта одной хромосомы (~100 KB,
  коммитится в репо). Полная карта (~50 MB) — `.gitignore`,
  скачивается через `scripts/download_genetic_map.py` с sha256-проверкой.

`physical_to_genetic(chromosome, position) -> cM` — линейная
интерполяция между соседними точками карты. Position до первой / после
последней точки → возвращаем boundary value (clamped extrapolation —
recombination rate уходит в 0 на концах хромосом).

### Источник relationship table

**Shared cM Project 4.0** (Blaine Bettinger, March 2020) —
[DNA Painter](https://dnapainter.com/tools/sharedcmv4) /
[Bettinger blog](https://thegeneticgenealogist.com/).

- Лицензия: **CC-BY 4.0** (свободное использование с attribution).
- Source data: ~60 000 known relationship pairs, statistics for cM ranges.
- Hardcoded таблица в `relationships.py` с CC-BY attribution в docstring
  и в JSON output (`"source": "Shared cM Project 4.0 (Bettinger, CC-BY)"`).
- Возвращаем **ranked list** возможных родств, не single guess —
  пользователь видит, что 875 cM может быть 1C *или* great-aunt.
- Probability — нормализованная плотность в cM-диапазоне для каждого
  relationship label, **не** posterior probability (для строгой Bayes-оценки
  нужен prior — Phase 6.4 с генеалогическим контекстом дерева).

### Privacy guards (фиксируется здесь, реализуется в коде)

1. **Pairwise matching внутри одной сессии — ok.** Это локальная
   обработка двух `DnaTest` объектов, которые пользователь сам сравнил.
2. **Cross-user matching между разными пользователями AutoTreeGen — Phase 6.2,
   требует обоюдного opt-in.** Без согласия обоих не показывать другому
   пользователю матча, даже факта его существования. Реализация —
   `dna_consent` table + service-level check, ADR-0015 в Phase 6.2.
3. **В логах — только агрегаты.** Те же правила что Phase 6.0:
   `_LOG.debug("found %d shared segments, total %.1f cM", n, total)`.
   Никаких rsid, никаких genotypes, никаких позиций. Тесты с `caplog`
   проверяют отсутствие raw values в логах.
4. **JSON output — без raw genotypes.** Сегменты в output содержат
   `chromosome`, `start_bp`, `end_bp`, `num_snps`, `cm_length` —
   достаточно для chromosome painter, не утечка SNP-калов.
5. **CLI default не пишет файлов.** `dna-analysis match a.txt b.txt`
   выводит JSON в stdout. Если пользователь делает `> match.json`,
   это его осознанный выбор.

## Последствия

**Положительные:**

- Phase 6.1 поставляет первую полезную фичу: cousin-matching на
  любых двух DTC raw файлах, с relationship prediction.
- Cross-platform работает «из коробки» (intersection по rsid),
  пользователь Ancestry + 23andMe получает матчи без GEDmatch.
- Algorithm transparent → evidence-graph в Phase 6.4 может объяснить
  пользователю «почему мы думаем, что это 1C» (вот сегменты,
  вот их cM, вот таблица).
- Half-IBD без phasing достаточен для MVP; phasing (Phase 6.4)
  улучшит точность distance estimation.

**Отрицательные / стоимость:**

- ~50 МБ HapMap data в `data/genetic_maps/` (gitignored, скачивается).
  Test fixture (~100 KB) коммитится — нужен для CI.
- Half-IBD без mismatch tolerance — теряем real segments при genotyping
  errors на коротких участках. Mitigation: высокий `min_snps`.
- Cross-platform overlap (~50-70%) — distant cousins (5C+) могут
  не найтись. Документируем в output как warning, реальное решение —
  Phase 6.5 imputation.

**Риски:**

- **HapMap map устаревает.** Последний major update — 2010-е. Для
  Восточной Европы / еврейских популяций (наша целевая ниша)
  recombination rate может отличаться от HapMap-baseline. Mitigation:
  deCODE map как future option, документируем как known limitation.
- **Shared cM Project смещён к European-ancestry sample.** Для
  endogamous популяций (Ashkenazi, Roma, Amish) total cM завышен в 1.5-2x.
  Mitigation: Phase 6.2+ — endogamy adjustment factor (ROADMAP §10.2.4).
  В Phase 6.1 — warning в output, если total cM > 200 cM в сочетании
  с большим количеством коротких сегментов.
- **Cross-platform false negatives.** 23andMe ↔ Ancestry дают сильно
  меньше overlap чем same-platform. Mitigation: clear warning в JSON
  output (`"warnings": ["Cross-platform comparison: ... ~60% SNP overlap"]`),
  Phase 6.5 imputation.
- **Performance.** Pairwise matching двух full тестов (~700k SNPs) —
  целевой бюджет 30 секунд на современном CPU. Если pure Python loops
  будут медленнее → optimизация через NumPy structured arrays. Не
  оптимизируем upfront.

**Что нужно сделать в коде (Phase 6.1):**

1. `packages/dna-analysis/src/dna_analysis/genetic_map.py` —
   `GeneticMap.physical_to_genetic(chromosome, position) -> cM`,
   loader из HapMap-формата.
2. `packages/dna-analysis/data/genetic_maps/hapmap_grch37/` —
   gitignored полная карта; test fixture в `tests/fixtures/genetic_map/`.
3. `packages/dna-analysis/scripts/download_genetic_map.py` —
   idempotent скачивание + sha256 verify.
4. `packages/dna-analysis/src/dna_analysis/matching/segments.py` —
   `find_shared_segments(test_a, test_b, genetic_map, min_cm, min_snps)`.
5. `packages/dna-analysis/src/dna_analysis/matching/relationships.py` —
   hardcoded Shared cM Project 4.0 table + `predict_relationship()`.
6. `packages/dna-analysis/src/dna_analysis/cli.py` — Click-based CLI:
   `dna-analysis match file_a file_b [--min-cm 7.0] [--min-snps 500]`,
   JSON в stdout.
7. `packages/dna-analysis/pyproject.toml` — добавить `click>=8.1` и
   `[project.scripts] dna-analysis = "dna_analysis.cli:cli"`.
8. Privacy-тесты: `caplog` не содержит raw values после end-to-end
   matching на synthetic парах.

## Когда пересмотреть

- **HapMap получает major update** (или переход на pangenome reference) →
  пересобрать data/, обновить sha256, regression-тесты на known points.
- **deCODE map становится свободно доступна** или
  **eastern European-specific map** появляется → second genetic map
  как опция, default-value pluggable.
- **Cross-platform overlap < 40%** в реальных данных пользователей →
  переход на Phase 6.5 imputation быстрее запланированного.
- **Endogamy false positives** — массовые жалобы пользователей на
  завышенные cM в еврейских семьях → Phase 6.2 endogamy adjustment.
- **Speed > 60s на full pair** → переход на NumPy structured arrays
  и/или Cython hot path.
- **Появляется Bayes-prior из tree context** (Phase 6.4) →
  `predict_relationship()` принимает optional prior, ranking
  переключается на posterior probability.
- **GERMLINE / iLASH** становится обязательным (massive cross-user
  matching matrices в Phase 6.2 service) → переход на hash-based
  алгоритм.

## Ссылки

- Связанные ADR:
  - ADR-0012 (DNA processing privacy & architecture) — фиксирует
    privacy guards, на которых строится этот ADR.
  - ADR-0009 (genealogy integration strategy) — DNA gap, GEDmatch
    как Phase 6.3 fallback.
  - Будущий ADR-0015 (Phase 6.2) — cross-user matching consent table.
- CLAUDE.md §3.5 (Privacy by design), §3.7 (Domain-aware — endogamous
  populations).
- ROADMAP §10 (Phase 6 — DNA Analysis Service), §10.2 (Алгоритмы).
- Внешние:
  - [HapMap GRCh37 genetic map (Pickrell)](https://github.com/joepickrell/1000-genomes-genetic-maps)
  - [Shared cM Project 4.0 — DNA Painter](https://dnapainter.com/tools/sharedcmv4)
  - [Shared cM Project 4.0 — Bettinger blog post](https://thegeneticgenealogist.com/)
  - [GERMLINE algorithm paper (Gusev et al. 2009)](https://genome.cshlp.org/content/19/2/318)
  - [iLASH algorithm (Shemirani et al. 2021)](https://genome.cshlp.org/content/31/2/263)
  - [hap-ibd reference implementation](https://github.com/browning-lab/hap-ibd)
