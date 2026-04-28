# ADR-0022: Person merge strategy (Phase 4.6)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `merge`, `dedup`, `persistence`, `phase-4`, `audit`

## Контекст

Phase 4.5 отдала read-only UI для duplicate-suggestions: пары
кандидатов с confidence-score, side-by-side evidence, `disabled`-кнопки
«Mark as same / Not duplicate / Skip» с подписью «Coming in 4.6».

Phase 4.6 включает кнопки. Ключевой инвариант проекта (CLAUDE.md §5):

> ❌ Автоматический merge персон с близким родством без manual review.

Это требование становится **кодом**, а не только конвенцией: merge —
двухшаговый flow `preview → commit` с обязательным explicit `confirm:true`
в payload. Никакой эвристики «high-confidence → auto-merge».

Phase 7.2 добавила Hypothesis ORM (PR #68 / ADR-0021) и явно
зафиксировала, что `reviewed_status='confirmed'` **не вызывает** auto-
mutation доменных entities — отдельный flow, отдельная audit-запись.
Этот ADR описывает тот самый «отдельный flow».

ORM-фундамент уже на месте:

- `persons.merged_into_person_id` (UUID, nullable, FK→persons, ON DELETE
  SET NULL) — поле существует с initial schema (Phase 0).
- `audit_log` — общий механизм для tracked-изменений.
- `hypotheses` / `hypothesis_evidences` — для проверки конфликтов.

Нужно решить **что merge делает с данными**, **как откатывается**, и
**когда блокируется**.

## Рассмотренные варианты

### Вариант A — Hard delete merged person

Удаляем merged-row физически; survivor поглощает все relationships.

- ✅ Чисто: один person — одна строка в `persons`.
- ❌ Неоткатно. Если merge ошибочный — данные потеряны.
- ❌ Audit-trail только в логах: невозможно показать «View merge history»
  в UI без полной копии.
- ❌ Несовместимо с `merged_into_person_id`-полем, которое уже в схеме.

### Вариант B — Soft-delete + redirect (рекомендуется)

Merged person остаётся в БД с `deleted_at=now()` и
`merged_into_person_id=survivor.id`. Все cross-references (events,
families, hypotheses) переводятся на survivor. Через UI «View merge
history» можно посмотреть исходные значения. После 90 дней undo-окно
закрывается, merged person hard-delete'ится фоном.

- ✅ Reversible до 90 дней.
- ✅ UI показывает «merged from N persons» — пользователь видит, какие
  альтернативные имена/события пришли откуда.
- ✅ `merged_into_person_id` уже в схеме — ноль миграций для базового
  поля.
- ❌ Дубликаты события могут схлопнуться по `(date, place, type)` — это
  отдельная политика, см. ниже.
- ❌ Запросы `WHERE deleted_at IS NULL AND merged_into_person_id IS NULL`
  везде — это уже стандарт проекта, ничего нового.

### Вариант C — Tombstone + replace

Merged-row остаётся как «tombstone» с минимальным набором полей и FK
на survivor; данные переезжают в `audit_log`.

- ✅ БД-таблица `persons` остаётся «чистой» (нет soft-deleted строк
  со всеми полями).
- ❌ Сложнее: ещё один «status», свои индексы.
- ❌ Затрудняет undo: нужно reconstruct из audit_log, что хрупко.

## Решение

Выбран **Вариант B — soft-delete + redirect**, с явным `audit_log`,
персистентным `person_merge_log` и **двухшаговым** API
(`preview` → `confirm`).

### Survivor selection

UI обязательно показывает toggle «Keep left / Keep right». Дефолт — тот
из двух, у которого:

1. Больше элементов в `provenance.source_files` (more evidence).
2. При равенстве — больший `confidence_score`.
3. При равенстве — раньше создан (`MIN(created_at)`), потому что более
   старые записи чаще канонические.

Дефолт виден в preview-ответе как `default_survivor_id`. Если user не
переключил toggle, передаёт его как-есть.

### Field-level merge policy

Для каждого скалярного поля Person:

| Поле | Политика |
|---|---|
| `gedcom_xref` | survivor's, merged's сохраняется в `provenance.merged_xrefs[]` |
| `sex` | survivor's; если различаются — блокировать merge до approval (см. конфликты) |
| `confidence_score` | `max(a, b)` — merge поднимает уверенность |
| `merged_into_person_id` | у merged'а ставится `survivor.id` |
| `provenance` (JSONB) | объединение: source_files = union, manual_edits = concat, добавляется `merged_from: [merged.id]` |
| `version_id` | survivor: `+1`. Merged заморожен. |
| `deleted_at` | merged: `now()`. Survivor: `NULL`. |

Имена (`names`):

- Все `Name` с `merged.id` переподключаются на `survivor.id`.
- `sort_order` ре-нумеруется: имена survivor'а первыми (по их прежнему
  sort_order), потом merged'ные (со смещением `+1000`, чтобы не
  пересекаться). Это сохраняет «primary name» survivor'а и делает
  merged-имена легко отличимыми в UI.
- Точные дубликаты (`given_name + surname` совпадают) **не схлопываются**
  — лучше показать «дубликат имени из merged» в карточке, чтобы
  пользователь видел эффект merge'а. Если хочет — удалит руками.

### Events / Participants

`event_participants.person_id == merged.id` → переключаются на
`survivor.id`. Дубликаты event'ов схлопываются по ключу
`(event_type, date_start, place_id, custom_type)`:

- Если у survivor'а и merged'а есть BIRT с одинаковыми
  `(date_start, place_id)` — оставляем survivor's BIRT, удаляем merged's
  Event (CASCADE удалит merged's `event_participants`-row для этого
  события).
- `provenance` survived-event'а получает `merged_from: [merged_event.id]`.
- При различии `(date_start, place_id)` — оба event'а оставляем
  (survivor получает оба). Пользователь решит руками — это редкий
  случай для duplicate-кандидатов.

Family-membership (`families.husband_id`, `families.wife_id`,
`family_children.child_person_id`) — простой UPDATE на `survivor.id`.

### Hypothesis conflicts (блокирующие)

Merge **блокируется** перед commit'ом, если выполнено хоть одно из:

1. **Прямой конфликт same_person:** существует `Hypothesis` с
   `(hypothesis_type='same_person', subject_a_id ∈ {a, b}, subject_b_id ∈ {a, b}, reviewed_status='rejected')` —
   user раньше явно сказал «это не дубликат», merge нарушает решение.
2. **Cross-relationship конфликт:** существуют две гипотезы про одного
   из участников merge'а с противоречивыми утверждениями (e.g.
   `parent_child(X, Y, confirmed)` и `parent_child(X, Y, rejected)`),
   и merge превратит X в survivor, у которого появятся обе.
3. **Subject уже merged:** `merged_into_person_id IS NOT NULL` у
   `a` или `b` — попытка merge уже-удалённой персоны. 409 Conflict.

При срабатывании блока — preview / commit отдают 409 с
`detail={"reason": "...", "blocking_hypotheses": [hyp_ids]}`. UI
показывает «Resolve conflicting hypotheses first» с deep-link на
hypothesis review page (когда появится). До тех пор — просто текст.

> **Degrade gracefully:** если в момент merge Hypothesis ORM пуст
> (нет ни одной гипотезы про эту пару), все три проверки тривиально
> проходят. Это поведение — `OK by default`, не «skip silently»:
> мы возвращаем `hypothesis_check: "no_hypotheses_found"` в preview-
> response, чтобы UI явно показал «No related hypotheses to check».

### Persistence (`person_merge_log`)

Новая таблица. **Не в `audit_log`**, потому что merge — крупное
событие с собственной семантикой (undo-окно, retention), и хочется
явный индекс.

```python
class PersonMergeLog(IdMixin, TimestampMixin, Base):
    __tablename__ = "person_merge_logs"

    tree_id: Mapped[uuid.UUID]                # FK trees, NOT NULL, indexed
    survivor_id: Mapped[uuid.UUID]            # FK persons, NOT NULL, indexed
    merged_id: Mapped[uuid.UUID]              # FK persons, NOT NULL, indexed
    merged_at: Mapped[datetime]               # = created_at, отдельный индекс для retention
    merged_by_user_id: Mapped[UUID | None]    # FK users, ON DELETE SET NULL
    confirm_token: Mapped[str]                # client-supplied idempotency key (UUID)
    dry_run_diff_json: Mapped[dict]           # JSONB полный snapshot
    undone_at: Mapped[datetime | None]
    undone_by_user_id: Mapped[UUID | None]
```

Поля:

- `confirm_token` — клиент шлёт UUID в payload, повторный POST с тем же
  токеном — 200 идемпотентно (возвращает существующий merge_log row).
- `dry_run_diff_json` хранит ровно то, что вернул preview: список
  изменений по полям, событиям, гипотезам. Используется для отображения
  «View merge history» и для undo.
- `undone_at` — когда был откат. После hard-delete merged'а через 90
  дней этот лог остаётся (для аудита), но `undone_at IS NULL` уже не
  имеет смысла.

Уникальный индекс:
`(tree_id, survivor_id, merged_id, confirm_token) WHERE undone_at IS NULL` —
не даёт случайно сделать два активных merge'а одной пары.

### Undo

`POST /persons/merge/{merge_id}/undo`:

1. Проверяет `merged_at + 90 days >= now()`. Если позже — 410 Gone
   с `detail={"reason": "undo_window_expired", "merged_at": ...}`.
2. Проверяет, что `merged.id` ещё есть в `persons` (не было hard
   delete). Если нет — 410 Gone (`reason: "merged_person_purged"`).
3. Применяет `dry_run_diff_json` в обратную сторону одной транзакцией:
   - `persons.merged_into_person_id = NULL`, `deleted_at = NULL`,
     `version_id += 1`.
   - Имена с offset `+1000` возвращаются на исходный sort_order
     (записан в diff).
   - Events, которые collapsed в survivor'е, восстанавливаются как
     отдельные у merged'а (если были uniquely свои); те, которые
     просто переподключились — переключаются обратно.
   - Family-membership возвращается.
   - Hypotheses — мы их **не трогали** при merge (только проверяли),
     поэтому undo на них не влияет.
4. `person_merge_logs.undone_at = now()`.

`merge_id` в payload commit'а не нужен — генерируется сервером и
возвращается в response.

### Retention (90 дней)

После 90 дней:

- merged person'а можно hard-delete'нуть (фон-job, отдельная фаза). До
  этого undo работает.
- `person_merge_logs` — **остаётся навсегда** (audit). С `undone_at`
  если был откат, или с пометкой `purged_at` если merged person hard-
  delete'нут (отдельный nullable timestamp на log row, добавим в
  миграцию заранее).

Job для hard-delete'а — out of scope этого ADR; зарезервируем хук
`apps_web` пока показывает «Undo expired» вместо удалённой персоны.

## Последствия

**Положительные:**

- Жёсткий manual-review invariant (CLAUDE.md §5) теперь enforced на
  уровне API: без `confirm:true` — 400.
- Reversible flow: пользователь может ошибиться и откатиться 90 дней.
- `dry_run_diff_json` — отдельный source-of-truth для UI history view,
  не нужно реконструировать состояние.

**Отрицательные / стоимость:**

- Soft-deleted persons остаются в БД до 90 дней — рост таблицы.
  Mitigation: тривиально-маленькое число (≤сотни merge'ей в день
  на больших деревьях).
- Логика collapsing event'ов нетривиальна. Mitigation: scope первой
  итерации — по `(event_type, date_start, place_id)`, остальное
  оставляем «и оба события прицеплены к survivor'у». Тонкая настройка
  — отдельный ADR при росте сложности.
- Hypothesis ORM может быть пустой — поведение «no hypotheses found»
  заявляется в response, не падает.

**Риски:**

- Race condition: два user'а одновременно merge'ят пересекающиеся
  пары. Mitigation: `SELECT ... FOR UPDATE` на обе persons-row внутри
  транзакции; сервер берёт ids в canonical-order чтобы избежать
  deadlock'а. `confirm_token`-уникальность даёт идемпотентность для
  retry.
- Undo через 89 дней при тяжёлом merge'е (десятки event'ов): размер
  diff_json может быть несколько МБ. Mitigation: предельный размер
  `<10 MB` per row — типичный merge помещается в KB.

**Что нужно сделать в коде:**

1. `packages/shared-models/src/shared_models/orm/person_merge_log.py` —
   новая ORM-модель + добавить в `orm/__init__.py`.
2. Alembic миграция `0005_person_merge_logs.py` — таблица + индексы.
3. `services/parser-service/src/parser_service/services/person_merger.py` —
   pure-functions: `compute_diff(a, b, survivor_choice) → MergeDiff`,
   `apply_merge(session, diff, confirm_token) → MergeLog`,
   `undo_merge(session, merge_id) → MergeLog`,
   `check_hypothesis_conflicts(session, a_id, b_id) → list[ConflictInfo]`.
4. `services/parser-service/src/parser_service/api/persons.py` — новый
   router с 4 endpoint'ами:
   - `POST /persons/{id}/merge/preview` (body: `target_id`,
     `survivor_choice="left"|"right"`).
   - `POST /persons/{id}/merge` (body: same + `confirm: true`,
     `confirm_token: uuid`).
   - `POST /persons/merge/{merge_id}/undo`.
   - `GET /persons/{id}/merge-history`.
5. Pydantic schemas: `MergeDiff`, `MergePreviewResponse`,
   `MergeCommitRequest`, `MergeCommitResponse`, `MergeHistoryItem`.
6. Tests: happy path, conflict (same_person rejected), idempotency
   (same `confirm_token` дважды), undo within window, undo > 90 days
   = 410, missing `confirm:true` = 400.
7. Frontend: `apps/web/src/app/persons/[id]/merge/[targetId]/page.tsx` —
   side-by-side с цветной diff-подсветкой, choose survivor toggle,
   confirm dialog с обязательным checkbox, success/undo state.
8. `apps/web/src/app/trees/[id]/duplicates/page.tsx` — кнопки больше
   не disabled, ведут на merge URL.
9. ROADMAP §4.6 → done.

## Когда пересмотреть

- При появлении auth (Phase 4.2/4.x): `merged_by_user_id` станет
  обязательным; политика «кто может merge'ить чужие persons» — отдельный
  ADR.
- Если retention 90 дней окажется недостаточным (legal request на
  3-летнюю реверсию): пересмотреть retention + добавить «archived»
  стадию.
- При scale (≥1k merge'ей в день): `dry_run_diff_json` в основном
  storage может стать тяжёлым; вынесем в отдельный blob-store с FK.
- Если auto-merge для confidence ≥0.99 окажется политически
  допустимым — это **новый ADR-сменщик**, который явно отменяет это
  решение через CLAUDE.md §5 (невозможно без обновления §5 правила).

## Ссылки

- Связанные ADR: ADR-0003 (versioning), ADR-0015 (entity resolution
  suggestions), ADR-0021 (hypothesis persistence), ADR-0001 (stack).
- Brief: `docs/agent-briefs/phase-4-6-merge-persons-ui.md`.
- Phase 4.5 PRs: #63, #69 (read-only review UI).
- CLAUDE.md §5 — корневое правило про manual-merge для close-kin.
