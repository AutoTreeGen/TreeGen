# Agent brief — Phase 9.0: Parser-service Prometheus metrics

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-metrics`.
> Чистая территория — никто другой не пишет в parser-service в этой
> сессии, кроме Agent 2 (он в `services/bulk_hypothesis_runner.py`,
> ты в новом `services/metrics.py` + main.py wiring).
> Перед стартом: `CLAUDE.md`, `services/parser-service/src/parser_service/main.py`.

---

## Зачем

Phase 4.9 (Agent 6) вот-вот зашипит review UI. Когда юзер начнёт
смотреть очереди — мы быстро упрёмся в вопросы «почему compute_all
такой медленный», «сколько hypothesis было создано за час», «scorer
тормозит на каком rule». Без metrics — слепой режим.

Минимальный observability stack: `/metrics` endpoint в Prometheus
exposition format + 4-5 ключевых counter'ов / гистограмм. Без Grafana,
без alertmanager — это всё follow-ups.

---

## Что НЕ делать

- ❌ Не настраивай Grafana / Alertmanager / Loki — это Phase 9.x.
- ❌ Не добавляй tracing (OpenTelemetry) — Phase 9.2.
- ❌ Не ставь Prometheus сервер локально — пусть юзер сам разберётся
  с scrape config в follow-up runbook.
- ❌ Не клади metrics в каждый сервис — только parser-service. Другие
  сервисы — Phase 9.0.1+.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — Зависимость + endpoint

**Файлы:**

- `services/parser-service/pyproject.toml` — добавить `prometheus-client>=0.20`
- `services/parser-service/src/parser_service/api/metrics.py` (новый):

```python
from fastapi import APIRouter, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = APIRouter()

@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

Wire в `main.py`: `app.include_router(metrics_router)`.

### Task 2 — Custom collectors

**Файл:** `services/parser-service/src/parser_service/services/metrics.py` (новый)

```python
from prometheus_client import Counter, Histogram

# Hypothesis pipeline
hypothesis_created_total = Counter(
    "treegen_hypothesis_created_total",
    "Total hypotheses created",
    labelnames=["rule_id", "tree_id"],
)

hypothesis_review_action_total = Counter(
    "treegen_hypothesis_review_action_total",
    "Hypothesis review actions",
    labelnames=["action"],  # approved/rejected/deferred
)

# Compute jobs
hypothesis_compute_duration_seconds = Histogram(
    "treegen_hypothesis_compute_duration_seconds",
    "Time to run a single hypothesis rule",
    labelnames=["rule_id"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0, 120.0),
)

# Imports
import_completed_total = Counter(
    "treegen_import_completed_total",
    "GEDCOM/FS imports completed",
    labelnames=["source", "outcome"],  # gedcom/fs, success/error
)

# Dedup
dedup_finder_duration_seconds = Histogram(
    "treegen_dedup_finder_duration_seconds",
    "Dedup scorer execution time",
    buckets=(0.01, 0.1, 0.5, 1.0, 5.0, 30.0),
)
```

### Task 3 — Wire collectors в существующий код

Минимальная инвазивность:

1. **hypothesis_runner.py** — после persist каждой Hypothesis:
   `hypothesis_created_total.labels(rule_id=h.rule_id, tree_id=str(h.tree_id)).inc()`
   Обернуть `rule.apply()` в `with hypothesis_compute_duration_seconds.labels(rule_id=...).time():`
2. **import_runner.py** — в конце import:
   `import_completed_total.labels(source="gedcom", outcome="success").inc()` (или error path)
3. **familysearch_importer.py** — analogichno с `source="fs"`
4. **dedup_finder.py** — обернуть scoring loop в Histogram timer.
5. **api/hypotheses.py** review endpoint — после успешного review action:
   `hypothesis_review_action_total.labels(action=...).inc()`

Не трогай файлы которые сейчас активно правят другие агенты:

- Agent 2: `services/bulk_hypothesis_runner.py` — ты НЕ туда лезешь
- Agent 4: `services/parser_service/services/hypothesis_runner.py` (registry registration) — может быть конфликт. **Перед commit** — `git pull --rebase`.
- Agent 6: API endpoints для hypotheses review — может задеть. Same — rebase.

Это hot-area, инвазивных изменений минимум — только `.inc()` / `.time()` обёртки на 1-2 строки.

### Task 4 — Тесты

**Файл:** `services/parser-service/tests/test_metrics.py`

- GET /metrics → 200, content-type `text/plain; version=0.0.4`
- Body содержит `treegen_hypothesis_created_total` (даже если value = 0)
- Smoke: создать hypothesis через test fixture, проверить counter инкремент
- Smoke: import GED через test fixture, проверить `import_completed_total{source="gedcom"}` инкремент

### Task 5 — Финал

1. ROADMAP §9.0 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-9.0-parser-service-metrics`.
4. CI green до merge. Никакого `--no-verify`.
5. PR description: пример `/metrics` output (несколько строк paste).

---

## Сигналы успеха

1. ✅ GET /metrics returns Prometheus exposition format.
2. ✅ Counter'ы инкрементятся (hypothesis create, import complete, review action).
3. ✅ Histogram'ы измеряют (compute duration, dedup duration).
4. ✅ Тесты зелёные.
5. ✅ Не трогается ORM, не конфликтует с активными PR'ами.

---

## Если застрял

- `prometheus-client` имеет global registry — multiprocess модели сложны.
  Для нашего случая (single FastAPI process) — default registry OK.
- Conflict при `git pull --rebase` с Agent 4 / Agent 6 — обычный resolve,
  collector imports независимы.

Удачи. Маленькая, чистая, заметная фаза.
