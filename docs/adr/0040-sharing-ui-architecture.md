# ADR-0040: Sharing UI architecture (Phase 11.1)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `frontend`, `phase-11`, `sharing`

## Контекст

Phase 11.0 (ADR-0036) положил backend для membership/invitation. Phase 11.1
надевает на это UI: страница `/trees/{id}/access` для владельца + accept-flow
по invite-ссылке + опциональная отправка email через email-dispatcher stub.

Контекст ограничивает выбор:

- Phase 4.10 Clerk auth ещё не приземлился; UI работает поверх auth-stub'а
  (X-User-Id header). Контракт `acceptInvitation` стабилен — после Phase 4.10
  только UX-кусок «sign-in if 401» начнёт реально срабатывать.
- Phase 12.2 transactional-email тоже ещё параллельно. Email-dispatch — через
  локальный stub (`send_share_invite` в parser-service), который Phase 12.2
  заменит на HTTP-POST в email-service.
- CLAUDE.md PR-budget ≈ 500–800 LOC; этот PR делит scope на 11.1a (текущий —
  must-have UI + email stub) и 11.1b (tree-picker dropdown — отдельный PR).

## Рассмотренные варианты

### Email integration: direct SMTP vs internal dispatch service vs stub

#### A. Direct SMTP (smtplib / SendGrid SDK) в parser-service

- ✅ Просто, работает мгновенно.
- ❌ Дублирует логику в каждом сервисе, который шлёт email.
- ❌ Privacy / opt-out проверки разбросаны.

#### B. Внутренний email-service (Phase 12.2, Agent 3)

- ✅ Один источник правды для email-flow: opt-out, idempotency,
  rate-limiting, deliverability.
- ✅ HTTP-контракт между parser-service и email-service документирован.
- ❌ Phase 12.2 ещё не в main; синхронная зависимость заблокировала бы 11.1.

#### C. Stub в parser-service сейчас, swap на B позже

- ✅ Phase 11.1 не блокируется; UI и flow работают.
- ✅ Когда Phase 12.2 приземлится — Agent 3 меняет тело
  `send_share_invite` на `send_transactional_email("share_invite", ...)`.
  Сигнатура helper'а стабильна.
- ❌ В staging до Phase 12.2 invitation идёт только через UI «copy link» +
  ручную пересылку. Это compromise для timing'а.

### Accept landing: server-rendered guard vs client-side optimistic

#### A. Server-side: middleware проверяет invitation, рендерит «Accept» button

- ✅ SEO-friendly (хотя для invite-flow SEO не важен).
- ❌ Требует server actions / Next.js server route, больше plumbing.

#### B. Client-side optimistic: страница mount → POST accept → redirect

- ✅ Простая реализация, переиспользует API-client.
- ✅ Естественный fallback на 410/409/401 errors с дружественным UI.
- ❌ Двойной POST если user refresh'ит — но backend уже идемпотентен
  (ADR-0036 §accept-flow).

### Owner transfer: single-step vs 2-of-2 confirmation

#### A. Single-step DELETE

- ❌ Случайный клик — теряешь весь свой tree безвозвратно. Нет.

#### B. 2-of-2: select target + retype your own email

- ✅ Защищает от мисс-клика и от XSS-formjacking (нужен email caller'а,
  который атакующий не знает заранее).
- ❌ +1 экран UI. Приемлемо.

## Решение

- **Email integration:** Вариант C (stub сейчас, swap на email-service
  Phase 12.2). См. `services/parser-service/src/parser_service/services/email_dispatcher.py`
  для документированного swap-контракта.
- **Accept landing:** Вариант B (client-side optimistic). Страница mount =
  POST `/invitations/{token}/accept`; на 401 редирект в `/sign-in`, на 410
  показываем «expired», на 201/200 — redirect на `/trees/{id}/persons`.
- **Owner transfer:** Вариант B (2-of-2). UI открывает modal step 1
  «Pick member», step 2 «Type your email to confirm»; backend
  `PATCH /trees/{id}/transfer-owner` сверяет
  `current_owner_email_confirmation` с email caller'а.
- **Email masking в /access:** local part после первого символа маскируется
  (`a***z@example.com`), чтобы неосторожный screenshot UI не утекал
  полные email'ы. Сервер всё равно возвращает full email — клиент
  ответственен за рендер. Полный текст копируется только при OWNER explicit
  action (transfer-modal, copy-invite-link).

### Что не сделано в этом PR (отнесено в Phase 11.1b)

- 🌳 **Tree-picker dropdown в site-header** — пользователи в multi-tree
  setup пока не видят, в каком дереве находятся. Cookie-persisted last-selected
  tree. Phase 11.1b separate PR.
- 🧾 **Audit-log endpoint UI** — backend готов (`GET /trees/{id}/audit-log`),
  UI потребления — Phase 11.1b.
- 🔐 **Real Clerk integration** — Phase 4.10 заменит auth-stub, добавит
  middleware, который сделает `/sign-in?redirect=...` реальным. Сейчас в
  invite-page есть hook'и которые автоматически активируются после Phase 4.10.

## Tree-picker cookie pattern (для Phase 11.1b)

Зафиксировано здесь, чтобы 11.1b просто следовал контракту:

- Cookie name: `atg_last_tree_id`.
- HttpOnly: **false** (нужен access из client component'а).
- SameSite: `Lax` (cross-site request не несёт cookie, но top-level
  navigation несёт — для invite-link redirect'ов).
- Path: `/`.
- Max-Age: 90 дней.
- Set-on: каждый успешный `useEffect` mount страницы `trees/[id]/*` (через
  `document.cookie`, без серверного round-trip).
- Read-on: header dropdown `useEffect`; сравнить с `members.tree_id` из
  /api/trees, если cookie указывает на дерево, доступа к которому больше
  нет (revoked) — fall back на первый available tree.

**Не использовать localStorage** — server-rendered страницы не имеют
к нему доступа, и middleware (который Phase 4.10 добавит для auth-redirect)
не сможет читать last-selected.

## Последствия

- ✅ Owner может полноценно управлять доступом без CLI / SQL.
- ✅ Email dispatch staging-ready: log line + idempotency-key, готово
  принять real email-service swap без правок.
- ✅ Owner-transfer надёжен — одна ошибка email требует и step-1 select'а,
  и step-2 retype, и backend-уровневой проверки.
- ❌ Email masking — UX trade-off: для семьи где все знают email'ы друг
  друга это лишний шум. Можно добавить «show full email» toggle в
  Phase 11.2.
- ❌ Multi-tree UX без picker'а — Phase 11.1b. Пока пользователь должен
  знать tree_id из URL.
- ❌ Audit-log UI — backend готов и тестирован, UI отложен до 11.1b.
  Owner до тех пор смотрит историю через `gcloud logging read` или прямо
  в БД (одно SELECT).

## Когда пересмотреть

- Если Phase 12.2 приземлится в main и опубликует kind=`share_invite` —
  Agent 3 меняет тело `send_share_invite` на `send_transactional_email`.
  Этот ADR не пересматривается, swap внутренний.
- Если accept-flow начнёт показывать высокий 401-rate (≥10%) — добавить
  serverside intercept'или который сначала проверяет invitation, потом
  редирект, чтобы экономить round-trip пользователю.
- Если 2-of-2 transfer-flow начнёт жаловаться на UX — добавить
  «pre-confirm by email» (отправить link с одноразовым confirm-token'ом).

## Ссылки

- ADR-0036 — Sharing & permissions model (Phase 11.0).
- ADR-0010 — Clerk auth (Phase 4.10) — целевой replace для auth-stub.
- ADR (TBD) Phase 12.2 — transactional email — для swap-контракта.
- `services/parser-service/src/parser_service/api/sharing.py`
- `services/parser-service/src/parser_service/services/email_dispatcher.py`
- `apps/web/src/app/trees/[id]/access/page.tsx`
- `apps/web/src/app/invite/[token]/page.tsx`
