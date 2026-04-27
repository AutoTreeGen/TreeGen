# Phase 3.1 — Events import in parser-service

Stack: builds on top of `feat/phase-3-parser-service` (PR #?).

## Summary

- `import_runner.run_import` теперь раскладывает GEDCOM `BIRT/DEAT/MARR/...`
  у `INDI` и `FAM` записей в таблицы `events` + `event_participants`.
  Bulk-insert тем же chunk=5000 паттерном с `set_audit_skip` (агрегированный
  audit-entry уровня import_job сохраняется как и раньше).
- Маппинг GEDCOM-тегов → `EventType` enum: 24 «известных» тега ложатся как есть,
  всё остальное (`BLES`, `FCOM`, `EVEN`, `OCCU`, `MARB`, …) → `CUSTOM` с
  оригинальным тегом в `custom_type`. Это удовлетворяет CHECK-constraint
  `custom_type_required_for_custom`.
- Парсинговая `ParsedDate` маппится в колонки `date_start/end/qualifier/calendar`.
  `is_period`/`is_range` корректно превращаются в `FROMTO`/`BET`. Календари
  `roman/unknown` → `NULL` (нет соответствия в `DateCalendar`).
- `place_id` остаётся `NULL` — Place-импорт отдельной задачей в Phase 3.2.

## Bug fixes по дороге (необходимы для зелёных тестов Phase 3)

Замечены при первом прогоне `pytest services/parser-service` против
testcontainers-postgres — Phase 3 PR не покрывал интеграционный путь end-to-end:

- `services/parser-service/tests/conftest.py` — миграции в testcontainer-DB
  не доезжали, потому что `infrastructure/alembic/env.py` подгружает `.env`
  и переписывает `sqlalchemy.url` локальным dev-DSN. Теперь conftest
  выставляет `DATABASE_URL` в URL testcontainer'а на время `command.upgrade(...)`
  и восстанавливает старое значение в `finally`.
- `parser_service/database.py` — `register_audit_listeners` вызывался
  по запросу (`session.sync_session.bind.sync_engine`), что (a) роняло
  атрибут-ошибкой в FastAPI-сценарии и (b) приводило бы к N-кратному
  срабатыванию листенеров. Перенесено в `init_engine` с idempotent-флагом.
- `import_runner.run_import` правки несоответствий ORM:
  - `Tree` использует `TreeOwnedMixins` — нет `status`/`confidence_score`,
    убрал лишние kwargs.
  - `ImportJob.created_by_user_id` (а не `owner_user_id`).
  - `ImportJobStatus.RUNNING` (нет элемента `PROCESSING`).
  - Принят опциональный `source_filename` — API передаёт оригинальное имя
    upload'а, чтобы не оседало `tmpXXXX.ged` в `import_jobs.source_filename`
    и `tree.provenance.source_filename`.

## Test plan

- [x] `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest services/parser-service -m "not gedcom_real"` — 9/9 green.
- [x] Новый `test_import_persists_birth_event_for_first_person` загружает
      `_MINIMAL_GED` (теперь содержит `1 BIRT / 2 DATE 1850` у `@I1@`
      и `1 MARR / 2 DATE 1875` у `@F1@`), забирает первую персону через
      `GET /trees/{id}/persons`, дальше `GET /persons/{id}` и проверяет,
      что в `events` ровно один `event_type=BIRT` с `date_raw="1850"`.
- [x] `test_post_import_creates_job_and_persons` дополнительно проверяет
      `stats["events"] == 2` и `stats["event_participants"] == 2`.
- [ ] `pre-commit run --all-files` (TODO до мержа).

## What's NOT included (defer)

- **Place-импорт** — `events.place_id` остаётся `NULL`. Парсер уже даёт
  `Event.place` (структурированный `ParsedPlace`), нужно отдельно
  добавлять `places`/`place_aliases`-вставку и резолвить FK. Phase 3.2.
- **Multi-principal участники** — у `MARR` сейчас один participant
  (сама семья). Husband/wife отдельными `person_id`-participants
  с ролями `husband`/`wife` — Phase 3.2 вместе с Place.
- **Citations / sources** — события несут `sources_xrefs`, но `citations`-
  таблица не заполняется. Phase 3.3.

## Out of scope (по запросу владельца проекта)

- ADR-решения по `arq` / auth (откладываем).
- Никаких прямых merge'ов в `main`, никакого touch'а `main`.
