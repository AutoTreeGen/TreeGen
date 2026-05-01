# Agent #7 — Chore: CI optimization (path filters + caching + job parallelism)

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (Conventional Commits, pre-commit must pass,
   **`--no-verify` запрещён**).
2. `.github/workflows/ci.yml` — текущая конфигурация CI (главный target оптимизации).
3. `.github/workflows/` — другие workflow'ы, если есть (deploy-staging.yml и т.п.).
4. `tests/test_ci_parity.py` — это test проверяет, что списки команд в
   `scripts/check.sh`, `scripts/check.ps1` и `.github/workflows/ci.yml`
   синхронизированы. **Не сломать.**
5. `scripts/check.sh` и `scripts/check.ps1` — локальные зеркала CI.

## Задача

Текущий CI монолитный: один job `lint-and-test (3.13)` гоняет всё подряд
(uv sync --all-extras, ruff, mypy, pytest, pnpm install, biome, typecheck,
jest) для **любого** PR. Это 2-7 мин на каждый PR (включая queue + cold setup),
независимо от scope diff'а.

**Цель:** docs-only PR → CI <1 мин; code PR в одном пакете → <2 мин warm.

**Save:** ~5-10 мин per PR. На 6 PR'ов в день это **30-60 мин/день**.

## Branch

```text
chore/ci-optimization-2026-05
```

От свежего main: `git checkout main && git pull && git checkout -b chore/ci-optimization-2026-05`.

## Scope

### A. Path-filter detect job

Добавить отдельный job `detect-changes` в начале pipeline'а через
`dorny/paths-filter@v3`. Outputs: `python_changed`, `js_changed`,
`docs_changed`, `infra_changed`, `workflows_changed`.

`.github/path-filters.yml` (новый файл):

```yaml
python:
  - 'packages/**/*.py'
  - 'services/**/*.py'
  - 'infrastructure/alembic/**/*.py'
  - 'tests/**/*.py'
  - 'pyproject.toml'
  - '**/pyproject.toml'
  - 'uv.lock'
js:
  - 'apps/**/*.{ts,tsx,js,jsx}'
  - 'apps/**/package.json'
  - 'pnpm-lock.yaml'
  - 'pnpm-workspace.yaml'
  - 'biome.json'
  - 'tsconfig*.json'
docs:
  - '**/*.md'
  - 'docs/**'
infra:
  - 'infrastructure/**'
  - 'docker-compose*.yml'
  - '.env.example'
workflows:
  - '.github/workflows/**'
  - '.github/path-filters.yml'
  - 'scripts/check.{sh,ps1}'
```

### B. Job split

Разделить монолитный `lint-and-test (3.13)` на параллельные jobs, каждый
с гейтом по path-filter:

| Job | Runs if | Что делает |
|---|---|---|
| `detect-changes` | always | paths-filter → outputs |
| `lint-py` | `python_changed` OR `workflows_changed` | uv sync **selective** (см. §B.1), ruff check, ruff format --check, mypy |
| `test-py` | `python_changed` OR `workflows_changed` | uv sync **selective**, pytest -m "not slow and not integration" |
| `lint-js` | `js_changed` OR `workflows_changed` | pnpm install (см. §C.2), biome check, pnpm typecheck |
| `test-js` | `js_changed` OR `workflows_changed` | pnpm install, pnpm test |
| `docs-lint` | `docs_changed` OR everything | markdownlint, secret-detect, pre-commit на затронутых .md |
| `ci-summary` | always (depends on all above) | Aggregate status — single required check для branch protection |

**`ci-summary`** — это финальный job, который собирает результаты всех
predecessor'ов. Branch protection rule в GitHub нужно настроить на этот
job (а не на каждый sub-job по отдельности). Это позволяет path-filter'ам
work properly: skipped jobs не блокируют merge, ci-summary вернёт success
если все non-skipped jobs прошли.

> **Также проверь `.github/workflows/deploy-staging.yml`** — он тоже
> ссылается на `lint-and-test` (через `needs:` или `workflow_run`). Если
> зависит от старого job-name — обновить на новый (`ci-summary`),
> иначе deploy сломается после merge.

Pattern для ci-summary:

```yaml
ci-summary:
  needs: [lint-py, test-py, lint-js, test-js, docs-lint]
  if: always()
  runs-on: ubuntu-latest
  steps:
    - name: Check all jobs passed
      run: |
        if [[ "${{ needs.lint-py.result }}" == "failure" ]] || \
           [[ "${{ needs.test-py.result }}" == "failure" ]] || \
           [[ "${{ needs.lint-js.result }}" == "failure" ]] || \
           [[ "${{ needs.test-js.result }}" == "failure" ]] || \
           [[ "${{ needs.docs-lint.result }}" == "failure" ]]; then
          exit 1
        fi
```

### B.1 Selective uv sync per-job

Сейчас `scripts/check.{sh,ps1}` гоняет `uv sync --all-extras --all-packages`
на каждом запуске. На CI это лишнее: если изменился только `packages/ai-layer/`,
нет смысла резолвить deps для `services/parser-service/` и других пакетов.

`detect-changes` job должен **дополнительно** выдать output `changed_pkg_paths`
(JSON-array затронутых workspace member'ов). Используй это в lint-py / test-py:

```yaml
- name: Detect changed Python packages
  id: changed-py-pkgs
  run: |
    # Парсим diff, извлекаем packages/X и services/Y из изменений
    # Если detect-changes уже вернул python_changed=true,
    # читаем git diff --name-only и группируем по корневому каталогу.
    changed=$(git diff --name-only origin/${{ github.base_ref }}...HEAD \
      | grep -E '^(packages|services)/' \
      | cut -d/ -f1-2 | sort -u | jq -R . | jq -s .)
    echo "list=$changed" >> $GITHUB_OUTPUT

- name: uv sync (selective)
  run: |
    # Если ровно один пакет изменился — sync только его + transitive
    # Если несколько — fallback на --all-packages (будет редко)
    if [ $(echo '${{ steps.changed-py-pkgs.outputs.list }}' | jq 'length') -eq 1 ]; then
      pkg=$(echo '${{ steps.changed-py-pkgs.outputs.list }}' | jq -r '.[0]' | sed 's|.*/||')
      uv sync --extra dev --package $pkg
    else
      uv sync --all-extras --all-packages
    fi
```

Эффект: cold install для single-package PR ~30s вместо 60-120s.

**Не нужно** делать «changed package + dependents» dep-graph — overkill.
Path-filter granularity достаточна: если изменился `shared-models`,
работают и тесты `parser-service` (он импортирует `shared-models`) — но
detect-changes увидит это через path-filter и trigger'нет полный sync.

### C. Caching

**uv cache:**

```yaml
- name: Cache uv
  uses: actions/cache@v4
  with:
    path: |
      ~/.cache/uv
      .venv
    key: uv-${{ runner.os }}-${{ hashFiles('uv.lock', '**/pyproject.toml') }}
    restore-keys: |
      uv-${{ runner.os }}-
```

### C.2 pnpm install — frozen-lockfile + node_modules cache

`setup-node cache:'pnpm'` кеширует только pnpm **store**, не `node_modules`.
Шаг `pnpm install` всё равно должен раскатать store → node_modules. Это 10-30s
даже на warm cache.

Кешируем `node_modules` напрямую — на warm runs `pnpm install` идёт <1s
(только integrity check):

```yaml
- name: Setup pnpm
  uses: pnpm/action-setup@v4
  with:
    version: 9

- name: Setup Node
  uses: actions/setup-node@v4
  with:
    node-version: 20
    cache: 'pnpm'

- name: Cache node_modules (workspace + apps/web)
  uses: actions/cache@v4
  id: node-modules-cache
  with:
    path: |
      node_modules
      apps/*/node_modules
      packages-js/*/node_modules
    key: node-modules-${{ runner.os }}-${{ hashFiles('pnpm-lock.yaml') }}
    restore-keys: |
      node-modules-${{ runner.os }}-

- name: pnpm install
  if: steps.node-modules-cache.outputs.cache-hit != 'true'
  run: pnpm install --frozen-lockfile --prefer-offline

- name: pnpm install (verify only, cache hit path)
  if: steps.node-modules-cache.outputs.cache-hit == 'true'
  run: pnpm install --frozen-lockfile --prefer-offline --offline
```

`--frozen-lockfile` — никаких lock-mutations в CI (security + speed).
`--prefer-offline` — использует локальный store до сети.
`--offline` (на cache hit) — категорически off network → <1s.

**Cache hit ratio target:** >80% после первой недели. Cold start ~2-3 мин,
warm ~20-40 сек (node_modules cache hit + uv selective + paths-filter).

### D. `tests/test_ci_parity.py` adaptation

Этот тест может сломаться при новой структуре CI. Прочитай его, пойми что
он сравнивает, и адаптируй:

- Если он сравнивает full-text command sets → переписать на сравнение
  логических групп (lint-py === ruff+mypy в check.sh).
- Если он строится на единственном job name `lint-and-test` → обновить
  на список новых job names.

**Не удаляй тест** — паритет local/CI важен.

`scripts/check.sh` и `scripts/check.ps1` остаются монолитными (это для
local dev cycle, не CI). Не трогай их структуру.

### E. Branch protection — WARN owner

Текущий required check: `lint-and-test (3.13)`. После merge'а этого PR'а
он перестанет существовать (job переименован в `ci-summary` или подобное).

**КРИТИЧНО для PR-описания:** owner должен вручную обновить branch protection
в GitHub Settings → Branches:

1. Old required check `CI / lint-and-test (3.13)` → delete
2. New required check → `CI / ci-summary` (или whatever name выберешь)

Альтернатива через CLI (приложить в PR-описание готовую команду):

```bash
gh api -X PATCH /repos/AutoTreeGen/TreeGen/branches/main/protection/required_status_checks \
  -f 'contexts[]=CI / ci-summary'
```

**Без этого main останется без CI-gate'а** — opasно. Прописать в PR-описании
явно как post-merge action item для owner'а.

### F. Optional: bigger runner

Если бюджет позволяет — `runs-on: ubuntu-latest-4-cores` (или larger SKU)
сокращает test runtime ~2x. Это option, не requirement. Если используешь —
flag в PR-описании для cost review.

### G. Documentation

`docs/runbooks/ci-architecture.md` — новый runbook:

- Архитектура: detect → parallel jobs → ci-summary
- Кэширование: что кешируется, как инвалидируется
- Path filters: что включает каждый
- Troubleshooting: «job skipped, но я думал должен был run» — типичные кейсы
- Branch protection: одна required check, ci-summary

## Definition of Done

- [ ] `.github/workflows/ci.yml` рефакторен на split-jobs
- [ ] `.github/path-filters.yml` создан
- [ ] uv + pnpm cache настроены
- [ ] `tests/test_ci_parity.py` passes на новой структуре
- [ ] PR-описание с **explicit warning про branch protection** для owner'а
- [ ] `docs/runbooks/ci-architecture.md` написан
- [ ] Тестировал на самом PR'е этой ветки: docs-only diff (правка README) →
      должен trigger только docs-lint, остальное skipped
- [ ] Тестировал на тестовом code-only diff (после первого успешного push'а
      сделать второй commit с no-op правкой в любом Python-файле — например,
      добавить пустую строку в комментарии) → lint-py + test-py запускаются,
      lint-js + test-js skipped
- [ ] Cache hit на втором push'е (cold → warm) — measure delta

## Что НЕ трогать

- `packages/`, `services/`, `apps/` — все 10.9a-зоны (#1-#4)
- `infrastructure/alembic/`, `infrastructure/terraform/k8s/monitoring/` — #1, #6
- `.gitignore` — уже почистил #5
- `docs/adr/` — кроме `docs/runbooks/ci-architecture.md` (новый)
- `ROADMAP.md` — #6
- `scripts/check.sh` / `scripts/check.ps1` — local dev mirror, оставить как есть

## Подводные камни

1. **Skipped vs failed.** Skipped job = success в GitHub Actions context,
   но без `if: always()` зависимый job не запустится. ci-summary должен
   быть `if: always()` иначе caching strategy не сработает.
2. **`detect-changes` для push на main.** Когда merge happens, detect-changes
   на push event может не иметь base для сравнения — добавить fallback
   `if no base, run all`.
3. **Schedule events** (если есть nightly CI) — path-filter не работает на
   `schedule:`, нужно overrride `if: github.event_name != 'schedule'`.
4. **uv.lock cache key.** Если ИЛИ python_changed ИЛИ workflows_changed
   trigger'ят lint-py, кеш-key должен включать оба, иначе stale cache.
5. **Branch protection migration.** Не делай force-push после merge —
   owner может оказаться без required check на главной ветке. Сначала
   merge, потом owner правит protection.
6. **Concurrency groups.** Добавь `concurrency: ci-${{ github.ref }}` для
   cancel-in-progress на новые push'и в тот же PR — экономит минуты.

## Conventional Commits шаблоны

```text
chore(ci): split lint-and-test into parallel jobs with path filters
chore(ci): add uv and pnpm caching (cold→warm runtime ~70% faster)
chore(ci): add ci-summary aggregator for branch protection
test(ci): adapt test_ci_parity to new job structure
docs(runbook): add ci-architecture runbook
```

## Acceptance metrics для PR-описания

Замерить и приложить в PR description:

| Сценарий | До | После cold | После warm |
|---|---|---|---|
| Docs-only PR (markdown change) | ~2-7 мин | ? | ? |
| Single-package Python change | ~2-7 мин | ? | ? |
| Frontend-only change | ~2-7 мин | ? | ? |
| Full-stack change (Python + JS) | ~2-7 мин | ? | ? |

(значения «До» взяты из реальных данных PR #161 = 2m17s job + queue ≈ 5 мин total wall-clock).

Цель — все docs-only ≤ 1 мин, single-pkg ≤ 2 мин warm.
