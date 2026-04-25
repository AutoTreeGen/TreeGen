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

```
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

# 3. Установить Python-зависимости (workspace через uv)
uv sync

# 4. Установить frontend-зависимости
pnpm install

# 5. Pre-commit hooks
uv run pre-commit install
```

Проверка: `docker compose ps` — все сервисы `healthy`.

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
