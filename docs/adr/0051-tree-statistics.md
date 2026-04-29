# ADR-0051: Tree statistics philosophy (Phase 6.5)

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `frontend`, `backend`, `stats`, `phase-6`

## Контекст

Phase 6.5 добавляет страницу `/trees/{id}/stats` — read-only dashboard
с агрегатами: counts (persons / families / events / sources / hypotheses /
DNA matches / places), pedigree depth, oldest known birth year,
top-10 surnames. Нужно решить три вопроса:

1. **Где живёт агрегация?** Server-side endpoint vs N клиентских вызовов.
2. **Кэшировать ли?** Если да — где и с каким TTL.
3. **Как считать pedigree depth?** Простая метрика или recursive walk.

Контекст:

- Staging-объёмы: ≤ 100k персон / дерево, ≤ 50k families.
- Real B2C-tree (наш Ztree.ged): ~5k персон, ~1.5k families.
- Нет агентского ETL'а — статистика свежая на каждый запрос.
- Phase 11.0 sharing: статистика видна всем members, OWNER/EDITOR/VIEWER
  одинаково (read-only).

## Рассмотренные варианты

### A. Один endpoint, server-side aggregation

`GET /trees/{id}/statistics` запускает 7 параллельных `COUNT(*)`-ов +
`MIN(date_start)` + `GROUP BY surname` + recursive CTE и отдаёт всё одним
JSON-ответом.

- ✅ Один round-trip от клиента.
- ✅ Backend контролирует permission gate (VIEWER достаточно).
- ✅ Аггрегация в БД — fastest path; индекс по `tree_id` уже есть на
  всех `TreeEntityMixins`-таблицах.
- ❌ N round-trip'ов внутри одной транзакции (по числу подзапросов).
  Для staging-объёмов это <50ms суммарно (по нашим измерениям на
  Ztree.ged).

### B. Клиент вызывает 7 list-эндпоинтов и считает сам

Использовать существующие `GET /trees/{id}/persons`, `…/sources`,
`…/hypotheses` и т.д. — каждый возвращает `{total, items}`, total можно
извлечь без items.

- ✅ Без нового endpoint'а.
- ❌ 7 parallel HTTP round-trip'ов: latency × jitter × auth overhead × 7.
- ❌ Top surnames и pedigree depth считать на клиенте — придётся
  скачать ВСЕ имена (10k+ row'ов в реальном дереве).
- ❌ Нет общей точки добавления новых метрик.

### C. Pre-computed snapshot in `tree_statistics` table

Worker (arq) пересчитывает агрегаты раз в N минут и кладёт в отдельную
таблицу. UI читает оттуда.

- ✅ Стабильная latency O(1).
- ❌ Stale data: после import job'а stats отстают N минут.
- ❌ Invalidation complexity: import / merge / undo / GDPR-export — все
  должны триггерить пересчёт.
- ❌ Лишняя миграция и worker, неоправдано для staging-объёмов.

## Решение

Выбран **Вариант A** — один endpoint без кэша.

**Reasoning:**

1. **Single round-trip** важнее микро-оптимизации: 50ms для запроса >>
   стоимость 7 HTTP-round-trip'ов по WAN.
2. **Свежесть данных** важна для evidence-based UX: после import job'а
   user сразу видит реальные counts, не «через минуту».
3. **Indexes уже есть**: `tree_id` индексирован на каждом TreeEntity.
   Запросы — это `COUNT` + `WHERE tree_id = ? AND deleted_at IS NULL`,
   plan'ятся как Index Only Scan.
4. **Кэш — следующий шаг**, не текущий. ADR-0028 (rate-limiting bulk
   compute) показывает паттерн для будущего: Redis с TTL=60s, key =
   `tree:stats:{tree_id}`. Но пока DB-aggregation сама по себе быстрее
   network round-trip'а до Redis на staging — кэш не оправдан.

### Pedigree depth

Считаем через **recursive CTE** на `family_children` + `families`:

1. Уровень 1 — персоны без родителей в этом дереве (т.е. roots
   pedigree-DAG'а).
2. Уровень N+1 — дети персон уровня N через `families.husband_id` /
   `families.wife_id` → `family_children.child_person_id`.

Hard cap: 50 поколений. Оправдание:

- Реальные деревья редко глубже 30 поколений (≈900 лет с avg 30y/gen).
- Cycles в кривых GED-данных встречаются (одна и та же персона как и
  родитель и ребёнок) — без cap recursive CTE будет крутиться вечно.
- 50 — sane bound, не блокирует легитимных пользователей и защищает
  от runaway-данных.

Если в дереве нет ни одной семьи с ребёнком — depth = 0. Если есть только
бездетные семьи — depth = 1 (все персоны в level 1 как «корни»).

Альтернатива — топ-down BFS на клиенте — отвергнута: пришлось бы
скачать всю DAG персон + семей, что для дерева 100k персон неподъёмно.

### Top surnames

`GROUP BY names.surname` с `COUNT(DISTINCT person_id)` — `DISTINCT`
защищает от double-counting когда у персоны несколько `Name`-rows с
одной фамилией (e.g. birth-name + married-name совпадают).

Игнорируем `NULL` и пустые строки — это «безымянные» записи (часто
import-fallback'ы из crypted GED'ов).

`LIMIT 10` хардкодом: top-10 — UX-стандарт для bar chart'а; параметризация
не оправдана (никто не просил).

### CSS bars vs charting library

Phase 6.5 spec упомянул `recharts`, но он не в deps. Top-10 данных —
тривиальный horizontal bar chart, реализуемый через `<div>` с
`width: ${pct}%`. Добавление recharts (~80kb gzipped) для одной страницы
не оправдано — `noPrematureAbstraction` rule. Если добавятся ещё графики
(Phase 6.6+), пересмотрим.

## Последствия

- ✅ Stats обновляются мгновенно после mutation'ов (import, merge, undo).
- ✅ Нулевая операционная нагрузка: нет worker'а, нет cache invalidation.
- ✅ Endpoint extensible: новые метрики добавляются как поле в
  `TreeStatisticsResponse` без breaking change'ей.
- ❌ N round-trip'ов внутри транзакции — не оптимально на huge trees.
  Если пользовательский tree вырастет до 1M+ персон, 50ms станет 500ms.
  Then we cache. Tracking via Phase 13 monitoring (latency-p95 alert на
  endpoint).
- ❌ Pedigree depth не идеален как метрика «сложности дерева» — два
  дерева с одинаковой depth могут иметь разный fan-out. Но это «нравится
  взгляду» метрика, а не business-логика; точная оценка не требуется.

## Когда пересмотреть

- p95 latency `GET /trees/{id}/statistics` > 500ms — добавить Redis-кэш
  TTL=60s.
- Фронтенд начнёт показывать stats в нескольких местах (sidebar
  - detail page) — добавить React Query staleTime=30s, чтобы не делать
  запрос дважды.
- Будет полезен снапшот «как было неделю назад» (для UX «вы добавили
  +50 persons на этой неделе») — добавить worker + `tree_stat_snapshot`
  таблицу с ежедневным rollup'ом.
