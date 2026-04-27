# Agent brief — Finalize Phase 3 + bootstrap ADR-0008

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** работаем на Windows, `D:\Projects\TreeGen`, ветка main по умолчанию.
> Перед стартом обязательно прочитай: `CLAUDE.md`, `ROADMAP.md`,
> `docs/adr/0008-ci-precommit-parity.md`.

---

## Текущее состояние (что уже сделано)

В main за последний день смерджены:

| PR | Что | Статус |
|---|---|---|
| #3 | `feat(phase-3)`: parser-service FastAPI scaffold | merged (CI был красный — pre-existing drift) |
| #7 | `fix(ci)`: ruff cleanup, per-file ignores | merged |
| #8 | `style`: apply ruff format | merged |
| #9 | `docs(adr)`: ADR-0008 — CI/pre-commit parity | merged |
| #10 | `fix(ci)`: pytest conftest collision (`--import-mode=importlib`, удалены пустые `__init__.py`) | merged |

**Открытая ветка:** `feat/phase-3.1-events-import` — стек поверх старой
`feat/phase-3-parser-service` (которая уже в main). Содержит:

- `import_runner` теперь раскладывает GEDCOM events в `events` +
  `event_participants` (bulk INSERT, audit-skip).
- Bug fixes: `register_audit_listeners` once-per-process,
  `DATABASE_URL` override в conftest, ORM kwargs alignment,
  `/imports` сохраняет оригинальное имя файла.
- 9/9 тестов локально зелёные.
- `docs/pr-templates/phase-3.1.md` — готовое тело PR.

**CI на main всё ещё красный** — последний фейл (post-merge на #9):

```text
ModuleNotFoundError: No module named 'httpx'
services/parser-service/tests/test_healthz.py:6
```

`httpx` нужен для `ASGITransport` в тестах parser-service, но не объявлен
в test deps.

**Также в логе CI:** `platform linux -- Python 3.14.4` — подозрительно для
проекта на Python 3.13. Возможно `actions/setup-uv` ставит latest вместо
matrix-version. Проверить и зафиксировать.

---

## Задачи (в этом порядке)

### Task 1 — fix(ci): httpx test dep + Python version pin

**Цель:** main CI наконец-то зелёный.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b fix/ci-httpx-and-python-version`
3. Добавить `httpx>=0.27` в test deps. Проверь как у `shared-models` сделано
   (`[dependency-groups] dev` или `[project.optional-dependencies] test`),
   повтори тот же паттерн в `services/parser-service/pyproject.toml`.
4. Проверить `.github/workflows/ci.yml`:
   - Если `python-version` в matrix указан как `"3.12"` или
     `["3.12", "3.13"]` — заменить на `"3.13"` (проект на 3.13 per
     Dockerfile + CLAUDE.md).
   - Если `astral-sh/setup-uv` не закреплён — закрепить версию.
   - Возможно нужен явный `actions/setup-python@v5` с
     `python-version: "3.13"` ДО `setup-uv`.
5. `uv lock`
6. `uv sync --all-packages` (или `uv sync` — то что используется в CI)
7. `uv run pytest -m "not gedcom_real and not integration"` →
   должно быть green локально
8. `git add -A && git commit -m "fix(ci): add httpx test dep + pin Python 3.13"`
   (БЕЗ `--no-verify` — pre-commit должен пройти)
9. `git push -u origin fix/ci-httpx-and-python-version`
10. `gh pr create --title "fix(ci): add httpx test dep + pin Python 3.13" \
        --base main --head fix/ci-httpx-and-python-version \
        --body "Закрывает последнюю красную CI ошибку перед Phase 3.1."`
11. **Дождись зелёного CI на этом PR**. Если красный — итерация.
12. После approve мерджа — переходи к Task 2.

### Task 2 — feat(phase-3.1): rebase + open PR-3.1

**Цель:** events импорт в main, Phase 3.1 закрыта.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout feat/phase-3.1-events-import`
3. Старая база: `feat/phase-3-parser-service` (уже в main).
   Ребейз:

   ```bash
   git rebase --onto main feat/phase-3-parser-service
   ```

4. Конфликты вероятны в `services/parser-service/src/parser_service/services/import_runner.py`
   и `pyproject.toml` (после Task 1 там `httpx`, в 3.1 могут быть свои
   правки). Резолвь:
   - `import_runner.py` — взять обе стороны (3.1 logic + сохранить
     существующее).
   - `pyproject.toml` — взять main версию (с httpx + python pin),
     сверху наложить 3.1 правки если есть.
5. `uv lock` (если pyproject.toml менялся)
6. `uv run pytest -m "not gedcom_real"` → 100% green ожидается
   (включая parser-service integration, потому что 3.1 чинит баги).
7. `git push --force-with-lease`
8. `gh pr create --title "feat(phase-3.1): import GEDCOM events" \
        --base main --head feat/phase-3.1-events-import \
        --body-file docs/pr-templates/phase-3.1.md`
9. Дождись зелёного CI. Если красный — итерация.
10. После approve мерджа — переходи к Task 3.

### Task 3 — chore(ci): implement ADR-0008 (CI/pre-commit parity)

**Цель:** реализовать решение из ADR-0008 пункт за пунктом, чтобы
больше никогда не было серии 5 мини-PR из-за `--no-verify` и
расхождения pre-commit ↔ CI.

**Прочитай:** `docs/adr/0008-ci-precommit-parity.md` секция «Что нужно
сделать в коде» (6 пунктов).

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b chore/ci-parity-implementation`
3. Создать `scripts/check.ps1` (Windows) и `scripts/check.sh` (Linux/macOS):
   обёртки над **всеми** CI-командами из `.github/workflows/ci.yml`
   job'а `lint-and-test`. Минимум:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy .
   uv run pytest -m "not slow and not integration and not gedcom_real" \
       --cov --cov-report=xml --cov-report=term
   ```

4. Расширить `.pre-commit-config.yaml`:
   - Добавить `ruff-format` хук (`args: [--check]`).
   - Убедиться что хуки в сумме < 5 сек на чистом репо.
   - Pre-commit НЕ должен запускать mypy/pytest (они в `scripts/check.*`).
5. Написать `tests/test_ci_parity.py`:
   - Парсит `.github/workflows/ci.yml`, извлекает `run`-команды job
     `lint-and-test`.
   - Парсит `scripts/check.sh`, извлекает команды.
   - `assert set(workflow_commands) == set(check_script_commands)`.
   - Игнорировать тривиальные шаги (checkout, setup-python, setup-uv,
     uv sync, codecov upload).
6. Зафиксировать в `pyproject.toml` `[tool.pytest.ini_options]`:

   ```toml
   addopts = "--import-mode=importlib --cov --cov-report=xml --cov-report=term"
   markers = [
       "slow: медленные тесты",
       "integration: требуют docker-compose сервисов",
       "gedcom_real: используют реальные GED-файлы (skipped в CI)",
       "db: требуют живой БД",
   ]
   ```

   (если уже есть из PR-10 — оставить, дополнить markers).

7. Обновить `CLAUDE.md` §6 «Стандарты качества» — добавить:
   - `--no-verify` запрещён. Если pre-commit падает — чинить причину,
     не bypass'ить.
   - Перед `git push` обязательно `pwsh scripts/check.ps1` (Windows)
     или `bash scripts/check.sh` (Linux/macOS).
8. Прогнать всё локально:

   ```bash
   pwsh scripts/check.ps1   # должно быть зелёное
   uv run pre-commit run --all-files   # тоже зелёное, < 5 сек
   uv run pytest tests/test_ci_parity.py -v   # green
   ```

9. Commit (БЕЗ `--no-verify`!), push, PR `chore/ci-parity-implementation`.
10. **Не настраивать branch protection через gh** — это сделает владелец
    вручную через GitHub UI после мерджа PR (Settings → Branches → main →
    Require status checks: `lint-and-test`).

### Task 4 (опционально) — chore(ci): branch protection memo

Создать `docs/runbooks/branch-protection-setup.md` — пошаговое руководство
для владельца (как зайти в Settings → Branches → main, какие галочки
поставить). Это не код, но снижает риск что branch protection забудут
включить.

---

## Что НЕ делать

- ❌ `git commit --no-verify` — даже один раз. Если pre-commit падает,
  чини причину.
- ❌ Мержить PR с красным CI. Каждый PR должен быть зелёным до merge.
- ❌ Трогать `main` напрямую (commit без PR).
- ❌ Создавать ADR без согласования. ADR-0008 уже есть, его реализуй.
- ❌ Phase 3.2 (places + multi-principal participants) — это отдельная
  фаза, не в этом скоупе.
- ❌ Скрейпинг архивов / ДНК / auth — за рамками Phase 3.x.

---

## Сигналы успеха

После всех 3 (или 4) PR должно быть:

1. ✅ Самый верхний CI run на main — зелёный.
2. ✅ `feat/phase-3-parser-service` ветка удалена (после Task 2).
3. ✅ ROADMAP §7.0 обновлён: 3-A done, 3.1 done.
4. ✅ `pwsh scripts/check.ps1` локально зелёный за < 60 сек.
5. ✅ `--no-verify` упоминается в CLAUDE.md §6 как запрещённый.

Если что-то из этого красное — итерируй до зелёного, не оставляй на потом.

---

## Если застрял

- Конфликт rebase разрешить не получается → закоммить текущий резолюшн,
  опиши проблему в PR description, попроси владельца ревью до push'а.
- CI красный на чём-то новом и непонятном → НЕ мержи, открой issue с
  логом, напиши в чат.
- Подозрение на неправильный ADR → лучше остановиться и переспросить,
  чем внести расхождение между ADR и реализацией.

Удачи. Жду PR-ссылок и финального «main green» апдейта.
