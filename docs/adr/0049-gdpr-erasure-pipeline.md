# ADR-0049: GDPR right-of-erasure pipeline (Phase 4.11b)

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `gdpr`, `privacy`, `erasure`, `phase-4.11b`

## Контекст

GDPR Art. 17 (right of erasure / "right to be forgotten") даёт пользователю
право требовать удаления своих персональных данных. Phase 4.10b (#122)

- Phase 4.11a (#132, ADR-0046) приземлили request-side: row в
`user_action_requests` со `status='pending'`, audit `ERASURE_REQUESTED`.
Phase 4.11b — этот PR — добавляет worker, который row'ы реально
обрабатывает.

Силы давления:

- **GDPR конкретика:** "without undue delay" (Art. 17(1)) — но
  Art. 17(3) разрешает retention для legitimate interests (legal
  records, audit trail для shared trees). Erasure ≠ "DROP TABLE".
- **ADR-0003 § Versioning everywhere:** soft-delete + audit log как
  основа доменной модели. Hard-delete всех записей нарушит invariants
  (FK cascade, restore-from-snapshot).
- **ADR-0012 § DNA privacy:** DNA — special category (Art. 9). Хранение
  encrypted blob'ов после "удаления" нарушает purpose limitation —
  даже если ключ потерян, ciphertext всё ещё биометрические данные.
- **Shared trees:** user может owner'ить tree с другими members.
  Удалить tree ⇒ выгнать членов; не удалять ⇒ нарушить эрасуру.
  Нужен явный handoff.
- **Cost & blast radius:** erasure уничтожает данные; ошибка дорогая.
  Idempotent processing + audit trail обязательны.

## Рассмотренные варианты

### A. Hard-delete всего

- ✅ Простое: один cascade DELETE через FK.
- ❌ Ломает audit-trail (audit_log имеет FK на actor_user_id —
  ondelete='SET NULL', но diff jsonb может содержать references).
- ❌ Невозможно восстановить если user отозвал erasure в grace-окне.
- ❌ Нарушает ADR-0003 § soft-delete как первичная семантика.
- ❌ Tree-data shared с другими members исчезает у них тоже.

### B. Soft-delete всего (выбрано для domain entities)

- ✅ Совместимо с ADR-0003. Восстановление возможно (Phase 4.11c
  grace window).
- ✅ Audit-trail преемственный. Provenance pointer `erasure_request_id`
  делает source-of-erasure traceable.
- ✅ Concurrent access safe: запросы с фильтром `WHERE deleted_at IS NULL`
  больше не видят данные сразу после soft-delete commit'а.
- ❌ Не подходит для DNA (см. ниже).
- ❌ Storage-cost не освобождается; retention-purge — Phase 4.11c+.

### C. Hybrid: soft-delete domain + hard-delete DNA (выбрано)

- ✅ Domain entities: soft-delete + audit (ADR-0003 invariants
  preserved).
- ✅ DNA: hard-delete (ADR-0012 §«Right to be forgotten»). Special
  category — нет purpose для retention после revocation. Blob bytes
  физически удаляются (storage path тоже становится недостижимым
  после row-delete).
- ❌ Сложнее объяснять. Митигировано: audit metadata явно перечисляет
  `soft_deleted: {...}` и `hard_deleted_dna: {...}` отдельно.

### D. Order of operations: pre-checks → soft → hard → external

Внутри pipeline'а порядок важен. Выбрано (см. ADR-0049 §«Pipeline»):

1. **Pre-checks first.** Edge-cases (shared tree, pending export, active
   subscription) ловим до любого destructive шага. Failure здесь оставляет
   row в `manual_intervention_required` без потери данных.
2. **Soft-delete tree-domain** before DNA: tree references kit (через
   citations / multimedia metadata) — soft-deleted records остаются с
   pointer'ом, но если порядок обратный, citation на удалённый kit_id
   FK-cascadit'ится unexpected.
3. **Audit ERASURE_COMPLETED** до Clerk-delete: audit пишется в нашу БД
   и должен persist'нуться даже если Clerk-call зависнет.
4. **Clerk delete после audit**, до email: failure здесь не должен
   отменять completion (мы своей-side всё сделали; Clerk row
   реконсилируется admin'ом).
5. **Email last:** идемпотентен по `request_id`, retry-safe.

### E. `manual_intervention_required` как отдельный статус

Альтернатива: использовать `failed` + flag в `request_metadata`. Отвергнута
потому что:

- `failed` означает "code-side error, retry safe (re-enqueue)" — для
  blocked-by-shared-tree это ложь: retry без admin-вмешательства даст
  тот же block.
- UI должен показывать разный CTA: для `failed` — "Try again", для
  `manual_intervention_required` — "Transfer ownership / contact
  support". Distinct status упрощает frontend conditional.
- Migration 0022 расширяет CHECK-constraint — однострочный alembic.

## Решение

Pipeline `services/parser-service/src/parser_service/services/user_erasure_runner.py::run_user_erasure`:

1. `status='processing'` + audit `ERASURE_PROCESSING`.
2. **Edge check A:** trees где user owner и есть другой active member →
   `manual_intervention_required`, `error="ownership transfer required (Phase 4.11c)"`.
3. **Edge check B:** active export request (pending/processing) →
   `manual_intervention_required`, `error="complete export request first"`.
4. **Edge check C** (placeholder): active subscription. Phase 4.11b
   billing-service не deployed — no-op. Hook оставлен в `_check_blockers`.
5. Per-tree `cascade_soft_delete` (`shared_models.cascade_delete`):
   bulk UPDATE `deleted_at=now()`, `provenance ||= {erasure_request_id, ...}`
   для persons / families / events / places / sources / citations / notes /
   multimedia_objects + names (sub-entity по person_id). Tree-row тоже
   `deleted_at=now()`.
6. `hard_delete_dna_for_user`: bulk DELETE для kits / test_records /
   consents / imports / matches / shared_matches.
7. Audit `ERASURE_COMPLETED`: counts only — `soft_deleted: {persons: N,
   ...}`, `hard_deleted_dna: {kits: M, ...}`. **No PII** в metadata
   (no user_id / email / display_name).
8. Clerk delete (`DELETE /v1/users/{clerk_user_id}`) — best-effort.
   Failure → `clerk_deleted=False` в metadata, audit отражает; не блокирует.
9. Email `kind=erasure_confirmation`, `idempotency_key=erasure_confirmation:{request_id}`.
10. `status='done'`, `processed_at=now()`, `users.deleted_at=now()`.
11. **Failure path** (любое исключение после processing-transition):
    `status='failed'`, `error=str(exc)`, audit `ERASURE_FAILED`. Без
    auto-retry на arq-уровне — failure требует admin investigation.

## Последствия

- **Положительные:**
  - Audit-trail полная + verifiable: каждая запись имеет provenance
    pointer на erasure_request, counts проверяются tests'ом.
  - Idempotent: terminal-status early-return защищает от случайного
    re-enqueue (worker crash / dual deployment).
  - Privacy by design: DNA исчезает физически; domain-data soft с
    pointer только для admin-side recovery (Phase 4.11c).

- **Отрицательные / стоимость:**
  - Soft-deleted persons / events остаются в БД — storage растёт.
    Retention-purge (Phase 4.11c+) обязан появиться до production-scale.
  - Clerk delete best-effort — admin-side reconciliation добавит
    operational burden до Phase 4.11c automation.
  - Migration 0022 (CHECK-constraint) — drop+create под нагрузкой;
    safe только если `user_action_requests` маленькая (на текущем
    этапе — да).

- **Риски:**
  - Race условия: user добавляет shared-member между edge-check'ом и
    cascade-soft-delete'ом → soft-delete всё равно пропустит этот tree
    (defensive `_list_solo_owned_tree_ids`). Other-member увидит, что
    его tree всё ещё доступен.
  - Email-dispatcher на Phase 12.2a — stub. Erasure_confirmation
    log-only до 12.2b.
  - DNA hard-delete не освобождает MinIO/GCS объекты автоматически —
    `dna_test_records.storage_path` хранится в row, а row удаляется.
    Для actual blob cleanup нужен отдельный sweeper (Phase 4.11c
    или storage-lifecycle policy).

- **Что нужно сделать в коде:**
  - ✅ Этот PR: cascade_delete utility, run_user_erasure runner,
    arq job, endpoint enqueue, audit + email integration, ADR-0049,
    migration 0022.
  - Phase 4.11c: ownership transfer flow (auto-promote earliest
    EDITOR → OWNER, или admin pick), retention-purge cron, MinIO
    blob sweeper, manual_intervention_required UI.
  - Phase 12.2b: реальная отправка ERASURE_CONFIRMATION email.

## Когда пересмотреть

- Если регуляторы потребуют explicit hard-delete для domain (не только
  DNA): переход к variant A с alternative audit-trail.
- Если erasure-volumes вырастут так, что bulk UPDATE'ы блокируют
  online-traffic: добавить chunked processing с `LIMIT N` per
  iteration (см. arq retry pattern).
- Если ownership-transfer (Phase 4.11c) родит race-cases: расширить
  edge-checks или ввести distributed-lock на (user_id, tree_id).

## Ссылки

- Связанные ADR: ADR-0003 (versioning), ADR-0012 (DNA privacy),
  ADR-0038 (user_action_requests schema), ADR-0046 (export pipeline,
  audit conventions).
- GDPR Art. 17: <https://gdpr-info.eu/art-17-gdpr/>.
- Clerk Backend API delete: <https://clerk.com/docs/reference/backend-api/tag/Users#operation/DeleteUser>.
