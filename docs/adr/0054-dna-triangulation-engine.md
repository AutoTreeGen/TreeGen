# ADR-0054: DNA triangulation engine — algorithm + Bayes-prior heuristic

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `dna`, `matching`, `triangulation`, `phase-6`

## Контекст

Phase 6.0–6.3 поставили парсеры всех платформ, pairwise half-IBD matching
(ADR-0014), persisted match-list (ADR-0020) и UI с chromosome painter +
link-to-person (ADR-0033). У пользователя теперь есть таблица DNA matches,
но **сама по себе сумма cM не отвечает на главный вопрос: «через какого
предка я связан с этим человеком?»**

Triangulation — стандартный приём в genealogy DNA, отвечающий именно
на этот вопрос. Если три человека (kit-owner, A, B) делят IBD-сегмент
**в одном и том же месте на одной хромосоме**, и A с B сами matches
между собой, то с очень высокой вероятностью они унаследовали этот
фрагмент от **общего предка** (MRCA). Дальше пользователь / inference-engine
может сопоставить положение сегмента с известными MRCA в дереве и
получить strong evidence для same-person / parent-child гипотез.

Phase 6.4 поставляет **compute-only triangulation engine + endpoint**.
Phase 6.5 добавит persistence (отдельная таблица `dna_match_segments`,
переезд с jsonb-сегментов), IBD2 для разделения parent vs sibling,
imputation для cross-platform false negatives.

Силы давления на решение:

1. **Industry standard.** DNA Painter, GEDmatch и Ancestry все используют
   7 cM как минимальный порог triangulation segment. Опускать ниже —
   неприемлемый шум; поднимать — терять distant cousins (4C+).
2. **Endogamy.** Ashkenazi / Roma / Amish популяции дают много IBD-сегментов
   через несколько независимых линий одновременно. Plain triangulation
   teryaет специфичность: «100 matches триангулируют на сегменте» — это
   не одно MRCA, это endogamy. Полное решение — IBD2 + phasing (Phase 6.5);
   на Phase 6.4 — эвристический penalty.
3. **Privacy.** Алгоритм работает с aggregate-only входом (cM-координаты
   сегментов, match-id'шники, shared-match relations). Никаких rsid /
   genotypes / положений в bp на этом уровне (ADR-0012).
4. **Compute cost vs freshness.** Match-list пользователя меняется редко
   (импорт раз в дни/недели), но triangulation на дереве в десятки matches —
   O(M²·S²) compute. Кэшировать необходимо, но не платить за consistency
   с заведомо-rare update'ами.

## Рассмотренные варианты

### Вариант A — Compute-only + cache (выбран для Phase 6.4)

- Compute-функция в `packages/dna-analysis/triangulation.py` — pure,
  принимает list[Match] с cM-координатами и shared-match relations.
- `GET /trees/{id}/triangulation` в dna-service — DB read только matches +
  shared_matches, compute групп, Redis cache 1ч.
- Никаких миграций / новых таблиц. Использует уже существующий
  `DnaMatch.provenance['segments']` jsonb.

Плюсы:

- ✅ Минимальный scope: один новый модуль + один endpoint + один ADR.
- ✅ Нет risk'а сломать ingestion: compute-on-demand не зависит от
  изменений в Phase 6.3 import pipeline.
- ✅ Cache-by-tree масштабируется лучше cache-by-match (триангуляция —
  глобальное свойство дерева, не локальное на match).

Минусы:

- ❌ Нет persistence для Phase 7.5 evidence-graph. Inference-engine,
  если хочет использовать triangulation как evidence, должен либо
  вызывать endpoint, либо ждать Phase 6.5.
- ❌ Cache invalidation — TTL only (1 час). Свежий import → пользователь
  ждёт час до увидения новых триангуляций. Acceptable для Phase 6.4
  (компромисс простоты).

### Вариант B — Persistent triangulation_groups table

Создать таблицу `triangulation_groups` + worker, который пересчитывает
группы при изменении match-list.

- ✅ Frontend читает persisted данные за один cheap lookup.
- ✅ Inference-engine может ссылаться на triangulation_group_id как
  evidence без re-compute.
- ❌ Требует миграцию + worker + инвалидация на любой change в matches /
  shared_matches. Больше surface area.
- ❌ Phase 6.5 переедет с jsonb-сегментов на отдельную segments-таблицу
  (часть ROADMAP §6.4 future), что может изменить computed groups.
  Persistence сейчас → migration burden позже.

Откладываем до Phase 6.5, когда схема сегментов стабилизируется.

### Вариант C — Frontend-only computation

Frontend сам делает triangulation через JS прямо в UI.

- ✅ Никакого backend'а.
- ❌ ETL fetch всех matches + shared_matches на клиент при каждом
  открытии вью — мегабайты на больших деревьях.
- ❌ Дублирование алгоритма JS↔Python — two-source-of-truth bug magnet.
- ❌ Phase 7.5 inference-engine на backend'е всё равно понадобится своя
  copy → реализуем сначала на backend'е.

## Решение

Принят **Вариант A — compute-only + Redis cache на 1 час.**

### Алгоритм

Pipeline за два шага:

1. **Pairwise triplet generation.** Для каждой пары matches (A, B):
   - B ∈ A.shared_match_ids И A ∈ B.shared_match_ids (mutual relation,
     fail-closed на one-way).
   - Для каждой пары IBD-сегментов на той же autosomal хромосоме:
     overlap_start = max(a.start, b.start), overlap_end = min(a.end, b.end).
     Если `overlap_end - overlap_start ≥ min_overlap_cm` — добавляем
     triplet `(chrom, overlap_start, overlap_end, {A_id, B_id})`.

2. **Connected-component merge.** Per-chromosome union-find:
   - Два triplet'а сливаются, если **И** делят хотя бы одного member,
     **И** их интервалы пересекаются ≥ `min_overlap_cm`.
   - Финальный интервал группы = пересечение интервалов всех
     вошедших triplet'ов (то место, где гарантировано все members
     делят IBD).
   - Если пересечение < `min_overlap_cm` после merge — группа
     отбрасывается (геометрия не сходится — два независимых сегмента
     на одной хромосоме через общего member, не настоящая триангуляция).

Сложность — O(M²·S²) на построении triplets (M matches, S сегментов
на match), O(T²/C) на merge (T triplets, C — число групп). Для типичных
trees с десятками matches — приемлемо. Если станет узким местом —
sweep-line + interval-tree оптимизация (отложено до жалоб от users).

### Параметры

| Параметр | Default | Источник |
|---|---|---|
| `min_overlap_cm` | **7.0** cM | Industry standard, ADR-0014 |
| Хромосомы | autosomal **1–22** | ADR-0014 (X/Y/MT — Phase 6.5+) |
| Cache TTL | **1 час** | Match-list меняется редко, compute дорог |
| Endogamy threshold | **>10 members** | Heuristic (Bettinger blog, Ashkenazi) |

### Confidence policy (`bayes_boost`)

Простая heuristic для Phase 6.4 (полная Bayes-модель — Phase 7.5 / ADR-0023):

| Условие | Multiplier | Rationale |
|---|---|---|
| `len(members) > 10` (override) | **0.5** | Endogamy-флаг: вероятно multi-line IBD, не одна MRCA |
| `len(members) == 2` (одинокий triplet) | **1.2** | Слабый сигнал, минимальный boost |
| `len(members) >= 3` без MRCA | **1.0** | Detected, но без tree prior нет posterior boost |
| `len(members) >= 3` c known MRCA в дереве | **1.5** | Strong evidence: triangulating cluster + tree-resolved relationship |

«Known MRCA» в Phase 6.4 — placeholder; caller подставляет сам, что
знает (или None). Phase 7.5 inference-engine будет резолвить MRCA
автоматически через tree-relationship analysis.

### Permission gate

`require_tree_role(TreeRole.VIEWER)` — любой active member дерева
может смотреть triangulation. Это не privacy leak: matches уже видимы
для тех же ролей через `GET /dna-kits/{id}/matches`. Triangulation —
производное от уже доступной информации.

Pattern зеркалирован из `parser-service.api.sharing` (см.
`services/dna-service/src/dna_service/services/permissions.py`).
Дублирование намеренное: permission-gate — простая pure-функция
с двумя per-service DI-обёртками, общий модуль в `shared_models`
будет уместен только когда появится третий consumer (Phase 6.5+).

### Caching strategy

- Backend: `redis.asyncio.Redis` (lazy import, optional). Если
  `DNA_SERVICE_REDIS_URL` пуст → no-op cache (всегда recompute).
  Интерфейс через FastAPI dep `get_cache` + Protocol `CacheBackend` —
  тесты подменяют на in-memory без поднятия Redis-контейнера.
- Ключ: `dna:triangulation:{tree_id}:{min_overlap_cm:.2f}` —
  namespace отделяет от других consumer'ов dna-service Redis.
- TTL: 1 час. Mismatch с свежим import'ом acceptable —
  пользователь либо ждёт TTL, либо явно re-fetch'ит после import.

### Privacy guards

1. Вход алгоритма — aggregate-only (cM-координаты, match-id, shared-match
   relations). Никаких rsid/genotypes/position-in-bp.
2. Логи — только статистика (count matches, count groups), никаких
   match_id или cM-координат отдельных сегментов в `_LOG`-сообщениях.
   Тест с `caplog` проверяет invariant.
3. Ответ endpoint'а — match.id'шники + cM + chromosome. UI делает
   отдельный fetch на `GET /dna-matches/{id}` для display_name и пр.
4. Soft-deleted matches и matches с deleted kit'ом исключаются из
   compute (privacy + consistency с `GET /dna-matches/{id}`).

## Последствия

**Положительные:**

- Пользователь получает первый triangulation-вью без миграции БД и
  без блокировки на Phase 6.5.
- Endpoint реиспользует existing schema (`DnaMatch.provenance['segments']`
  - `SharedMatch`), нулевой migration risk.
- `bayes_boost` определяет contract для Phase 7.5 — inference-engine
  знает, как trianulation-evidence трансформируется в hypothesis-confidence.
- Pure-функция в `dna-analysis` тестируется без БД; integration tests
  на endpoint'е используют testcontainers как остальной dna-service.

**Отрицательные / стоимость:**

- Дубль `require_tree_role` в dna-service (~30 LOC). Acceptable,
  pattern идентичен parser-service, общий модуль приедет в Phase 6.5+.
- Cache TTL вместо event-based invalidation: после import пользователь
  до часа видит stale triangulation. UI может показать «refreshing…»
  badge с возможностью force-refresh (отдельный feature, отложен).
- Compute O(M²·S²) — для деревьев с >500 matches на одном kit'е
  может быть медленно (несколько секунд). Mitigation: cache на 1 час
  амортизирует first-hit cost. Если жалобы от users → переход на
  sweep-line + interval-tree.

**Риски:**

- **Endogamy false positives.** Heuristic-penalty `>10 members → 0.5x`
  — грубая. Real Ashkenazi-датасет может дать groups с 50+ members,
  где даже 0.5x — слишком mild. Mitigation: Phase 6.5 IBD2 detector,
  до тех пор — UI должен показывать penalty-badge на таких группах.
- **One-way SharedMatch.** Если ETL пишет shared-relation только в
  одну сторону (что было замечено на Ancestry exports), наша
  fail-closed policy теряет triangulation. Будем мониторить через
  count'ы в endpoint logs; если массовый — переключим на permissive
  resolve в loader.
- **Provenance schema variance.** Текущие matches могут не иметь
  `start_cm`/`end_cm` в provenance.segments (только `start_bp`/`end_bp`
  - `cm`). Phase 6.4 endpoint просто пропускает такие сегменты —
  не падает, но и не триангулирует. Backfill через Phase 6.5
  (`dna_match_segments` table) автоматически решит.
- **Cache stampede.** При истечении TTL множественные concurrent
  GET'ы все compute'ят независимо. Phase 6.4 не делает stampede
  protection — acceptable для low-QPS. Если нужно: Redis-based
  single-flight (`SETNX` + retry) — отложено.

**Что нужно сделать в коде (Phase 6.4):**

1. `packages/dna-analysis/src/dna_analysis/triangulation.py` —
   `Match`, `TriangulationSegment`, `TriangulationGroup`,
   `find_triangulation_groups`, `bayes_boost`.
2. Экспорт публичного API в `packages/dna-analysis/__init__.py`.
3. `packages/dna-analysis/tests/test_triangulation.py` — unit-тесты,
   coverage > 80% (включая privacy-инвариант через caplog).
4. `services/dna-service/src/dna_service/services/permissions.py` —
   `require_tree_role`, `check_tree_permission`, `get_user_role_in_tree`
   (зеркало parser-service).
5. `services/dna-service/src/dna_service/services/cache.py` —
   `CacheBackend` Protocol + `get_cache` FastAPI-dep + `_NullCache` стаб.
6. `services/dna-service/src/dna_service/api/triangulation.py` —
   `GET /trees/{tree_id}/triangulation` endpoint.
7. `services/dna-service/src/dna_service/schemas.py` —
   `TriangulationGroupItem`, `TriangulationListResponse`.
8. `services/dna-service/src/dna_service/config.py` —
   `redis_url` setting.
9. `services/dna-service/src/dna_service/main.py` — register router.
10. `services/dna-service/tests/test_triangulation_endpoint.py` —
    integration-тесты (403/200/cache-hit/format/legacy-data).

## Когда пересмотреть

- **Phase 6.5 ingestion** заводит `dna_match_segments` таблицу →
  переезд с jsonb-сегментов; algorithm тот же, loader меняется.
- **Phase 7.5 inference-engine** просит persistence для evidence-graph
  → создаём `triangulation_groups` таблицу + worker (Variant B).
- **Endogamy жалобы** массово в Ashkenazi-deree'ях → Phase 6.5 IBD2.
- **Cache stampede** обнаруживается в prod metrics → Redis-based
  single-flight.
- **Перформанс O(M²·S²) > 5s p95** на типичном дереве → переход на
  sweep-line + interval-tree.
- **Phase 11.0+ general-tree-share** допускает viewer'а другого
  пользователя — пересмотреть, насколько triangulation отдаётся
  read-only sharer'ам (сейчас — отдаётся; OK by ADR-0036 §sharing).

## Ссылки

- Связанные ADR:
  - ADR-0012 (DNA processing privacy & architecture) — privacy-инварианты.
  - ADR-0014 (DNA matching algorithm) — origin of 7 cM threshold.
  - ADR-0020 (dna-service architecture) — endpoint-style и storage layout.
  - ADR-0023 (DNA-aware inference) — где Phase 7.5 подберёт `bayes_boost`
    в полную Bayes-модель.
  - ADR-0033 (DNA matches UI principles) — UI-контракт, на который
    Phase 6.4 надстраивается.
  - ADR-0036 (Sharing & permissions model) — `require_tree_role` semantics.
- ROADMAP §10.4 — статус Phase 6 подфаз.
- Внешние:
  - [DNA Painter — triangulation guide](https://dnapainter.com/help/triangulation)
  - [Bettinger — endogamy multiplier](https://thegeneticgenealogist.com/)
  - [GEDmatch triangulation tool](https://www.gedmatch.com/) (методология,
    не код).
