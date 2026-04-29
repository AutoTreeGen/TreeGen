# ADR-0050: Auto ownership-transfer for shared trees during GDPR erasure (Phase 4.11c)

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `gdpr`, `erasure`, `sharing`, `worker`, `audit`

## Контекст

Phase 4.11b (PR #135, in flight) реализует hard- и soft-delete cascade
для GDPR right-of-erasure (Art. 17). У этого pipeline есть «edge case»
который 4.11b сам не решает: если пользователь — OWNER дерева, в
котором есть ещё active members (editors / viewers), простой
soft-delete дерева убьёт доступ для них. По текущему дизайну Phase 11.0
(ADR-0036) **на дерево всегда должен быть ровно один OWNER** (партиал-
уникальный индекс `uq_tree_memberships_one_owner_per_tree`). Значит,
перед erasure уходящего owner'а его OWNER-роль должна быть передана
кому-то другому.

Существующий manual-flow `PATCH /trees/{tree_id}/transfer-owner`
(Phase 11.1, sharing.py:616-728) делает 2-of-2 transfer: owner вводит
свой email + email нового owner'а, эндпоинт атомарно меняет роли.
Этого не хватает для erasure: нужна автоматическая фоновая логика,
которая для каждого shared tree находит next-eligible editor и
выполняет swap **без участия user'а** (он уже инициировал erasure;
дальнейшие interactive шаги не уместны).

Фасоны решения:

- **Eligibility policy.** Кто получает дерево по умолчанию? Только
  editor, или viewer тоже? Старший member или новейший?
- **Failure mode.** Что делать если eligible editor нет?
- **Triggering.** Auto-transfer триггерится из 4.11b runner'а
  (preflight) или становится отдельным flow, который user должен
  пройти перед erasure?
- **Atomicity.** Re-use существующего swap-кода или дублировать?
- **Audit/discovery.** Как user узнает, что transfer произошёл (или
  не произошёл)?

## Рассмотренные варианты

### Eligibility policy

#### Вариант A — viewer eligible тоже

- ✅ Меньше «BLOCKED» случаев; легче автоматизировать.
- ❌ Viewer мог быть приглашён just-for-reading; передача ему
  ownership'а — нарушение implicit user expectation.
- ❌ Owner редко проверяет, кому он viewer-доступ давал.

#### Вариант B — только editor (выбран)

- ✅ Editor уже имеет write-доступ — ownership это «editor + admin
  rights», natural progression.
- ✅ Editor очевидно знал что работает с деревом.
- ❌ Если у дерева только viewer'ы — auto-transfer fails, нужен
  manual fallback.

#### Tiebreaker

- **Oldest editor wins** (`ORDER BY created_at ASC, id ASC`). Старший
  editor скорее всего имеет больший контекст / историю работы. UUID
  tiebreaker — детерминизм для тестов.

### Failure mode

#### Вариант A — abort erasure entirely

- ✅ Безопаснее — не оставляем orphaned trees.
- ❌ User не может удалить аккаунт пока не разберётся со всеми
  shared trees вручную.

#### Вариант B — emit notification, продолжить erasure (выбран частично)

- ✅ User получает actionable feedback.
- ❌ Если он его проигнорирует, дерево уйдёт в orphan-state.

#### Вариант C — emit notification + 4.11b решает (выбран)

- ✅ Делегируем policy 4.11b runner'у — он знает full erasure
  context (есть ли pending exports, активные subscriptions, etc).
- ✅ Loose coupling между фазами.
- 4.11c только эмитит counts (`auto_pickable` + `blocked_tree_ids`)
  и notifications; 4.11b сам решает abort vs continue.

### Triggering

#### Вариант A — sync inline в erasure runner

- ✅ Простота, atomic с erasure transaction.
- ❌ Slow если у user много trees; блокирует main worker.
- ❌ Ошибка одного transfer'а ломает всю erasure.

#### Вариант B — async per-tree через UserActionRequest (выбран)

- ✅ Per-transfer observability + retry per row.
- ✅ Erasure runner делает только preflight; основная работа —
  отдельные jobs.
- ❌ Больше state to manage (request rows + worker registration).

### Atomicity

#### Вариант A — extract `swap_tree_owner_atomic` helper (выбран)

- ✅ One source of truth — manual flow и auto-flow используют
  одну и ту же проверенную процедуру.
- ✅ Тестировать swap независимо от транспорта.
- ❌ Немного сдвигает sharing.py:transfer_owner internals.

#### Вариант B — duplicate logic in worker

- ✅ Изоляция; рефакторинг sharing.py не затрагивает worker.
- ❌ Code duplication; risk that swap-invariants drift apart.

### Audit / discovery

- **Tree-scoped audit_log entries** (`tree_id != NULL`): action
  `OWNERSHIP_TRANSFER_AUTO` для success, `OWNERSHIP_TRANSFER_BLOCKED`
  для failure. `actor_user_id` = уходящий owner, `actor_kind = SYSTEM`
  (worker инициировал).
- **In-app notification** через
  `NotificationEventType.OWNERSHIP_TRANSFER_REQUIRED` (новый kind) —
  best-effort. Channel: `in_app + log`.
- **Email to new owner** (`EmailKind.OWNERSHIP_TRANSFERRED`) с en/ru
  templates — explicit «вам передали дерево»; idempotency-key per
  request → safe re-enqueue.

## Решение

Выбран **Eligibility B + Failure C + Triggering B + Atomicity A**.
Конкретно:

1. Migration `0022_user_action_kind_ownership_transfer` —
   расширить `ck_user_action_requests_kind` на
   `('export', 'erasure', 'ownership_transfer')`.
   - **Numbering note:** PR #135 (Phase 4.11b) тоже использует
     revision="0022". Если #135 ляжет в main первым, эта миграция
     ребейзается на 0023 with `down_revision='0022'`.
2. `AuditAction` enum: `OWNERSHIP_TRANSFER_AUTO`,
   `OWNERSHIP_TRANSFER_BLOCKED`. Tree-scoped (entity_type='trees',
   entity_id=tree_id).
3. `EmailKind.OWNERSHIP_TRANSFERRED` + en/ru Jinja2 templates.
4. `NotificationEventType.OWNERSHIP_TRANSFER_REQUIRED` для blocked-
   case discovery.
5. `parser_service.services.ownership_transfer.swap_tree_owner_atomic`
   — extract атомарного swap'а из `sharing.py:transfer_owner`.
   Существующий PATCH endpoint теперь вызывает этот helper.
6. `parser_service.services.auto_transfer`:
   - `prepare_ownership_transfers_for_user(session, user_id)` —
     public preflight для 4.11b. Сканирует owned trees, создаёт
     `UserActionRequest(kind='ownership_transfer')` для каждого
     с eligible editor, эмитит notification + audit для blocked.
     Возвращает `PreparedTransferReport` с counts.
   - `run_ownership_transfer(session, request_id)` — worker logic.
     Re-validate eligible editor (between preflight и run может
     пройти время), atomic swap, audit, email.
7. `parser_service.worker.run_ownership_transfer_job` — arq entry
   зарегистрирован в `WorkerSettings.functions`.
8. `parser_service.services.notifications.notify_ownership_transfer_required`
   — async helper (mirror of `notify_hypothesis_pending_review`),
   enqueue'ит `dispatch_notification_job` arq.

### Что НЕ делаем в этой фазе

- **Не модифицируем `api/users.py:request_erasure`.** Wiring
  4.11b's runner → preflight call — separate Phase 4.11d (или
  inline когда оба landed).
- **Не добавляем endpoint `POST /trees/{id}/transfer-ownership`.**
  Existing PATCH endpoint cover'ит manual flow; новая POST была бы
  duplication.
- **Не реализуем «defer-until-eligible» автоматический retry.** Если
  blocked, user разбирается через notification → manual transfer
  через PATCH endpoint → re-trigger erasure. Acceptable UX trade-off
  для Phase 4.11c.

## Последствия

### Положительные

- Erasure pipeline для типичного case (shared tree с editor'ом)
  работает без manual интервенции.
- Re-use battle-tested swap-кода из Phase 11.1 — нет drift'а
  invariants partial-unique-OWNER constraint.
- Per-transfer observability через `UserActionRequest` rows +
  audit_log + arq result.

### Отрицательные / стоимость

- Migration 0022 collide-prone с PR #135's 0022. Renumbering на
  rebase — рутина.
- ADR-0050 предполагает Phase 4.11d для wiring в erasure runner;
  без неё 4.11c — useful library, но не end-to-end pipeline.
- `NotificationEventType.OWNERSHIP_TRANSFER_REQUIRED` discovery
  работает только если `AUTOTREEGEN_NOTIFICATION_SERVICE_URL` env
  задана (light-integration mode иначе silent-skip'ает).

### Риски

- **Race condition: editor revoke'нул себя между preflight и run.**
  Mitigation: worker re-validate eligible editor; если уже нет —
  blocked path (status=failed + audit + notification).
- **Race condition: новый OWNER случайно тут же уходит сам в
  erasure.** Cascading erasure → cascading auto-transfer. Без
  loop-detection. Acceptable для now: каждый erasure-request
  проходит свой preflight; circular ownership-transfer scenarios
  в реальности редки и обнаруживаются через 2nd-iteration BLOCKED.
- **Email idempotency.** `idempotency_key=ownership_transfer:{request_id}`
  — re-enqueue не создаст второй email. ✅.

### Что нужно сделать в коде

- [x] Migration `0022_user_action_kind_ownership_transfer`.
- [x] Enum extensions (`AuditAction.OWNERSHIP_TRANSFER_*`,
      `EmailKind.OWNERSHIP_TRANSFERRED`,
      `NotificationEventType.OWNERSHIP_TRANSFER_REQUIRED`).
- [x] `swap_tree_owner_atomic` helper extract + sharing.py refactor.
- [x] `auto_transfer` service module.
- [x] `run_ownership_transfer_job` arq registration.
- [x] `notify_ownership_transfer_required` notification helper.
- [x] Email templates en/ru.
- [x] `tree_id` + `previous_owner_user_id` в email-service redaction
      allowlist.
- [x] Tests: prepare (no trees / solo / editor / viewer-only / oldest
      pick / multi-tree); run (happy / blocked-at-runtime / idempotent);
      direct swap unit.

### Integration handoff

Когда оба 4.11b (#135) и 4.11c (this PR) лягут, `request_erasure`
должен в начале вызвать
`prepare_ownership_transfers_for_user(session, user_id)` и enqueue
`run_ownership_transfer_job(request_id)` для каждого
`auto_pickable_request_ids`. Если `blocked_tree_ids` непустой —
4.11b's runner может либо abort'ить erasure (UX: «complete pending
ownership transfers first») либо продолжить с предупреждением
(orphaned trees). Эта policy-логика — **Phase 4.11d**, не входит
в этот PR.

## Когда пересмотреть

- Если viewer-eligibility начнёт запрашиваться enterprise-customer'ами.
- Если cascading erasure (owner A передаёт owner'у B → B немедленно
  уходит в erasure → ...) станет реальной проблемой; ADR на
  loop-detection.
- Если notification-service `user_id` BigInteger → UUID миграция
  состоится, переписать `_emit_blocked_notification` на direct
  Notification insert.

## Ссылки

- ADR-0036 (sharing/permissions — определяет «owner», membership lifecycle).
- ADR-0046 (Phase 4.11a GDPR export — pattern для UserActionRequest worker).
- ADR-0024 (Notification model + dispatch — origin of BigInteger
  user_id legacy).
- ADR-0029 (Notification delivery model — channel layering).
- GDPR Art. 17 (right to erasure).
