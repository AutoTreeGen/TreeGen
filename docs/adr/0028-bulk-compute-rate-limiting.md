# ADR-0028: Bulk hypothesis-compute — rate limiting and cost shaping

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `backend`, `inference`, `phase-7`, `cost`, `queue`

## Контекст

Phase 7.5 finalize переводит ``POST /trees/{id}/hypotheses/compute-all``
с in-process sync-исполнения на arq-очередь (Phase 3.5 инфра). Job
итерирует все candidate-pairs из ``dedup_finder`` через
``hypothesis_runner.compute_hypothesis``. На реальных деревьях:

- 5k INDI tree → ≈ 30k candidate pairs (Daitch-Mokotoff bucket overlap).
- 50k INDI tree → миллионы пар.
- ``compute_hypothesis`` дёргает inference-engine rules для каждой пары:
  CPU-bound, без LLM на этом этапе. На 50-INDI древе — < 5 c (см.
  ``test_compute_all_50_persons_completes_under_5s``), но на 5k INDI
  это уже минуты, а на 50k — десятки минут.

Без ограничений пользователь может:

1. Кликнуть «Compute all» десять раз — десять параллельных job'ов,
   все драят базу по одной и той же tree.
2. Запустить bulk-compute, открыть второе окно, запустить опять —
   две параллельных копии.
3. На больших деревьях затопить worker и заблокировать импорты
   (общая очередь ``imports``, см. ADR-0026).

Idempotency-окно на стороне сервиса (``_IDEMPOTENCY_WINDOW = 1h``)
уже частично решает (1) и (2): повторный POST в течение часа
возвращает существующий job. Но это не «rate limit» по смыслу — это
«не запускать дублирующий пересчёт». Когда job уже succeeded и юзер
хочет re-compute с обновлёнными rules — он должен подождать час
или явно дёрнуть retry-механизм (которого пока нет).

Этот ADR фиксирует MVP-семантику для Phase 7.5 и будущие пути роста.

## Рассмотренные варианты

### Вариант A — Idempotency-only (Phase 7.5 baseline) — рекомендую

«Один активный bulk-compute job на дерево в окне 1 час» — уже
реализованная семантика. Никакого формального лимита на параллельные
job'ы или per-user budget'а; защищаемся только тем, что
``enqueue_compute_job`` возвращает существующий job если найдёт его в
``QUEUED | RUNNING | SUCCEEDED`` за последний час.

- ✅ Уже реализовано, не требует новой инфраструктуры.
- ✅ Решает 80% реалистичного abuse'а: пользовательский spam-clicking,
  повторный POST из-за UI-bug'а.
- ✅ Совместимо с retry: после часа можно re-compute (например, после
  того как добавили новые rules).
- ✅ FAILED / CANCELLED **не** включаются в idempotency-окно — это
  фича: пользователь может сразу retry'нуть упавший job.
- ❌ Не защищает от user'а с многими деревьями (один и тот же user
  может запустить compute параллельно на 10 разных trees).
- ❌ Не считает absolute cost: бесконечный compute на 50k-INDI tree
  всё ещё может занять worker'а на десятки минут.

### Вариант B — Per-user concurrency cap (e.g. 1 active across all trees)

Глобальный счётчик «активных bulk-compute job'ов на user'а». Второй
POST в любом дереве пока первый не завершился — 429.

- ✅ Защищает от user-level abuse'а через много деревьев.
- ❌ Требует user-id (Phase 9.x — ещё нет full auth).
- ❌ UX-trade-off: пользователь не понимает, почему «у меня же другое
  дерево», без хорошего error-сообщения 429-flow болезненно.
- ❌ Преждевременная оптимизация: phase 7.5 — solo-mode, multi-user
  abuse не realistic.

### Вариант C — Job-level cost cap (max pairs / max wall-clock)

«Если total candidate-pairs > 100k, прервать с 422 и попросить
batch'ить». Защита worker'а от runaway-compute.

- ✅ Защищает worker'а от tree, которое физически слишком большое
  для одного job'а.
- ❌ Без UI для batch'инга (по surname-cluster, по generation-band и
  т.п.) пользователь упрётся в стену без выхода.
- ❌ Лимит — magic number, который нужно tune'ить под железо.

### Вариант D — Token-bucket по tree (e.g. 1 compute per 15 минут)

Жёсткий cooldown: после succeeded повторный compute разрешён только
после N минут.

- ✅ Простая семантика.
- ❌ Дублирует idempotency (Вариант A) худшим способом: запрещает
  retry даже после FAILED.

### Вариант E — Distributed lock (Redis SETNX) на tree_id

Атомарная блокировка одного активного compute per tree через Redis,
независимо от idempotency-окна.

- ✅ Надёжнее DB-row-уровня idempotency (race между двумя API-нодами).
- ❌ Race в реальности отсутствует: ``enqueue_compute_job`` через
  SQLAlchemy с SERIALIZABLE-семантикой uniqueness уже даёт правильный
  ответ для конкурентных POST'ов.
- ❌ Дополнительная инфра без явного выигрыша на текущем масштабе.

## Решение

Выбран **Вариант A — idempotency-only baseline для Phase 7.5**, плюс
дорожная карта на B/C для будущих фаз.

**Контракт MVP:**

1. ``enqueue_compute_job`` идемпотентен в окне 1 час по
   ``(tree_id, status ∈ {queued, running, succeeded})``. Это даёт
   «один активный job на дерево» де-факто.
2. POST повторно в это окно → 202 с тем же ``id``, без нового
   ``pool.enqueue_job`` (worker уже поднимет существующий или уже
   обработал его).
3. После завершения (succeeded / failed / cancelled) и истечения
   окна (либо немедленно для failed/cancelled) — пользователь
   может re-compute.
4. Параллельные POST с разных tree-id одного пользователя — **разрешены**
   (Phase 7.5 — solo, abuse-vector маловероятен).

**Что НЕ делаем в Phase 7.5:**

- Per-user concurrency cap — отложено до Phase 9.x (multi-user auth).
- Job-level cost cap — отложено до Phase 7.6 (если придётся, будем
  вводить chunk'ование по generation-band).
- Distributed Redis-lock — пересмотр когда API горизонтально
  scal'ируется (Phase 13).

**Очередь:** общая ``imports`` с ImportJob'ами. Это сознательно —
разделение очередей преждевременно, оба класса job'ов IO/CPU-mixed
с похожим job_timeout. Если bulk-compute начнёт регулярно вытеснять
импорты — отделим в ``hypothesis-compute`` очередь и подкрутим
``--max-jobs`` worker'у.

## Последствия

**Положительные:**

- Минимальная имплементация для Phase 7.5: используем уже написанный
  ``_IDEMPOTENCY_WINDOW``, не ввязываемся в новые токенс-бакет/локи.
- Failed/cancelled job'ы не включены в idempotency — пользователь
  может retry'нуться немедленно, не ждать час. Это специально:
  retry — частый сценарий после фикса rules или восстановления Redis.

**Отрицательные / стоимость:**

- На очень больших деревьях (>50k INDI) job может занять worker'а на
  десятки минут. Импорты в это время будут в очереди. Mitigation в
  Phase 7.6 (см. триггеры).

**Риски:**

- Idempotency-окно — UX-сюрприз: «я добавил новые rules, кликаю
  compute, а вижу старый job». Поскольку UI отдаёт events_url старого
  job'а, пользователь не зависает; видит финальное состояние и кнопку
  «Dismiss». В UI добавим в Phase 7.6 force-recompute checkbox если
  потребуется (см. триггеры).
- Параллельные computes на разных trees могут утопить worker'а CPU.
  Mitigation: arq ``max_jobs`` (default 10) ограничивает concurrency
  на воркер; horizontal scaling даст больше throughput'а.

**Что нужно сделать в коде (Phase 7.5 finalize):**

- Уже сделано: idempotency-логика в
  ``services/parser-service/src/parser_service/services/bulk_hypothesis_runner.py::enqueue_compute_job``.
- Уже сделано: arq-enqueue вместо sync-execute в
  ``services/parser-service/src/parser_service/api/hypotheses.py::compute_all_hypotheses``.

## Когда пересмотреть

- Bulk-compute job'ы регулярно блокируют импорты (метрика: queue depth
  ``imports`` >> N в течение M минут) — отделить очередь
  ``hypothesis-compute``.
- Worker CPU > 80% устойчиво — добавить per-user concurrency cap (B)
  и/или cost cap (C).
- Появляется multi-user auth (Phase 9.x) — переходим на Вариант B
  как часть auth-flow.
- Кто-то упирается в idempotency-окно («хочу re-compute сразу после
  succeeded») — добавить ``?force=true`` query-param и UI-checkbox.

## Ссылки

- ADR-0021 — Hypothesis persistence (data model для job'а).
- ADR-0026 — arq как очередь фоновых задач (общая инфра).
- ROADMAP §7.5 — Phase 7.5 finalize.
- ``services/parser-service/src/parser_service/services/bulk_hypothesis_runner.py``
  — реализация idempotency.
