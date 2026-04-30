# ADR-0063: DNA AutoClusters + endogamy detection (Phase 6.7)

- **Status:** Accepted
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `dna-analysis`, `clustering`, `endogamy`, `pile-up`, `phase-6`,
  `leiden`, `networkx`, `ai-labels`

## Контекст

Phase 6.7 — auto-clustering DNA-matches пользователя на community
семейных ветвей: пользователь видит группы своих matches (по mother /
father / unknown branches) с UI-метками и предупреждениями про
endogamy / pile-up. Эта capability уже частично подразумевалась
проектом (см. `dna_match.py` со ссылкой на «AutoCluster algorithm
(Leeds Method + Louvain)» в комментарии к таблице `shared_matches`).

Phase 6.4 (ADR-0054) уже шипит triangulation engine — compute-only
часть, которая на одной хромосоме ищет пересекающиеся IBD-сегменты
для same-MRCA inference. AutoCluster — другой угол: на уровне
match-list'а целиком, по графу co-match relations. Эти два
дополнительные signal'а (triangulation на сегментах, AutoCluster на
графе) → позже комбинируются в Bayes-prior'ы для гипотез (Phase 7.5).

Domain-specific сила обращения к eastern-European Jewish corpora —
**Ashkenazi Jewish (AJ) endogamy**: founder population ~350 для
большинства Ashkenazi (Carmi et al. 2014), что приводит к избыточно
высоким среднеcM ср. между парами matches и к **много-сегментному**
паттерну (короткие сегменты на нескольких независимых линиях).
AutoCluster без endogamy-correction фейлит на AJ-дата'те. Owner'ская
DNA — AJ-pile-up (memory `project_owner_dna_aj_pile_up.md`), это
dogfood для phase 6.7.

Phase 6.7 разделена на **6.7a / 6.7b / 6.7c** (этот ADR — 6.7a):

- **6.7a (этот PR):** data model (3 таблицы) + Leiden clustering с
  NetworkX-greedy fallback'ом + heuristic endogamy detection +
  unit-тесты.
- **6.7b:** pile-up region detection (segment overlap analysis по
  популяциям) + per-population thresholds + tests.
- **6.7c:** AI labels (через ai-layer use_case'ы, opt-in flag) +
  frontend (route /dna/clusters, ClusterList / ClusterDetail /
  EndogamyWarningBadge / PileUpBadge components) + e2e.

Деление выбрано так, чтобы каждый под-PR помещался в CLAUDE.md §6
guideline `<500 строк диффа желательно` (фактически 6.7a ~1000 LOC,
6.7b ~600 LOC, 6.7c ~800 LOC; разумный компромисс между
PR-size и атомарностью).

## Рассмотренные варианты

### A. Louvain clustering (исходное упоминание в комментарии shared_matches)

- ✅ Простой, классический, networkx-родной, не требует C-extensions.
- ❌ **Bad partitioning problem** (Traag, Waltman, Van Eck 2019,
  «From Louvain to Leiden: guaranteeing well-connected communities»):
  Louvain может производить **disconnected communities** в финальной
  partition'е, что нелепо для DNA-clustering — у пользователя один
  «cluster» состоит из двух кусков, никак не связанных рёбрами.
  Особенно плохо на больших или sparse графах.
- ❌ Resolution-stability ниже Leiden'а на одинаковом graph'е.

### B. Leiden clustering (preferred)

- ✅ Гарантирует connected communities (Property 1 в Traag et al. 2019).
- ✅ Resolution-stability лучше, modularity strictly higher на
  стандартных бенчмарках.
- ✅ В Python — `leidenalg + igraph`, обе либы стабильные, активно
  поддерживаются.
- ❌ C-extension зависимость (igraph): **не всегда собирается** на
  Windows + Python 3.13 без pre-built wheels (что в нашей `uv`-среде
  оказалось не-issue по факту resolve'а 2026-05-01, но риск
  на чужих воркстейшнах остаётся).

### C. NetworkX greedy_modularity_communities (acceptable fallback)

- ✅ Pure-Python, никаких C-extensions, всегда устанавливается.
- ✅ NetworkX уже доступен через косвенные зависимости / тесты.
- ❌ Качество partition'ов хуже Leiden'а (greedy vs full optimisation;
  не-deterministic при ties без seed'а).
- ❌ Не reproducibly seeded по API — добавочный risk для тестов
  (компенсируем тем, что synthetic graph'и в тестах достаточно
  «разделяемы» — три disjoint clique'и любой нормальный modularity
  алгоритм найдёт).

## Решение

**Combined Variant B + C:** Leiden — preferred-path; NetworkX-greedy —
acceptable fallback. Алгоритм определяется в runtime'е по
import-success'у:

```python
LEIDEN_AVAILABLE = _detect_leiden_available()  # at import

def run_clustering(...):
    if LEIDEN_AVAILABLE:
        return _run_leiden(...)
    log.warning("leidenalg not installed ... falling back to NetworkX greedy")
    return _run_networkx_greedy(...)
```

`force_algorithm` параметр позволяет тестам форсировать оба пути и
обнаруживает деградацию явно (RuntimeError, если caller просит Leiden,
а environment его не имеет).

`DnaCluster.algorithm` (text column) пишет в БД фактически использованный
алгоритм. UI Phase 6.7c покажет WARNING badge на cluster'ах с
`algorithm='networkx_greedy'` («ваш кластеринг прогнан в degraded-режиме,
поставьте leidenalg для лучшего результата»).

### Per-population endogamy thresholds

Heuristic per Phase 6.7 brief:

- avg pairwise cM в кластере **>= threshold** AND
- минимум 3 рёбер в кластере (стабильность),
- opt: много IBD-сегментов на match — слабый дополнительный сигнал.

Population thresholds (cM avg):

| Population | Threshold | Source |
|---|---|---|
| AJ (Ashkenazi Jewish) | 30.0 | Carmi et al., Nat Commun 2014 — «Sequencing an Ashkenazi reference panel ...» |
| Mennonite (Old Order) | 25.0 | Pennsylvania Mennonite reference panel observations (PMC, 2014) |
| Iberian-Sephardic | 20.0 | «Sephardic Jewish ancestry component in Iberian populations», Nat Genet 2008 |

**Selection rule** — **самый специфичный label**: если средний cM
> 30 → 'AJ'; иначе если > 25 → 'mennonite'; иначе если > 20 →
'iberian_sephardic'; ниже 20 — `population_label = NULL`,
`endogamy_warning = false`.

Эти числа — **эвристика**, не строгая статистика. Reference-panel-based
detection (с принципиально лучшим discrimination'ом) — Phase 6.5+.
Текущая heuristic интенционально консервативна в pair_count gate'е
(чтобы не флагать 2-человечные кластеры по случайно тяжёлой паре) и
**не флагает** binary-only графы (Ancestry-style без числовых
pairwise cM): без cM-значений endogamy просто не определима. Это
documented как degraded mode и отражено в тесте
`test_endogamy_degraded_mode_only_binary_edges`.

### Persistence shape

Service-tables: ни `dna_clusters`, ни `dna_cluster_members`, ни
`dna_pile_up_regions` не несут tree_id / soft-delete / provenance /
version_id (см. `test_schema_invariants.py` allowlist update). Причины:

- `dna_clusters` — immutable history compute output'а; повторный run
  → новая row, старые остаются для аудит-trail и для UI «compare
  runs».
- `dna_cluster_members` — чистая m2m связь с CASCADE на любую сторону.
- `dna_pile_up_regions` — population-aggregate observation,
  regenerируется detector'ом (Phase 6.7b); идентичность определяется
  по (chromosome, start_position, end_position, population_label),
  не имеет tree-привязки (это про **популяцию**, не про дерево).

### AI label fields в schema 6.7a, populated в 6.7c

Колонки `ai_label`, `ai_label_confidence`, `pile_up_score` есть в
`dna_clusters` уже сейчас — пустые (`NULL`) до 6.7c / 6.7b. Это
сознательно: добавить колонки одной миграцией дешевле, чем три
последовательных `ALTER TABLE`. Phase 6.7c только write-path'ом
populates эти fields, без миграционной работы.

### Trade-off: AI labels — opt-in (Phase 6.7c)

В Phase 6.7c генерация AI labels — opt-in flag в API
(`POST /clusters/run` body field `generate_ai_labels: bool`),
**не default-on**. Cost-concern: каждый run генерирует Anthropic API
call per cluster, может быть дорого на больших match-list'ах.
Default-off позволяет ship'ить feature без сюрпризов в счёт.

## Последствия

- **Положительные:**
  - Auto-clustering ready as compute primitive в `dna-analysis` package
    (pure-functions, БД-агностично, переиспользуется тестами без
    docker).
  - Endogamy heuristic shipped с честным degraded-mode behavior'ом —
    не делает вид, что работает на binary-only графах.
  - Schema готова под 6.7b и 6.7c, без миграций в этих фазах.
- **Отрицательные / стоимость:**
  - Heuristic endogamy не differentiates AJ от Sephardic-Jewish или
    Roma — все они «high pairwise cM populations». Reference-panel
    discrimination — Phase 6.5+.
  - NetworkX-greedy fallback менее accurate чем Leiden на больших
    sparse графах; в degraded-mode пользователь увидит больше
    мелких / шумных кластеров.
- **Риски:**
  - Population thresholds ~30/25/20 cM выведены из 2008–2014 literature;
    могут сдвинуться по мере появления reference panels (Phase 6.5+).
    Захардкожены в `POPULATION_THRESHOLDS_CM`; смена → bump'нем PR'ом
    с новым ADR.
  - Без числовых pairwise cM — endogamy недетектируема. Ancestry —
    самая популярная платформа в нашей user-base — даёт только
    binary membership. Workaround: пользователю показывается badge
    «degraded mode — endogamy detection недоступен» (UI Phase 6.7c).
- **Что нужно сделать в коде** (этим PR — 6.7a):
  - Alembic 0029: 3 таблицы + check constraints + индексы.
  - `packages/shared-models/src/shared_models/orm/dna_cluster.py` —
    `DnaCluster`, `DnaClusterMember`.
  - `packages/shared-models/src/shared_models/orm/dna_pile_up_region.py` —
    `DnaPileUpRegion`.
  - `packages/shared-models/src/shared_models/orm/__init__.py` — re-export.
  - `packages/shared-models/tests/test_schema_invariants.py` —
    SERVICE_TABLES allowlist update.
  - `packages/dna-analysis/pyproject.toml` — `leidenalg`, `igraph`,
    `networkx` deps.
  - `packages/dna-analysis/src/dna_analysis/clustering/{__init__,graph,leiden,endogamy}.py`.
  - `packages/dna-analysis/src/dna_analysis/__init__.py` — re-export.
  - `packages/dna-analysis/tests/test_clustering.py` — 22 unit тестов
    (graph, Leiden, NetworkX fallback, endogamy всех band'ов,
    degraded mode).
  - `docs/adr/0063-dna-autoclusters-and-endogamy.md` — этот файл.
  - `ROADMAP.md` — секция 6.7 со split'ом 6.7a/b/c.

## Что отложено

- **Pile-up region detection** (6.7b): `pile_up.py` модуль с
  `segment_overlap_analysis`. Schema готова уже сейчас.
- **AI labels** (6.7c): `ai-layer/use_cases/cluster_label.py` — Claude
  API call с top-N shared surnames / places / cM ranges → 1–3-word
  cluster label + confidence. Opt-in via flag.
- **dna-service endpoint + arq worker** (6.7c): `POST /clusters/run`,
  `GET /clusters`, `GET /clusters/{id}`. Long-running job →
  arq-queue.
- **Frontend route + components** (6.7c): `/dna/clusters` route,
  ClusterList / ClusterDetail / EndogamyWarningBadge / PileUpBadge.
- **Phase 6.5 IBD2 integration:** настоящий phasing-based endogamy
  detector, который различает AJ ↔ Sephardic ↔ Roma по сегмент-паттернам
  (а не только средн cM). Заменит heuristic в этом ADR.

## Когда пересмотреть

- Если AJ-thresholds сдвигаются по мере новых reference panels — bump
  `POPULATION_THRESHOLDS_CM` + новый ADR (deprecate этот в части
  thresholds).
- Если Ancestry начнёт отдавать numeric pairwise cM — degraded-mode
  branch endogamy detection'а становится не-нужен.
- Если `leidenalg` будет deprecated / заменён на новую либу —
  пересмотреть Variant B (тогда либо migration на новый алгоритм,
  либо повышение acceptable-уровня NetworkX-greedy fallback'а).
- Если auto-clustering окажется too-noisy на real owner-data
  (AJ-pile-up corner case) — возможно понадобится Phase 6.7-something
  с pre-filtering pile-up regions перед graph build'ом.

## Ссылки

- Связанные ADR: ADR-0012 (DNA privacy & architecture), ADR-0014
  (cM thresholds), ADR-0020 (DNA service consents), ADR-0054
  (triangulation engine), ADR-0063 (этот файл — Phase 6.7a).
- ROADMAP §10 (Фаза 6) — DNA Analysis Service подфазы.
- Memory `project_owner_dna_aj_pile_up.md` — owner's DNA = AJ-pile-up
  dogfood.
- Traag, Waltman, Van Eck 2019, «From Louvain to Leiden: guaranteeing
  well-connected communities», *Scientific Reports* 9:5233.
- Carmi et al. 2014, «Sequencing an Ashkenazi reference panel supports
  population-targeted personal genomics and illuminates Jewish and
  European origins», *Nature Communications* 5:4835.
- `leidenalg` documentation: <https://leidenalg.readthedocs.io>
- NetworkX `greedy_modularity_communities`:
  <https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.modularity_max.greedy_modularity_communities.html>
