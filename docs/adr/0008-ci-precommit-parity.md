# ADR-0008: CI / pre-commit parity и запрет `--no-verify`

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `ci`, `quality`, `process`, `dx`

## Контекст

В течение Phase 3 (день мерджа PR-3, PR-7, PR-8 и `fix/pytest-importlib`)
четыре мини-PR подряд понадобились только для починки CI на main. Корневая
причина не в самих ошибках (они тривиальны: `ruff format`, неиспользованные
импорты, `pytest` import-mode collision), а в **процессе**:

1. Phase 3 коммиты делались с `git commit --no-verify`, потому что pre-commit
   локально не дожимал из-за побочных проблем (Cyrillic в `alembic.ini`,
   медленные хуки). `--no-verify` стал привычным шорткатом.
2. Pre-commit и CI **проверяют разный набор**: pre-commit гонял `ruff check`,
   но не `ruff format --check`, `mypy` падал в CI с `no-any-return`, который
   локально молчал, `pytest --import-mode=importlib` нужен был только в CI.
3. Никакой fail-fast перед `git push` — сломанный билд обнаруживался уже
   на GitHub, после открытия PR.

Стоимость одного дня: 4 PR × {ветка → push → CI круг → ревью → мердж} вместо
1 правильного PR. И главное — Phase 3 PR-1 был замёрджен **с красным CI
(0/2 checks passed)**, что нормализует игнорирование сигналов.

## Рассмотренные варианты

### Вариант A — Статус-кво

Оставить как есть: pre-commit «по мере удобства», `--no-verify` разрешён,
CI ловит то, что просочилось.

- ✅ Минимум трения для одиночного разработчика.
- ❌ Каждая Phase будет начинаться с серии CI-fix PR. Тренд уже виден.
- ❌ Нормализуется мердж с красным CI → теряется доверие к зелёному.
- ❌ При появлении второго контрибьютора порог входа ломается.

### Вариант B — Pre-commit полностью зеркалит CI

В pre-commit добавить весь набор CI-чеков: `ruff check`, `ruff format --check`,
`mypy --strict`, `pytest -m "not slow and not integration"`.

- ✅ Локально и в CI всегда одно и то же.
- ❌ Pre-commit на каждый `git commit` становится **минуты**, а не секунды
  (mypy на всём workspace + pytest на 300+ unit-тестах). Итог — `--no-verify`
  как обходной путь возвращается.
- ❌ Mypy + pytest имеют смысл перед push'ем, не перед каждым commit'ом.

### Вариант C — Двухуровневый: pre-commit (быстро) + pre-push / `make check` (полно) + запрет `--no-verify`

- **Pre-commit hooks (быстро, < 5 сек):** `ruff check`, `ruff format --check`,
  `markdownlint`, `detect-secrets`, end-of-file-fixer, trailing-whitespace.
- **Pre-push hook ИЛИ `uv run make check` ИЛИ `scripts/check.ps1`
  (полно, < 60 сек):** добавляется `mypy` и `pytest -m "not slow and not
  integration and not gedcom_real"`.
- **Запрет `--no-verify`:** в commit-msg или pre-commit hook прописана
  проверка-«honor» (если коммит сделан с `--no-verify`, последующая команда
  `git push` всё равно прогонит проверки и упадёт). Альтернативно — соглашение
  - код-ревью.
- ✅ Быстрые `git commit` (секунды), полная гарантия перед `git push`.
- ✅ Чёткое разделение: «быстро / полно».
- ✅ CI становится последним рубежом, а не первым местом обнаружения ошибок.
- ❌ Нужно поддерживать pre-push hook или `make check` синхронно с CI YAML.

### Вариант D — Запрет `--no-verify` без двухуровневой системы

Только запретить `--no-verify` (соглашением + branch protection). Pre-commit
оставить как есть.

- ✅ Минимум работы.
- ❌ Не решает проблему различий pre-commit ↔ CI. Что pre-commit не проверяет,
  всё равно прорвётся.

## Решение

Выбран **Вариант C**.

Pre-commit становится «быстрым гейтом» (< 5 сек, не раздражает на каждый
commit), `scripts/check.ps1` (Windows) и `scripts/check.sh` (Linux/macOS)
становятся «полным гейтом» (< 60 сек, обязателен перед `git push`). Список
команд внутри `check.*` **должен буквально совпадать** с шагами CI workflow
(`.github/workflows/ci.yml`) — это инвариант, его проверяет отдельный тест.

`--no-verify` запрещён конвенцией (CONTRIBUTING + CLAUDE.md). На branch
protection main стоит требование «CI must pass» — мердж в main с красным CI
технически невозможен.

## Последствия

**Положительные:**

- Красные CI исчезают на ровном месте: всё, что упадёт в CI, уже упадёт
  локально перед push'ем.
- Phase 4+ начинается с чистого CI baseline, нет накопленного долга.
- Чёткий contract для AI-агентов и будущих контрибьюторов: «`uv run
  scripts/check.ps1` зелёный → можно push'ить».

**Отрицательные / стоимость:**

- ~30 минут разовая работа: создать `scripts/check.{ps1,sh}`, расширить
  `.pre-commit-config.yaml` (добавить `ruff format --check`, оставить fast),
  обновить CLAUDE.md секцию 6, написать тест `tests/test_check_script_matches_ci.py`,
  включить branch protection на main.
- Каждый `git push` теперь медленнее на ~60 сек локально.
- Если CI workflow обновится без обновления `check.*` — тест упадёт, что
  и есть нужное поведение.

**Риски:**

- Соблазн обойти `check.ps1` через `git push --force-with-lease` без проверки.
  Mitigation: branch protection на main + код-ревью PR.
- Тест-инвариант `check_script_matches_ci` хрупок к косметическим правкам
  workflow. Mitigation: парсим YAML, сравниваем только реальные `run`-команды
  job'а `lint-and-test`.

**Что нужно сделать в коде:**

1. `scripts/check.ps1` + `scripts/check.sh` — обёртки над всеми CI-командами.
2. `.pre-commit-config.yaml` — добавить `ruff-format` (с `args: [--check]`),
   убедиться что fast-чеки занимают < 5 сек.
3. `tests/test_ci_parity.py` — парсит `.github/workflows/ci.yml` job
   `lint-and-test`, парсит `scripts/check.ps1`, сравнивает множество команд.
4. `pyproject.toml` — `[tool.pytest.ini_options]` фиксирует
   `addopts = "--import-mode=importlib"` и регистрирует все маркеры.
5. `CLAUDE.md` §6 — обновить «Стандарты качества»: добавить «`--no-verify`
   запрещён», «перед `git push` обязательно `scripts/check.ps1`».
6. GitHub → Settings → Branches → main → Require status checks (`lint-and-test`).

Эти задачи трекаются в Phase 3.x как `chore(ci): enforce parity`.

## Когда пересмотреть

- Если `scripts/check.*` становится медленнее 2 минут — пересмотреть, что
  именно гонять локально (возможно, выделить ещё один уровень «pre-merge»).
- Если появится >1 разработчика — пересмотреть, нужны ли pre-push hooks
  через `husky`-подобный механизм (сейчас это просто скрипт + соглашение).
- При переходе CI с GitHub Actions на другой runner — обновить парсер
  workflow в тесте.

## Ссылки

- Связанные ADR: ADR-0001 (tech-stack — ruff/mypy/pytest как стандарт),
  ADR-0002 (monorepo-structure — почему сразу несколько `tests/conftest.py`).
- PR-7 (`fix/ci-ruff-cleanup`), PR-8 (`fix/ruff-format`),
  `fix/pytest-importlib` — три мини-PR, мотивировавшие этот ADR.
- pre-commit docs: <https://pre-commit.com/#pre-push>
