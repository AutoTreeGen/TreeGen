# ADR-0036: Sharing & permissions model (Phase 11.0)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `data-model`, `security`, `phase-11`

## Контекст

До Phase 11.0 каждое дерево принадлежало одному пользователю
(`Tree.owner_user_id`). Любые соавторы / зрители делались вручную.
Phase 11.0 — первый шаг к семейной коллаборации: владелец должен уметь
пригласить родственников редактором или зрителем, и потом отозвать
доступ.

Контекст ограничивает дизайн:

- Параллельно идёт Phase 4.10 (Clerk JWT auth). Текущий `current_user` —
  стаб (`X-User-Id` header → DB-lookup, fallback на `settings.owner_email`).
  Контракт зависимости стабильный, тело меняется.
- Аудитория: семьи 5–20 человек. Не Slack-workspace, не enterprise.
- DNA-данные = special-category (GDPR Art. 9) — за приглашением не должны
  утекать чужие email'ы / данные кита.
- MVP бюджет ≈ 800 LOC на PR (Phase 11.0 = первый PR из двух; PR 11.1
  закроет email delivery, frontend, audit-history-endpoint).

## Рассмотренные варианты

### Permission scheme: role-based vs ACL fine-grained

#### A. Role-based (3 роли: OWNER, EDITOR, VIEWER)

- ✅ Просто рассказать пользователю: «жена — editor, дети — viewer».
- ✅ Один FK + строковая колонка `role` на membership.
- ✅ Permission-checks — табличный compare ranks; pure function.
- ❌ Нельзя делать вещи вроде «может редактировать только person X, не Y».
   Нет field-level permissions.
- ❌ Owner-transfer — отдельная семантика, не «обмен role'ями».

#### B. ACL fine-grained (per-entity grants)

- ✅ Powerful: «can-edit person», «can-view source».
- ❌ Cartesian взрыв в `permissions` table — миллионы строк на дерево с 50k персон.
- ❌ Неподъёмный UX: «как мне дать жене доступ только к её ветке?» — не
   ответить без полного дерева в UI.
- ❌ Неполные тесты (matrix огромная).

#### C. Постфазная гибрид-модель: roles + per-feature flags

- ✅ Расширяемо: VIEWER + flag «can_export_gedcom», EDITOR + flag
   «can_invite_others».
- ❌ Уровень-сложности слишком высок для MVP.

### Способ приглашения: token-link vs add-by-email

#### A. Token-based invitation link

- ✅ Работает с не-зарегистрированными пользователями: invitee получает
   email со ссылкой, регистрируется, accept'ит.
- ✅ Идемпотентно: один token = одно приглашение, повторный accept = no-op.
- ✅ TTL на токене защищает от долгоживущих утечек.
- ❌ Ссылка летит в plain email — нужно TTL и revoke.
- ❌ Token в DB хранится в чистом виде — для MVP допустимо (UUID v4 = 122 бита
   энтропии, expires_at + revoke ограничивают окно атаки).

#### B. Direct add by email (без token-link'а)

- ✅ Проще: владелец пишет email, mb shows в /members сразу.
- ❌ Не работает для не-зарегистрированных: в `users` нет такой записи →
   куда binding? Создавать stub-user'а — privacy-leak (создаём аккаунт
   без согласия владельца email'а).
- ❌ Не работает для off-platform email-share (telegram, ручная пересылка).

#### C. Гибрид: invitation создаётся всегда; UI auto-accept'ит для уже-зарегистрированных

- ✅ Лучшее из обоих миров.
- ❌ Двойной path в фронте/бэке. Phase 11.0 — простой token-link, auto-accept
   = Phase 11.1 follow-up.

### Database integrity для OWNER-uniqueness

#### A. Application-level (race condition possible)

- ✅ Гибко.
- ❌ Race: два конкурентных PATCH role=owner → два OWNER.

#### B. DB-level partial unique index

- ✅ Жёсткая гарантия даже под concurrency.
- ✅ Postgres-нативно: `CREATE UNIQUE INDEX ... WHERE role='owner' AND revoked_at IS NULL`.
- ❌ Не дружит с MySQL/SQLite (не наша проблема — мы AlloyDB-only, ADR-0001).

## Решение

- **Permission scheme:** Вариант A (role-based, 3 роли). Гибридная модель — не
  ранее Phase 12 (если вообще понадобится).
- **Способ приглашения:** Вариант A (token-link с TTL 14 дней). Auto-accept
  для логиненных in-app users — Phase 11.1.
- **OWNER-uniqueness:** Вариант B (partial unique index). См.
  миграцию 0014.
- **Resource model:** Две таблицы.
  - `tree_memberships` — active access. ``UNIQUE(tree_id, user_id)`` +
    partial unique для OWNER. ``revoked_at`` — soft-revoke.
  - `tree_invitations` — pending email-invitations. Token (UUID v4),
    `expires_at`, `accepted_at`, `accepted_by_user_id`, `revoked_at`.
- **Permission gate:** FastAPI-зависимость `require_tree_role(TreeRole.X)`,
  парная `require_person_tree_role` для per-person endpoint'ов. Pure
  helper `check_tree_permission(session, user_id, tree_id, required)` для
  inline-checks.
- **Owner fallback:** если для tree нет membership-row, но
  `Tree.owner_user_id == user.id`, считаем OWNER. Compatibility-shim для
  trees, созданных через import-job / FS-importer / прямой ORM-insert
  до момента, когда «create tree» flow будет всегда писать membership-row
  (Phase 11.1). Backfill-INSERT в миграции 0015 покрывает trees, существующие
  на момент применения.

### Privacy guarantees

- `invitee_email` возвращается только в OWNER-видимых эндпоинтах
  (`POST/GET /trees/{id}/invitations`). accept-flow не возвращает email.
- Email-match (invitee_email == user.email при accept'е) НЕ enforce'им —
  invitee может зарегистрироваться под другим email и accept'нуть. Trade-off:
  токен — single-secret, кто знает токен, тот и accept'ит. Альтернатива
  (требовать совпадение email) ломает «sign up to accept» flow и UX
  для shared email-aliases. Митигация: short TTL (14d), revoke.
- 404 vs 403 для permission denial: gate возвращает 403 если tree существует,
  404 если нет. Существование дерева мы leak'аем для read-paths, но семантика
  совпадает с pre-Phase-11 поведением (тесты на 404 unknown-tree остались).
  Phase 11.2 — единый 404 для privacy, если решим.

### Что не сделано в этом PR (отнесено в Phase 11.1)

- 📨 **Email delivery.** Invitation отдаёт `invite_url` в JSON-ответе; UI/owner
  копирует и шлёт вручную. SendGrid через notification-service — Phase 11.1.
- 🖥️ **Frontend pages.** `/trees/[id]/sharing` и `/invitations/[token]` — Phase 11.1.
- 🧾 **Audit log integration.** Sharing-changes пока не пишутся в `audit_log`
  через event-listener. Endpoint `GET /trees/{id}/audit?type=membership` — Phase 11.1.
- 🔄 **Owner-transfer flow.** PATCH /memberships для OWNER-row отвергается 409.
  Полноценный transfer (`POST /trees/{id}/transfer-ownership`) — Phase 11.1.
- 🔓 **Implicit auto-accept** для in-app логиненных user'ов — Phase 11.1.

## Последствия

- ✅ Single-owner режим перестал быть единственным; платформа умеет
  колаборировать.
- ✅ Permission-gate централизован (`require_tree_role`); добавление гейта
  на новый endpoint = одна `dependencies=[…]` строка.
- ✅ Существующие тесты пропускаются (после fix'а двух обнаруженных
  тест-багов: `survivor_id` использовался как `tree_id`, и mismatch
  `owner_email` в фикстуре). Owner-fallback в `check_tree_permission`
  означает, что код, создающий Tree без membership, всё равно работает
  для самого владельца.
- ❌ `tree_collaborators` (legacy таблица из Phase 1) теперь dead code.
  Дроп — отдельной миграцией после Phase 11.1.
- ❌ Permission-check добавляет 1–2 SQL'я на каждый gated request. На
  staging-нагрузке некритично; на проде >100 RPS — рассмотреть кэш в
  `request.state` (один lookup на запрос).
- ❌ Permission-gate использует SAVEPOINT (`session.begin_nested()`) в
  persons-merge endpoint'ах, потому что autobegin от gate-SELECT'а
  конфликтует с явным `session.begin()`. Это совместимо, но добавляет
  semantic-дополнительный слой к транзакции.

## Когда пересмотреть

- Появятся public family trees (Phase 11.2) — нужен `TreeRole.PUBLIC`
  или отдельный `Tree.visibility=PUBLIC` обработка (без membership).
- ≥ 1000 active users → 100 RPS на gated endpoints → нужен
  request-state кэш role lookup'а.
- Field-level permissions (например, «эта персона видна только OWNER»)
  → переход к гибридной модели (роли + per-feature flags).
- Регуляторное требование «hide invitee email в audit-log» →
  refactor sharing audit с redacted email.

## Ссылки

- ADR-0001 — стек.
- ADR-0010 — Clerk auth (Phase 4.10) — целевой replace для auth-stub'а.
- ADR-0027 — Fernet at-rest (FS OAuth) — паттерн «secrets stored encrypted».
- ROADMAP §15 — Community / collaboration.
- `infrastructure/alembic/versions/2026_04_28_0015-0015_tree_memberships_invitations.py`
- `services/parser-service/src/parser_service/api/sharing.py`
- `services/parser-service/src/parser_service/services/permissions.py`
