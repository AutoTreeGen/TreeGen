# ADR-0003: Стратегия версионирования данных

- **Status:** Accepted
- **Date:** 2026-04-25
- **Authors:** @autotreegen
- **Tags:** `data-model`, `audit`, `compliance`, `phase-2`

## Контекст

В дереве каждый факт может быть оспорен, переопределён, объединён с другим
источником или восстановлен после ошибочного удаления. Без формальной стратегии
истории мы не сможем:

1. Объяснить пользователю, **откуда** взялось утверждение и **кто/что** его последний раз менял.
2. Поддержать GDPR Art. 17 (right to deletion) и Art. 15 (right to access).
3. Дать движку гипотез (Phase 8) пересматривать решения при появлении новых доказательств.
4. Откатить ошибочный массовый импорт без потери ручных правок.
5. Пройти аудит при работе с DNA-данными (special category).

Решение влияет на схему БД, шаблон каждого сервиса, UI «история изменений»,
поведение Alembic-миграций, перформанс. Ошибиться сейчас — переписать все
сервисы Phase 3+.

## Рассмотренные варианты

### Вариант A — Bi-temporal таблицы (`valid_from/valid_to` + `recorded_from/recorded_to`)

Полное двухосевое время: «когда факт был верен в реальности» × «когда мы его записали».

- ✅ Точное моделирование исторических данных. «Person Z был жителем Вильно в 1840–1880, а мы это узнали в 2026».
- ✅ Стандарт в финансах, медицине, регуляторных доменах.
- ❌ **Удваивает сложность каждого запроса**. Любой `SELECT` нуждается в фильтре по двум диапазонам.
- ❌ Mature ORM-поддержка слабая. SQLAlchemy 2 не имеет нативного bi-temporal — пишем руками.
- ❌ Сложные миграции схемы (нужно эволюционировать оба измерения).
- ❌ Mental load для соло-разработчика на старте — высокий.

### Вариант B — Event sourcing (события — единственный источник правды, проекции — для чтения)

Все изменения фиксируются как иммутабельные события (`PersonCreated`, `NameAdded`, `EventLinked`),
текущее состояние — проекция.

- ✅ Идеальная аудируемость и time-travel «out of the box».
- ✅ Естественно для движка гипотез (Phase 8): гипотеза = событие с весом.
- ✅ Replay/rebuild проекций при изменении схемы.
- ❌ Требует отдельной инфраструктуры (event store: Postgres + outbox или Kafka).
- ❌ Eventual consistency проекций → лаги в UI, нужна compensation logic.
- ❌ Дисциплина моделирования событий — сложна на старте, легко наделать «PersonUpdated» вместо доменных событий.
- ❌ Отладка и onboarding новых разработчиков в 2–3 раза дороже.

### Вариант C — Audit-log + soft delete + snapshot-восстановление (выбран)

Текущее состояние живёт в обычных таблицах. Каждое изменение — запись в `audit_log`
(jsonb diff: before/after, actor, reason). Удаление — `deleted_at` (soft) с возможностью
полного `hard delete` через GDPR-flow. Восстановление — применением обратного diff
или из периодических снапшотов сущности (хранятся в `versions`).

- ✅ Прагматично. Чтение остаётся обычным `SELECT` без temporal-фильтров.
- ✅ Стандартная SQLAlchemy 2 + event listeners — никаких экзотических библиотек.
- ✅ GDPR: hard delete = удаление строки + анонимизация audit_log (PII-поля заменяются на `<redacted>`, structural поля сохраняются).
- ✅ Простой переход к Bi-temporal в Phase 8 для подмножества сущностей (гипотезы), не трогая остальное.
- ❌ Нет полного time-travel: «как выглядело дерево на 2025-01-01?» — придётся реконструировать пошагово через audit_log.
- ❌ Audit_log растёт быстро (~3–5x от write-volume в jsonb). Партиционирование по `tree_id` + `created_at` + lifecycle-policy на холодные годы.

## Решение

Выбран **Вариант C** — audit-log + soft delete.

Обоснование:

1. **MVP-bias.** Phase 2 должна разблокировать Phase 3 за 1–2 недели. Bi-temporal или event sourcing удвоят/утроят срок.
2. **Эволюция возможна.** Bi-temporal можно «надеть» поверх audit-log в Phase 8 для гипотез — ровно там, где он нужен. Остальные сущности (places, sources, multimedia) от bi-temporal не выигрывают.
3. **Стандартная экосистема.** SQLAlchemy 2 + Alembic + asyncpg покрывают всё без кастомных слоёв.
4. **Соответствует ROADMAP §6.3** (явная рекомендация на старте).

## Реализация

### Поля на каждой доменной записи

Через миксины в `packages/shared-models/src/shared_models/mixins.py`:

```python
class TimestampMixin:        # created_at, updated_at (server_default=now(), onupdate=now())
class SoftDeleteMixin:       # deleted_at (nullable, default None)
class ProvenanceMixin:       # provenance jsonb (default {})
class VersionedMixin:        # version_id BIGINT (incremented on update via SQLA event)
```

Применяются **все четыре** к: `persons`, `names`, `families`, `events`, `places`,
`sources`, `citations`, `notes`, `multimedia_objects`, `trees`, `import_jobs`.

### Таблица `audit_log`

```text
id              uuid PK
tree_id         uuid FK NOT NULL  (партиционирование по tree_id в проде)
entity_type     text  NOT NULL    ('person'|'family'|...)
entity_id       uuid  NOT NULL
action          text  NOT NULL    ('insert'|'update'|'delete'|'restore'|'merge')
actor_user_id   uuid  NULL FK
actor_kind      text  NOT NULL    ('user'|'system'|'import_job'|'inference')
import_job_id   uuid  NULL FK
reason          text  NULL
diff            jsonb NOT NULL    {"before": {...}, "after": {...}, "fields": [...]}
created_at      timestamptz NOT NULL DEFAULT now()

INDEX (tree_id, created_at DESC)
INDEX (entity_type, entity_id, created_at DESC)
```

### Таблица `versions` (снапшоты)

Полные снапшоты сущности на момент времени. Создаются:

- При каждом import_job (фиксируем состояние затронутых сущностей до импорта).
- По расписанию: ежедневный rolling snapshot последних N изменений.
- Вручную через API: «сделать чекпоинт перед merge-операцией».

```text
id              uuid PK
tree_id         uuid FK
entity_type     text
entity_id       uuid
snapshot        jsonb         (полное состояние сущности)
reason          text
created_at      timestamptz
created_by      uuid FK NULL
```

### Запись в audit_log

Через **SQLAlchemy event listeners** на `before_flush` сессии. Для каждого
объекта в `session.new`/`session.dirty`/`session.deleted` строим diff и вставляем
запись `audit_log` в той же транзакции.

Альтернатива (Postgres triggers) отвергнута: труднее тестировать, привязка к
конкретной СУБД, конфликт с soft delete-логикой на app-уровне.

### Soft delete и query filter

`SoftDeleteMixin.deleted_at` + опциональный фильтр в сессии (через `with_loader_criteria`).
По умолчанию запросы показывают только активные строки (без deleted_at). Восстановление: `entity.deleted_at = None` + audit-запись `restore`.

**Hard delete** доступен только через сервисный метод `gdpr_delete(entity_id)`,
который удаляет строку и анонимизирует все связанные `audit_log.diff` PII-поля.

## Последствия

**Положительные:**

- Понятная и тестируемая модель «истории».
- Простая GDPR-реализация (soft delete по умолчанию, hard delete через документированный flow).
- Audit-log готов к использованию в Phase 8 (rationale для гипотез).
- Снижение mental load на разработчика.

**Отрицательные / стоимость:**

- Audit-log объём: ~3–5x write-volume в jsonb. Решение: партиционирование + lifecycle на холодные годы (Phase 13).
- Time-travel не из коробки. Реализация UI «дерево на дату» — отдельная задача в Phase 8.

**Риски:**

- Забыть применить миксины к новой сущности → нет аудита. Митигация: тест проверяет, что все таблицы под `Base.metadata`, кроме whitelist (`audit_log`, `versions`, `users`), имеют все четыре миксина.
- Performance audit_log при массовых импортах. Митигация: bulk-insert audit-записей одним statement, batch-режим в `import_job`.

**Что нужно сделать в коде:**

- `packages/shared-models/src/shared_models/mixins.py` — четыре миксина.
- `packages/shared-models/src/shared_models/audit.py` — event listeners.
- `packages/shared-models/src/shared_models/orm/audit_log.py` — модель.
- `packages/shared-models/src/shared_models/orm/version.py` — модель.
- Первая Alembic-миграция включает таблицы и индексы.
- Тест: каждая доменная модель имеет миксины (рефлексия по `Base.registry`).

## Когда пересмотреть

- Гипотезы (Phase 8) требуют точного bi-temporal для подмножества сущностей — добавляем bi-temporal таблицы рядом, не трогая остальное.
- audit_log растёт быстрее N GB/мес → пересмотр партиционирования и компрессии.
- Регуляторное требование к финансовому-style аудиту (SOX-подобное) — рассмотреть event sourcing для DNA-операций.

## Ссылки

- Связанные ADR: ADR-0002 (структура монорепо), ADR-0009 (TBD: hypothesis evidence-graph).
- ROADMAP §6.3 (стратегия версионирования).
- SQLAlchemy 2 events: <https://docs.sqlalchemy.org/en/20/orm/session_events.html>.
- Martin Fowler — «Bi-Temporal History»: <https://martinfowler.com/articles/bitemporal-history.html>.
- GDPR Art. 5(1)(e), Art. 17, Art. 15.
