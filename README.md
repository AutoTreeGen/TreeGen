# AutoTreeGen / SmarTreeDNA

AI-платформа для научной генеалогии: GEDCOM + ДНК + архивы + движок гипотез.

> **Статус:** Фаза 0 (фундамент). См. [`ROADMAP.md`](./ROADMAP.md) — рабочая дорожная карта.

---

## Принципы

- **Evidence-based.** Каждое утверждение в дереве имеет источник, степень уверенности и историю изменений.
- **Hypothesis-first.** Система хранит не только факты, но и гипотезы со всеми «за» и «против».
- **Provenance everywhere.** Для каждого узла известно, откуда он пришёл (GEDCOM file X, DNA match Y, архив Z).
- **Versioning everywhere.** Ничего не удаляется молча, всё версионируется.
- **Domain-specific.** Еврейская генеалогия, Восточная Европа, транслитерация — first-class citizens.
- **MVP-driven.** Сначала рабочий парсер на личном GED-файле, потом всё остальное.

---

## Структура репозитория

```text
autotreegen/
├── ROADMAP.md                  ← рабочая дорожная карта (читать первой)
├── CLAUDE.md                   ← инструкции для Claude Code
├── docker-compose.yml          ← локальная инфраструктура (Postgres + Redis + MinIO)
├── pyproject.toml              ← Python workspace (uv)
├── pnpm-workspace.yaml         ← Frontend workspace (pnpm)
├── packages/                   ← переиспользуемые Python-пакеты
│   ├── gedcom-parser/          ← Фаза 1: парсер GEDCOM 5.5.5
│   ├── dna-analysis/           ← Фаза 6: алгоритмы ДНК-анализа
│   ├── entity-resolution/      ← Фаза 7: дедупликация
│   ├── inference-engine/       ← Фаза 8: движок гипотез
│   └── shared-models/          ← Pydantic-модели
├── services/                   ← FastAPI-сервисы
│   ├── api-gateway/
│   ├── parser-service/
│   ├── dna-service/
│   ├── archive-service/
│   ├── inference-service/
│   └── notification-service/
├── apps/web/                   ← Next.js фронтенд
├── infrastructure/             ← Terraform / k8s / Alembic
├── docs/                       ← архитектура, ADR, спецификации
└── scripts/                    ← вспомогательные скрипты
```

---

## Локальный запуск (быстрый старт)

> **Требования:** Windows + WSL2 (или macOS / Linux), Docker Desktop, Python 3.12 (через [`uv`](https://github.com/astral-sh/uv)),
> Node.js 22+ (через [`fnm`](https://github.com/Schniz/fnm)), [`pnpm`](https://pnpm.io/) 9+.

```bash
# 1. Клонировать
git clone <repo-url> autotreegen && cd autotreegen

# 2. Поднять локальную инфраструктуру
cp .env.example .env
docker compose up -d
# Postgres: localhost:5432  /  Redis: localhost:6379  /  MinIO: localhost:9001

# 3. Установить Python-зависимости (workspace через uv).
#    `--all-packages` — все workspace members (без флага ставятся только root deps).
#    `--all-extras` — optional dependency groups. Так делает CI (см. .github/workflows/ci.yml).
uv sync --all-extras --all-packages

# 4. Установить frontend-зависимости
pnpm install

# 5. Pre-commit hooks
uv run pre-commit install
```

Проверка: `docker compose ps` — все сервисы `healthy`.

---

## Authentication (Phase 4.10, ADR-0033)

Все user-facing endpoints в `parser-service` / `dna-service` /
`notification-service` требуют Bearer JWT, выпущенный Clerk.
Frontend (`apps/web`) использует `@clerk/nextjs` middleware для входа
и `useAuth().getToken()` для прикрепления токена к API-вызовам.

### Clerk dashboard setup

1. Зарегистрируйся на <https://clerk.com>, создай application.
2. **Settings → API Keys**:
   - Скопируй **Publishable key** → `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
     (для apps/web).
   - Скопируй **Secret key** → `CLERK_SECRET_KEY`
     (для apps/web Server Components).
   - Из «Show JWT public key» возьми **issuer URL** (например,
     `https://accept-XXXX.clerk.accounts.dev`) и положи в три бекенд-
     ENV: `PARSER_SERVICE_CLERK_ISSUER`, `DNA_SERVICE_CLERK_ISSUER`,
     `NOTIFICATION_SERVICE_CLERK_ISSUER`.
3. **Webhooks → Endpoint**:
   - Endpoint URL: `https://<your-domain>/webhooks/clerk` (parser-service).
   - Subscribe to: `user.created`, `user.updated`, `user.deleted`.
   - Скопируй **Signing Secret** → `PARSER_SERVICE_CLERK_WEBHOOK_SECRET`.
4. Подними фронт (`pnpm dev` в `apps/web`), зайди на `/sign-up` —
   зарегистрируй тестового user'а. На первом authed-API-вызове
   parser-service автоматически создаст `users` row через
   `get_or_create_user_from_clerk` (JIT).

### Local dev без Clerk

Если бекенд `*_CLERK_ISSUER` пуст, auth-зависимости возвращают **503
Service Unavailable** на любой защищённый endpoint. Это сделано
сознательно: misconfigured-окружение не должно «молча» пропускать
неаутентифицированных юзеров. Для локального dev'а:

- Frontend: установи `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (Clerk
  выдаёт `pk_test_*`-ключи на dev-app).
- Backend: установи `*_CLERK_ISSUER` на dev-issuer URL.
- Tests: используют `dependency_overrides` (см. `conftest.py`),
  поэтому работают и без реального issuer'а.

См. ADR-0033 для полного флоу + migration path при смене провайдера.

---

## Конвенции

- **Код и идентификаторы — на английском.**
- **Комментарии и docstrings — на русском.**
- **Сообщения коммитов — Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:`, …).
- **Прямые коммиты в `main` запрещены.** Все изменения через PR.
- **Архитектурные решения — через ADR** (`docs/adr/`).
- Подробности — в [`CLAUDE.md`](./CLAUDE.md).

---

## Лицензия

TBD (определить до публичного beta).

---

## Контакты

- Maintainer: Владимир (`autotreegen@gmail.com`)
