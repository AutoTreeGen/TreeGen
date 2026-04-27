# Branch protection setup для `main`

> Этот шаг — пункт 6 ADR-0008. CI / pre-commit parity (пункты 1–5)
> уже сделана в коде, но без branch protection в GitHub UI всё ещё
> можно случайно мерджить PR с красным CI (как это произошло с PR #11).
> Документ — пошаговая инструкция для владельца репозитория.

## Зачем

Без branch protection кнопка **Merge** доступна даже когда CI красный.
ADR-0008 явно фиксирует, что мердж в `main` с красным CI запрещён —
эта настройка делает запрет **технически невозможным**, а не только
конвенцией.

## Шаги (GitHub UI)

1. Открыть **Settings → Branches** репозитория
   (`https://github.com/AutoTreeGen/TreeGen/settings/branches`).
2. В блоке **Branch protection rules** → **Add branch protection rule**.
3. **Branch name pattern:** `main`.
4. Включить:
   - ✅ **Require a pull request before merging**
     - ✅ Require approvals: `1` (можно `0`, если работаем соло —
       тогда хотя бы PR-сабмит обязателен).
     - ✅ Dismiss stale pull request approvals when new commits are pushed
       (опционально, защищает от approve-then-force-push).
   - ✅ **Require status checks to pass before merging**
     - ✅ Require branches to be up to date before merging
     - В поиск-поле статус-чеков добавить: `lint-and-test (3.13)`
       (имя job из `.github/workflows/ci.yml`; точное имя видно в
       Checks-табе любого PR).
   - ✅ **Require conversation resolution before merging** (опционально).
   - ✅ **Do not allow bypassing the above settings**
     (важно — иначе администратор может обойти правила).
   - ❌ **Allow force pushes** — НЕ включать.
   - ❌ **Allow deletions** — НЕ включать.
5. **Save changes**.

## Проверка

После сохранения — открыть любой PR с заведомо красным CI (например,
сломать ruff и запушить) и убедиться, что кнопка Merge серая, а под ней
сообщение "Required status check ... has not succeeded".

## Когда настройка падает

- Имя status check изменилось (например, после правки matrix
  `python-version`) → вернуться в Settings и обновить required check.
- Job переименован — то же самое.
- CI workflow временно вынесен в reusable workflow — required check
  теперь смотрит в другое имя.

В любом из этих случаев — обновить branch protection rule, не выключать.
