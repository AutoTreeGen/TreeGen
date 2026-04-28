# Agent brief — Phase 6.1: DNA matching MVP (Shared cM + relationship prediction)

> **Кому:** Агент 4 (Claude Code CLI, bypass on) — продолжение после Phase 6.0.
> **Контекст:** Windows, `D:\Projects\TreeGen` (или твой worktree).
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

Phase 6.0 закрыта (4 PRs): ADR-0012 privacy + scaffold +
23andMe parser + Ancestry parser. Парсеры дают `DnaTest` объекты с
SNP-листом и метаданными.

Phase 6.1 — **первая полезная DNA-функция:**

1. Сравнить два `DnaTest` → найти **shared segments** (непрерывные
   участки совпадающих SNP-ов на одной хромосоме)
2. Перевести segments в **centiMorgans (cM)** через genetic map
3. Подсчитать **total shared cM** + longest segment
4. Predict relationship range (1C, 2C, etc.) по статистическим таблицам
   (Shared cM Project / DNA Painter)

**Почему это важно для AutoTreeGen и почему уникально:**

Существующие сервисы (Ancestry/MyHeritage/FTDNA) делают это в своих
walled gardens, но:

- Не дают cross-platform matching (Ancestry-аккаунт не видит MyHeritage-матчей)
- Не интегрируют с твоим evidence-graph деревом
- Не показывают **почему** prediction именно такой (no provenance)

GEDmatch делает cross-platform, но это community tool без SLA, и
твоя DNA там лежит.

AutoTreeGen с твоей инфраструктурой = **локальное cross-platform
matching с full evidence trail**, привязанное к твоему дереву. Это
действительно редкая комбинация.

**Параллельно работают:**

- Агент 1: `apps/web/` (Phase 4.3 tree viz)
- Агент 2: `services/parser-service/` (Phase 3.3 sources/citations/multimedia)
- Агент 3: `packages/familysearch-client/`

**Твоя территория:**

- `packages/dna-analysis/` — целиком твой
- `docs/adr/0014-*.md` — новый ADR (matching algorithm + cM source)
- `docs/research/` — можешь добавить research notes по genetic maps

---

## Цель Phase 6.1

1. **Genetic map** — скачать (или зашить как fixture в test_data) таблицу
   recombination rates для GRCh37 (~hg19) — стандарт для DTC DNA
2. **Segment matching** — алгоритм поиска shared segments между двумя `DnaTest`
3. **cM conversion** — segments в physical bp → genetic cM через map
4. **Relationship prediction** — total cM + longest → diapason возможных
   родственных связей (на основе Shared cM Project 4.0)
5. **CLI** — `dna-analysis match file1.txt file2.txt` → JSON output

---

## Задачи (в этом порядке)

### Task 1 — docs(adr): ADR-0014 DNA matching algorithm + sources

**Цель:** зафиксировать алгоритм + источники данных до кода.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b docs/adr-0014-dna-matching`
3. Создать `docs/adr/0014-dna-matching-algorithm.md`:
   - Status: Accepted, Date: today, Authors: @autotreegen
   - Tags: dna, matching, statistics, phase-6
   - Контекст: что нужно для cross-platform matching
   - Алгоритм:
     - Half-IBD vs full-IBD (мы делаем half-IBD MVP)
     - Min segment threshold: **7 cM** (industry standard, less than 5cM = noise)
     - Min SNPs per segment: **500** (уменьшает false positives)
     - Mismatch tolerance: **0** (для half-IBD строго; full-IBD относится к Phase 6.2)
   - Источник genetic map:
     - **HapMap GRCh37 / hg19 genetic map** (свободно, public domain)
     - URL: <https://github.com/joepickrell/1000-genomes-genetic-maps>
     - Альтернатива: deCODE map (более точная для northern Europe)
     - Решение: HapMap для MVP, deCODE как future option
   - Relationship prediction:
     - Source: **Shared cM Project 4.0** (Blaine Bettinger / DNA Painter)
     - Hardcoded таблица в коде с CC-BY attribution
     - Возвращаем range relationships, не single guess
   - **Privacy guard:** matches между двумя пользователями требуют
     **обоюдного opt-in** (отдельная consent table). Это документируется
     здесь, реализуется в Phase 6.2 service-level.
   - Когда пересмотреть: появится better genetic map (HapMap последний раз
     обновляли давно), или мы решим перейти на full-IBD
4. `pwsh scripts/check.ps1` зелёное
5. Commit, push, PR
6. Дождаться CI green, мерджить

### Task 2 — feat(dna-analysis): genetic map data + loader

**Цель:** load HapMap genetic map в memory.

**Шаги:**

1. `feat/phase-6.1-genetic-map`
2. **Download HapMap genetic map** (один раз):
   - Source: <https://github.com/joepickrell/1000-genomes-genetic-maps/tree/master/interpolated_OMNI>
   - Или: <https://www.shapeit.fr/files/genetic_map_b37.tar.gz>
   - Положить как `packages/dna-analysis/data/genetic_maps/hapmap_grch37/chr*.txt`
   - **Файлы добавь в `.gitignore` если они большие** (~50MB total).
     Test fixture — урезанная версия (1 chromosome, ~100KB), коммить.
3. Скрипт download: `packages/dna-analysis/scripts/download_genetic_map.py`
   с проверкой sha256, idempotent
4. `packages/dna-analysis/src/dna_analysis/genetic_map.py`:

   ```python
   class GeneticMap:
       """In-memory recombination rate map, GRCh37."""
       def __init__(self, source_dir: Path): ...

       def physical_to_genetic(self, chromosome: int, position: int) -> float:
           """bp → cM, linear interpolation между точками"""
   ```

5. Тесты:
   - test_loader_reads_all_22_autosomes
   - test_physical_to_genetic_at_known_points
   - test_position_outside_map_raises_or_returns_nearest
6. `pwsh scripts/check.ps1` зелёное
7. Commit, push, PR

### Task 3 — feat(dna-analysis): shared segment finder

**Цель:** алгоритм поиска shared segments между двумя `DnaTest`.

**Шаги:**

1. `feat/phase-6.1-shared-segments`
2. `packages/dna-analysis/src/dna_analysis/matching/segments.py`:

   ```python
   @dataclass
   class SharedSegment:
       chromosome: int
       start_bp: int
       end_bp: int
       num_snps: int
       cm_length: float

   def find_shared_segments(
       test_a: DnaTest,
       test_b: DnaTest,
       genetic_map: GeneticMap,
       min_cm: float = 7.0,
       min_snps: int = 500,
   ) -> list[SharedSegment]:
       """Half-IBD shared segments using GERMLINE-style algorithm."""
   ```

3. Алгоритм (упрощённый GERMLINE / iLASH):
   - Sort SNPs обоих тестов по chromosome + position
   - Для каждой хромосомы:
     - Скользящее окно: пока genotypes совпадают (any allele in common
       для half-IBD), расширяем segment
     - При mismatch — закрываем segment, проверяем длину
     - Если ≥ min_snps и ≥ min_cm → добавляем в результат
4. Тесты с синтетическими данными (random seed=42):
   - test_two_identical_tests_yield_full_genome_segments
   - test_two_unrelated_tests_yield_few_short_segments
   - test_simulated_parent_child_yields_22_chromosomes_full_match
   - test_simulated_2nd_cousin_yields_3_to_5_segments
5. **Privacy guard:** в logs пишем только counts (segments_found=N),
   НЕ конкретные SNPs.
6. `pwsh scripts/check.ps1` зелёное
7. Commit, push, PR

### Task 4 — feat(dna-analysis): relationship prediction

**Цель:** total cM → range relationships.

**Шаги:**

1. `feat/phase-6.1-relationship-prediction`
2. `packages/dna-analysis/src/dna_analysis/matching/relationships.py`:

   ```python
   class RelationshipRange(BaseModel):
       label: str           # "2nd-3rd cousin"
       probability: float   # 0..1
       cm_range: tuple[int, int]

   def predict_relationship(
       total_shared_cm: float,
       longest_segment_cm: float,
   ) -> list[RelationshipRange]:
       """Returns ranked list of plausible relationships (Shared cM Project 4.0)."""
   ```

3. Hardcoded таблица из Shared cM Project 4.0 (CC-BY 4.0):
   - 0 cM: unrelated
   - 7-25 cM: 5C-8C+ (very distant, often noise)
   - 25-100 cM: 4C-5C
   - 100-300 cM: 2C-3C
   - 300-1500 cM: 1C / nephew/niece / great-grandparent
   - 1500-2500 cM: parent / sibling
   - 3400+ cM: identical twin / same person
4. Тесты:
   - test_zero_cm_returns_unrelated
   - test_3500_cm_returns_identical_twin_top_match
   - test_800_cm_returns_first_cousin_or_great_aunt_in_top_3
5. **Attribution:** в docstring класса упомянуть Shared cM Project 4.0
   - Blaine Bettinger + CC-BY 4.0 + URL
6. `pwsh scripts/check.ps1` зелёное
7. Commit, push, PR

### Task 5 — feat(dna-analysis): CLI `dna-analysis match`

**Цель:** end-to-end CLI: 2 raw DNA files → JSON match report.

**Шаги:**

1. `feat/phase-6.1-cli-match`
2. В `packages/dna-analysis/pyproject.toml` добавить script entrypoint:

   ```toml
   [project.scripts]
   dna-analysis = "dna_analysis.cli:cli"
   ```

3. `packages/dna-analysis/src/dna_analysis/cli.py`:

   ```python
   @cli.command()
   @click.argument("file_a", type=click.Path(exists=True))
   @click.argument("file_b", type=click.Path(exists=True))
   @click.option("--min-cm", default=7.0)
   @click.option("--min-snps", default=500)
   def match(file_a, file_b, min_cm, min_snps):
       """Compare two DNA tests, output JSON: segments + relationship range."""
   ```

4. Output JSON:

   ```json
   {
     "test_a": {"provider": "23andme", "snp_count": 700000},
     "test_b": {"provider": "ancestry", "snp_count": 680000},
     "shared_segments": [...],
     "total_shared_cm": 875.3,
     "longest_segment_cm": 78.2,
     "relationship_predictions": [
       {"label": "1st cousin", "probability": 0.45, "cm_range": [553, 1225]},
       {"label": "Great-aunt/uncle", "probability": 0.30, ...}
     ],
     "warnings": ["Cross-platform comparison: Ancestry uses different chip"]
   }
   ```

5. End-to-end test:
   - Использует synthetic 23andMe + Ancestry fixtures из Phase 6.0
   - `dna-analysis match fixture_a.txt fixture_b.txt`
   - Парсит JSON output, проверяет нон-empty segments
6. `pwsh scripts/check.ps1` зелёное
7. Commit, push, PR

### Task 6 (опционально) — docs(runbook): DNA matching usage guide

`docs/runbooks/dna-matching-usage.md`:

- Как получить raw DNA file у Ancestry/23andMe/MyHeritage/FTDNA
- Как запустить matching
- Как читать output (что значит "1st cousin probability 0.45")
- Privacy reminder

---

## Что НЕ делать

- ❌ **Storage в БД** — это Phase 6.2 (DNA service)
- ❌ **Web UI** — Phase 6.x когда будет dna-service
- ❌ **DNA visualization (chromosome painter)** — Phase 6.3+
- ❌ **GEDmatch upload integration** — отдельный Phase, ADR нужен
- ❌ **Phasing** (определение какой allele от какого родителя) — Phase 6.4
- ❌ Реальные DNA файлы в коммитах — никогда
- ❌ Трогать `apps/web/`, `packages/familysearch-client/`,
  `services/parser-service/` (другие агенты)
- ❌ `git commit --no-verify`
- ❌ Мердж с красным CI

---

## Сигналы успеха

После 5 PR:

1. ✅ ADR-0014 в `docs/adr/`
2. ✅ HapMap genetic map загружается, конвертит bp → cM
3. ✅ `find_shared_segments(test_a, test_b)` работает на synthetic data
4. ✅ `predict_relationship(875cM)` возвращает ranked list
5. ✅ `dna-analysis match a.txt b.txt` выдаёт корректный JSON
6. ✅ Privacy guards: 0 raw rsids/genotypes в logs (caplog assertion)
7. ✅ Все CI зелёные
8. ✅ ROADMAP §11: 6.1 done

---

## Performance notes

- **Memory:** GeneticMap ~50MB в RAM. Acceptable для desktop tool.
- **Speed:** comparison двух full DNA tests (~700k SNPs each) — не должно
  превышать 30 секунд на современном CPU. Если медленнее — оптимизация
  через NumPy arrays вместо pure Python loops.
- **Accuracy:** half-IBD with 0 mismatches — overestimate close
  relationships, underestimate distant. Phase 6.2 — добавить mismatch
  tolerance + IBD2 detection (full sibs).

---

## Coordination

- Никаких пересечений с Агентами 1/2/3 на уровне файлов
- Корневой `pyproject.toml` / `uv.lock` — если конфликт, rebase + uv lock
- Если найдёшь баг в Phase 6.0 parsers (23andMe/Ancestry) — отдельный
  fix-PR, не намешивай в matching

---

## Next phases (для контекста, не делай)

- **6.2 — DNA service** (FastAPI, store DNA in encrypted column,
  consent table, matches table)
- **6.3 — Web UI for DNA** (Phase 4.x web app + DNA dashboard)
- **6.4 — Phasing + IBD2** (full siblings detection)
- **6.5 — Imputation** (cross-platform SNP overlap improvement)
- **6.6 — GEDmatch integration** (с user-provided kit)

Удачи. Это первая по-настоящему уникальная функция AutoTreeGen.
Жду PR-ссылок и demo run на synthetic data.
