# CLAUDE.md

Инструкции для Claude Code и других AI-ассистентов, работающих с этим репозиторием.

> **Перед началом любой задачи:** прочитай `ROADMAP.md`, относящуюся к фазе секцию,
> и соответствующий файл в `docs/` (если есть).

---

## 1. О проекте

**AutoTreeGen / SmarTreeDNA** — AI-платформа для научной генеалогии. Объединяет
GEDCOM-данные, ДНК-результаты и архивные источники в единое evidence-based древо
с движком гипотез.

Полная дорожная карта: `ROADMAP.md`. Архитектура: `docs/architecture.md`.

---

## 2. Языковые конвенции

| Артефакт | Язык |
|---|---|
| Имена идентификаторов (классы, функции, переменные, файлы, БД-таблицы) | **English** |
| Сообщения об ошибках в коде, логи | **English** |
| Комментарии, docstrings | **Russian** (для владельца проекта так удобнее) |
| Документация (`README`, `docs/`, ADR) | **English** |
| UI-строки фронтенда по умолчанию | **English** (i18n с ru-локалью добавится в Phase 4.1) |
| Сообщения коммитов | **English** (Conventional Commits) |

> **Контекст языковых выборов.** Идентификаторы и пользовательские интерфейсы —
> по-английски, чтобы проект был доступен международной аудитории и не зависел от
> кодировок в инструментарии. Документация — по-английски как стандарт open-source
> и чтобы PR-ревью с внешними контрибьюторами было возможно. Комментарии и
> docstrings — по-русски, потому что владелец проекта читает русский быстрее, и
> при code review важна скорость локального понимания.

**Пример docstring:**

```python
def parse_gedcom_date(value: str) -> ParsedDate:
    """Разбирает GEDCOM date phrase в структурированную дату.

    Поддерживает: ABT, BEF, AFT, BET..AND, FROM..TO, юлианский/григорианский
    календари, иврит-даты. Сохраняет оригинальную строку в `raw` для round-trip.

    Args:
        value: Строка вида "ABT 1850" или "BET 1840 AND 1845 (Old Style)".

    Returns:
        ParsedDate с диапазоном и оценкой uncertainty.

    Raises:
        GedcomDateParseError: If the value cannot be parsed.
    """
```

---

## 3. Архитектурные принципы (нерушимые)

1. **Evidence-first.** Каждое утверждение → источник + confidence + provenance.
   Никаких «голых фактов» без атрибуции.
2. **Hypothesis-aware.** Гипотезы — first-class entity, не «черновики». Хранятся
   с rationale и evidence-graph.
3. **Provenance everywhere.** Поле `provenance` (jsonb) на всех доменных записях
   дерева (persons, families, events, places, sources, notes, multimedia).
   Минимум: `source_files`, `import_job_id`, `manual_edits`.
4. **Versioning everywhere.** Soft delete (`deleted_at`), audit log, восстановление
   из снапшотов. Стратегия — см. ADR-0003.
5. **Privacy by design.** ДНК-данные = special category (GDPR Art. 9). Шифрование
   at-rest на application-level, явное consent, политика удаления.
6. **Deterministic > magic.** LLM применяется только там, где он реально полезен
   (см. Фаза 10). Базовые операции — детерминированные.
7. **Domain-aware.** Восточная Европа XIX–XX вв., еврейская генеалогия,
   транслитерация — учитывать с самого начала, не «потом».

---

## 4. Технологический стек

См. `ROADMAP.md` секция 1. Кратко:

- **Backend:** Python 3.12, FastAPI 0.115+, Pydantic v2, SQLAlchemy 2 (async), Alembic.
- **Frontend:** Next.js 15, React 19, TypeScript (strict), Tailwind 4, shadcn/ui.
- **БД:** PostgreSQL 16 + pgvector. Локально через `docker compose`.
- **Очереди:** `arq` на Redis (локально), Cloud Tasks (прод).
- **Storage:** MinIO (локально), GCS (прод).
- **LLM:** Anthropic Claude API. **Embeddings:** Voyage AI.
- **Менеджер пакетов Python:** `uv` (НЕ poetry, НЕ pip напрямую).
- **Менеджер пакетов JS:** `pnpm` (НЕ npm, НЕ yarn).

---

## 4a. Карта репозитория

Точные детали — `docs/architecture.md`. Минимум для ориентирования:

| Путь | Что внутри |
|---|---|
| `packages/*` | Переиспользуемые Python-библиотеки (uv workspace member). Текущие: `gedcom-parser`, `dna-analysis`, `entity-resolution`, `inference-engine`, `shared-models`. |
| `services/*` | FastAPI-сервисы (uv workspace member): `api-gateway`, `parser-service`, `dna-service`, `archive-service`, `inference-service`, `notification-service`. |
| `apps/web/` | Next.js фронтенд (pnpm workspace member). |
| `infrastructure/alembic/` | Миграции БД. |
| `infrastructure/postgres/init/` | Init-скрипты Postgres (монтируются в `docker-entrypoint-initdb.d`). |
| `infrastructure/terraform/`, `infrastructure/k8s/` | Прод-инфра (GCP). |
| `docs/` | `architecture.md`, `data-model.md`, `gedcom-extensions.md`, `adr/`. |
| `scripts/` | Вспомогательные скрипты (CLI, миграционные утилиты). |
| `Ztree.ged` | Личный GED-файл владельца, используется как fixture для `-m gedcom_real`. **В `.gitignore`.** |

`pnpm-workspace.yaml` объявляет `apps/*` и `packages-js/*` (последний — на будущее, пока не существует).

---

## 5. Запреты

- ❌ **Прямые коммиты в `main`.** Только через PR + ревью.
- ❌ **Секреты в коде.** Только через `.env` (в `.gitignore`) или Secret Manager.
- ❌ **Личный GED-файл (`Ztree.ged`) в коммитах.** Только локальный test fixture.
- ❌ **DNA-данные в репозитории.** Тестовые DNA — синтетические/обезличенные.
- ❌ **Breaking changes без ADR.** Любая ломка контракта — через `docs/adr/`.
- ❌ **Скрейпинг платформ без публичного API** (Ancestry, 23andMe, …).
  См. `ROADMAP.md` секция 13.
- ❌ **Автоматический merge персон с близким родством без manual review.**

---

## 6. Стандарты качества

| Метрика | Цель |
|---|---|
| Покрытие тестами новой логики | > 80% |
| Mypy / TypeScript strict | без `any`, без `# type: ignore` без комментария-обоснования |
| Линтеры | ruff (lint + format), biome — passing |
| p95 API latency | < 300 мс (для эндпоинтов без LLM) |
| Размер PR | < 500 строк диффа желательно |

> **Mypy в pre-commit** запускается только на `^(packages|services)/.+\.py$`,
> исключая `tests/` и `samples/` — strict-онбординг постепенный, новый код
> в этих путях должен проходить strict сразу.

**`--no-verify` запрещён.** Если pre-commit падает — чинить причину, не
обходить. Для AI-агентов это правило тем более жёсткое: bypass превращает
red CI в норму (см. ADR-0008).

**Перед `git push` обязательно прогнать полный `check`-скрипт:**

- Windows: `pwsh scripts/check.ps1`
- Linux / macOS: `bash scripts/check.sh`

Это локальное зеркало шагов CI job `lint-and-test`. Парность гарантирует
`tests/test_ci_parity.py` — он сравнивает множества команд в `check.{sh,ps1}`
и `.github/workflows/ci.yml` и падает при расхождении.

---

## 7. Команды (локально)

```bash
# Инфраструктура
docker compose up -d                 # поднять Postgres+Redis+MinIO
docker compose down                  # остановить
docker compose down -v               # полный сброс с volumes

# Python (uv workspace)
uv sync                                    # установить все зависимости
uv run pytest                              # все тесты
uv run pytest -m "not slow and not integration"   # быстрый цикл
uv run pytest -m gedcom_real               # тесты на личном GED (skipped в CI)
uv run pytest path/to/test.py::test_name   # один конкретный тест
uv run --package gedcom-parser pytest      # тесты пакета изнутри workspace
uv add --package gedcom-parser <dep>       # добавить зависимость в подпакет
uv run ruff check .                        # линт
uv run ruff format .                       # форматирование
uv run mypy .                              # типы

# Pytest-маркеры (определены в pyproject.toml):
#   slow         — медленные тесты
#   integration  — требуют docker-compose сервисов
#   gedcom_real  — используют реальные GED-файлы (skipped в CI)

# Корпус реальных GED-файлов от разных платформ (Ancestry, MyHeritage, Geni,
# UTF-16, ANSEL, разные размеры до 150 МБ). Путь конфигурируется через env var:
#   GEDCOM_TEST_CORPUS=D:/Projects/GED  (default — этот же)
# Если папки нет — параметризованный smoke автоматически skip-ается.
GEDCOM_TEST_CORPUS=D:/Projects/GED uv run pytest packages/gedcom-parser -m gedcom_real

# Frontend / monorepo-алиасы из корневого package.json
pnpm install                         # установить все зависимости
pnpm dev                             # = pnpm -F web dev
pnpm build                           # сборка всех workspaces
pnpm lint                            # biome check . (весь репо)
pnpm lint:fix                        # biome check --write .
pnpm format                          # biome format --write .
pnpm typecheck                       # tsc -r во всех workspaces
pnpm test                            # тесты всех workspaces
pnpm -F web dev                      # точечно: только Next.js

# Pre-commit
uv run pre-commit run --all-files    # прогнать все хуки

# Миграции БД (после Фазы 2)
uv run alembic revision --autogenerate -m "описание"
uv run alembic upgrade head
```

---

## 8. Workflow задачи

1. **Прочитать контекст:** `ROADMAP.md` (секция фазы), `docs/architecture.md`,
   `docs/data-model.md`, релевантные ADR.
2. **Создать ветку:** `feat/<short-kebab-name>`, `fix/<…>`, `docs/<…>`.
3. **Сформулировать план** (5–10 шагов) и согласовать с владельцем
   до написания кода.
4. **Реализовать.** Маленькие коммиты с осмысленными сообщениями.
5. **Тесты.** Покрытие новой логики > 80%. Включая edge cases.
6. **Самопроверка:**
   - `uv run pre-commit run --all-files` — passing.
   - `uv run pytest` — passing.
   - Ручная проверка ключевых сценариев.
7. **Если архитектурное решение** — ADR в `docs/adr/`.
8. **PR** по шаблону `.github/pull_request_template.md` (что, зачем, как тестировалось, что осталось).
   CI-пайплайны живут в `.github/workflows/` — перед открытием PR убедиться, что
   они проходят локально (pre-commit + pytest).

---

## 9. Conventional Commits

Формат: `<type>(<scope>): <subject>`

Типы: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `build`, `ci`.

Примеры:

```text
feat(gedcom-parser): add ANSEL encoding auto-detection
fix(gedcom-parser): handle missing CONT line in NOTE record
docs(adr): add ADR-0003 versioning strategy
test(gedcom-parser): add fixtures for proprietary Ancestry tags
chore(deps): bump pydantic to 2.10
```

Breaking changes: `feat!:` или `BREAKING CHANGE:` в теле.

---

## 10. ADR (Architecture Decision Records)

Хранятся в `docs/adr/`. Формат — см. `docs/adr/0000-template.md`.

Каждое решение, влияющее на:

- структуру данных,
- межсервисные контракты,
- выбор технологии,
- security / privacy политику,

должно быть зафиксировано как ADR.

Список открытых решений — `ROADMAP.md` секция 24.

---

## 11. Работа с GEDCOM

- Канонический формат вход/выход — **GEDCOM 5.5.5** (см. <https://gedcom.io>).
- Обратная совместимость с 5.5.1 и проприетарными расширениями (Ancestry, MyHeritage, Geni).
- Round-trip без потерь: ввод → БД → вывод. Если потери — явный лог.
- Личный test fixture: `./Ztree.ged` (в корне репо, в `.gitignore`, **не коммитить**).
  Доступен в тестах с маркером `gedcom_real` (skipped в CI).

См. `docs/gedcom-extensions.md` для проприетарных тегов.

---

## 12. Subagents (`.claude/agents/`)

Папка `.claude/agents/` создаётся по мере появления специализированных субагентов —
сейчас её ещё нет. Запланированные роли (создавать по мере необходимости):

| Агент | Когда использовать |
|---|---|
| `gedcom-expert` | Парсер GEDCOM, проприетарные расширения, кодировки |
| `dna-analyst` | Алгоритмы кластеризации, Shared cM, endogamy |
| `db-architect` | Миграции, индексы, перформанс запросов |
| `security-reviewer` | PR с DNA / PII / authn |
| `code-reviewer` | Общий ревью |
| `test-writer` | Генерация pytest-фикстур и тестов |
| `historian` | Нормализация мест, исторические границы |
