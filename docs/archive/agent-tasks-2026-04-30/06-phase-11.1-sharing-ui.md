# Agent 6 — Phase 11.1: Sharing UI (owner page + accept-flow)

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (`F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md`.
2. `ROADMAP.md` — §15 «Фаза 11 — Сообщество и совместная работа», особенно «Phase 11.1 — UI + email + audit (next PR)». ⚠️ Email-часть и owner-transfer уже сделаны (Phase 4.11c, PR #136), НЕ дублируй.
3. ADR-0036 — permission API; ADR-0037 — i18n стратегия; ADR последний sharing-PR `5fbe065 phase 11.2 — public tree share`.
4. `services/parser-service/src/parser_service/api/sharing.py` — все эндпоинты (POST/GET `/trees/{id}/invitations`, DELETE `/invitations/{id}`, POST `/invitations/{token}/accept`, GET `/trees/{id}/members`, PATCH/DELETE `/memberships/{id}`).
5. `apps/web/` — текущая структура: App Router, `<SiteHeader>`, `next-intl` setup, паттерн `messages/{en,ru}.json`, `<ErrorMessage>` компонент.
6. Существующие UI Phase 4.13a/b — как сейчас локализованы pages.

## Задача

Реализовать UI-часть Phase 11.1:

1. Owner page `/trees/[id]/sharing`.
2. Accept-flow `/invitations/[token]`.
3. Tree-picker dropdown в `<SiteHeader>`.
4. i18n с самого начала (en/ru).

## ⚠️ Этот агент — **единственный**, кому разрешено

- Менять `apps/web/messages/{en,ru}.json` — добавить namespace `sharing.*`.
- Менять что-либо в `apps/web/`.

## Scope

### 1. `/trees/[id]/sharing` (owner page)

- Защита: только OWNER. Если пользователь не OWNER — 403 page (используй `<ErrorMessage code="forbidden" />`).
- Секции:
  - **Members** — таблица: avatar, name/email, role (OWNER/EDITOR/VIEWER), joined date. Owner может изменить role (PATCH `/memberships/{id}`) или revoke (DELETE `/memberships/{id}` с confirm dialog «remove access»).
  - **Pending invitations** — таблица: email, role, sent date, кнопка «Revoke» (DELETE `/invitations/{id}`).
  - **Invite form**: email + role select + optional message → POST `/trees/{id}/invitations`. Toast «Invitation sent» при успехе, ErrorMessage при failure.
- TanStack Query (как в остальном проекте), optimistic updates где уместно.

### 2. `/invitations/[token]` (accept-flow)

- Public route (но требует Clerk auth для submit).
- Server component: pre-fetch invitation details (нужен новый эндпоинт `GET /invitations/{token}` в parser-service — добавь его как часть этой задачи; возвращает invited email, role, tree name, inviter name, expiry — без accepting).
- States:
  - Token invalid/expired → ErrorMessage + кнопка «Go to dashboard».
  - Token valid, user not signed in → Clerk SignIn modal.
  - Token valid, user signed in but email mismatch (пригласили на другой email) → понятное предупреждение + кнопка «I'll sign in as <invited_email>».
  - Token valid, user signed in, email match → кнопка «Accept invitation» → POST `/invitations/{token}/accept` → редирект на `/trees/{id}`.

### 3. Tree-picker dropdown в `<SiteHeader>`

- Если у пользователя ≥1 tree (включая shared) — dropdown в хедере с именами деревьев, текущее выделено, last-active первым.
- Внизу dropdown — «Manage trees» → `/dashboard`.
- При выборе — обновляет cookie `current_tree_id` + редиректит на `/trees/{id}`.
- Если 0 trees — не рендерить dropdown (sign of empty state).

### 4. i18n

- Новый namespace `sharing.*` в `apps/web/messages/en.json` и `apps/web/messages/ru.json`. Все UI-строки через `useTranslations("sharing")`.
- Email-приглашение **уже** локализовано в notification-service (Phase 4.11c) — проверь там, добавь только то, чего нет.
- Pre-commit hook `scripts/check_i18n_strings.py` (Phase 4.13a) проверит — НЕ оставляй raw English JSX text.

## Тесты

- `apps/web/src/app/trees/[id]/sharing/page.test.tsx` (vitest + RTL) — рендер таблиц, обработка role change, error states. Mock fetch.
- `apps/web/src/app/invitations/[token]/page.test.tsx` — все 4 state'а.
- `apps/web/src/components/site-header.test.tsx` — tree-picker show/hide логика.
- `apps/web/src/__tests__/locale-rendering.sharing.test.tsx` — рендер обоих локалей без missing-key fallback'ов, parity en ↔ ru.
- `services/parser-service/tests/test_invitation_lookup.py` — новый GET `/invitations/{token}` эндпоинт: 404 для неизвестного, 410 для expired, 200 валидный без consume.

## Запреты

- ❌ Alembic-миграции — все таблицы из Phase 11.0 (миграция 0015) уже есть.
- ❌ `packages/shared-models/`.
- ❌ Backend sharing endpoints (кроме `GET /invitations/{token}` lookup) — они уже в parser-service.
- ❌ Email-шаблоны (уже сделано в Phase 4.11c).
- ❌ Корневой `pyproject.toml`.

## Процесс

1. `git checkout -b feat/phase-11.1-sharing-ui`
2. Коммиты: `feat(parser-service): GET /invitations/{token} lookup`, `feat(web): sharing owner page`, `feat(web): invitation accept flow`, `feat(web): tree-picker dropdown`, `feat(web): sharing i18n`, `test(...)`.
3. `uv run pre-commit run --all-files` + `uv run pytest services/parser-service` + `pnpm -F web test` + `pnpm -F web typecheck` + `pnpm -F web lint` перед каждым коммитом.
4. Локально проверь, что обе локали рендерятся без warnings: `NEXT_LOCALE=ru pnpm -F web dev`.
5. **НЕ мержить, НЕ пушить в `main`.**

## Финальный отчёт

- Ветка, коммиты, vitest+pytest summary, файлы, скриншоты UI (если можешь сделать через playwright headless), open questions.
