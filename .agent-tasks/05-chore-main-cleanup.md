# Agent #5 — Chore: pre-existing main breakage + ADR-0057 collision + dev-junk gitignore + worktree/stash audit

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (RU comments, EN identifiers, Conventional Commits,
   biome для TS/JSX, markdownlint, pre-commit must pass, **`--no-verify` запрещён**).
2. `docs/adr/README.md` — формат ADR.
3. Pre-existing failures (выявлены 01.05 при `uv run pre-commit run --all-files`):
   - `apps/landing/src/components/waitlist-form.tsx:159,174` — biome
     `lint/a11y/noLabelWithoutControl`
   - `docs/adr/0003-versioning-strategy.md:93,118` — markdownlint
     `MD040/fenced-code-language`
   - `scripts/{migrate_drive_full,commit_pre_migration,migrate_to_drive,check,setup}.ps1` —
     mixed line endings (CRLF)
4. `docs/feature_voice_to_tree.md` §10 — TODO про коллизию трёх ADR-0057
   (контекст: `ai-hypothesis-explanation`, `inference-engine-v2-aggregation`,
   `mobile-responsive-design-system`).

## Задача

Пять независимых cleanup'ов в одной PR-ветке. Все мелкие, low-risk, разгребают
debt накопленный к 01.05.

## Branch

```text
chore/main-cleanup-may-2026
```

От свежего main: `git checkout main && git pull && git checkout -b chore/main-cleanup-may-2026`.

## Scope

### A. Fix biome a11y в `apps/landing/src/components/waitlist-form.tsx`

Lines 159 и 174: `<label>` оборачивает shadcn/ui `<Checkbox>`, но biome не
видит `<Checkbox>` как `<input>`. Два способа починить:

1. **Preferred:** добавить `id` к `<Checkbox>` и `htmlFor` к `<label>`:

   ```tsx
   <label htmlFor="consent-marketing" className="...">
     <Checkbox id="consent-marketing" ... />
     <span>...</span>
   </label>
   ```

2. **Альтернатива:** обернуть в `<div role="presentation">` и использовать
   `aria-labelledby` — менее чистый паттерн, выбирать только если #1 ломает
   стилинг.

**Не меняй UX**: проверь визуально (`pnpm -F landing dev`), что чекбокс
по-прежнему кликается через клик на текст рядом.

Тесты — если есть `apps/landing/src/components/__tests__/waitlist-form.test.tsx`,
обнови; если нет — не создавай (этот компонент исторически без unit-tests).

### B. Fix markdownlint MD040 в `docs/adr/0003-versioning-strategy.md`

Lines 93 и 118 — fenced code blocks без language. Прочитай контекст вокруг
каждого:

- Если внутри SQL-снапшоты или DDL → ```sql
- Если ASCII-диаграммы / pseudo-code → ```text
- Если bash-команды → ```bash

Не угадывай — открой файл, посмотри что внутри, поставь правильный тег.

### C. Normalize CRLF → LF в 5 PowerShell-скриптах

Файлы:

- `scripts/migrate_drive_full.ps1`
- `scripts/commit_pre_migration.ps1`
- `scripts/migrate_to_drive.ps1`
- `scripts/check.ps1`
- `scripts/setup.ps1`

PS-скрипты должны работать с LF (PowerShell ≥ 5 LF-tolerant). Конвертируй
content (без изменений в логике скриптов). Проверь, что hook
`mixed-line-ending` после твоих правок passes.

После: запусти `tests/test_ci_parity.py` — он сверяет, что списки команд
в `check.sh` и `check.ps1` совпадают с `.github/workflows/ci.yml`. Не должен
сломаться от CRLF→LF, но проверь на всякий случай.

### D. `.gitignore` patch — dev junk

Текущий untracked-мусор в корне репо (видно через `git status`):

- `dev.log.cd`
- `install-and-dev.bat`
- `just-install.bat`
- `rebuild-step1-restore.bat`
- `scripts/auto-merge.ps1`

Добавь в `.gitignore` секцию (создай если нет) `# ---- Локальные dev-скрипты`:

```gitignore
# ---- Локальные dev-скрипты (не для коммита) ----
*.log.cd
install-and-dev.bat
just-install.bat
rebuild-step1-restore.bat
scripts/auto-merge.ps1
```

**НЕ удаляй сами файлы** — они могут быть полезны owner'у локально. Только
gitignore.

**ОТДЕЛЬНЫЙ вопрос:** `apps/landing/src/components/{providers,theme-toggle}.tsx`
— тоже untracked. Спроси у owner'а в PR-описании: «keep or delete?» — это
остатки от feat/landing-rebuild ветки, может быть случайным мусором.

### E. ADR-0057 collision rename

Три файла с одним номером:

- `docs/adr/0057-ai-hypothesis-explanation.md` — **KEEP** (опубликованный,
  ссылается ADR-0064 + другие)
- `docs/adr/0057-inference-engine-v2-aggregation.md` — **RENAME → 0065**
- `docs/adr/0057-mobile-responsive-design-system.md` — **RENAME → 0066**

После #1+#2+#3+#4 могут сесть номера 0067+, но на момент твоего PR
это зарезервировано — координируй через коммит-сообщение «reserves 0065 and
0066».

Шаги для каждого rename:

1. `git mv docs/adr/0057-X.md docs/adr/006N-X.md`
2. Обнови заголовок `# ADR-0057: ...` → `# ADR-006N: ...`
3. Добавь note под заголовком:

   > **Note:** изначально опубликован как ADR-0057, перенумерован 01.05.2026
   > для разрешения коллизии трёх ADR-0057. Внутренние ссылки сохраняются;
   > внешние upstream-ссылки на 0057-имя файла будут битыми.

4. Найди все cross-references (`grep -r "ADR-0057" docs/ packages/ services/ apps/`)
   и обнови — но **только для двух перенумерованных** ADR. References на
   `ai-hypothesis-explanation` (KEEP) не трогай.
5. `docs/adr/README.md` (если есть индекс) — обнови.

### F. Worktree + stash audit (REPORT only — не модифицировать)

Запусти на хосте (через Bash tool):

```bash
git worktree list
git stash list --pretty=format:"%gd | %ci | %gs"
```

Сформируй отчёт `docs/reports/worktree-stash-audit-2026-05-01.md`:

- Список **всех** prunable/active worktrees из вывода (что есть, то и есть —
  не закладывайся на конкретное число) с индикацией: branch merged? branch
  deleted? recommend prune/keep
- Список **всех** stashes из вывода с timestamp + branch + recommendation:
  drop/keep
- В конце — раздел «Recommended cleanup commands» — готовый script для
  owner'а (но не выполняй сам).

Это **отчёт**, не cleanup. Owner запустит prune/drop вручную после ревью.

## Definition of Done

- [ ] §A: biome `pnpm -F landing lint` — **passes** на waitlist-form
- [ ] §B: `uv run pre-commit run markdownlint --files docs/adr/0003-versioning-strategy.md` — passes
- [ ] §C: `uv run pre-commit run mixed-line-ending --files scripts/*.ps1` — passes
- [ ] §C: `uv run pytest tests/test_ci_parity.py` — passes
- [ ] §D: `git status --short` после твоих правок — clean от dev-junk
- [ ] §E: 2 ADR перенумерованы, cross-refs обновлены, `find docs -name "0057-*"`
      возвращает 1 файл (только KEEP)
- [ ] §F: report.md написан, recommended commands — корректный PowerShell/bash
- [ ] **`uv run pre-commit run --all-files` — все hooks PASS** (это критерий — main теперь чистый)
- [ ] PR-описание перечисляет все 6 sub-tasks (A-F) с статусом

## Что НЕ трогать

- Все зоны Phase 10.9a: `packages/{shared-models,ai-layer}`,
  `services/parser-service`, `apps/web/src/components/voice/`,
  `apps/web/messages/{en,ru}.json` (namespace `voice.*`),
  `infrastructure/alembic/versions/0030*`
- `docs/adr/0064-voice-to-tree-pipeline.md` — это другой ADR
- ROADMAP.md — закрыто #6

## Подводные камни

1. **biome a11y fix** может потребовать изменения структуры JSX. Если
   `<Checkbox>` impl от shadcn делает что-то нестандартное (e.g.,
   `forwardRef` без `id`-prop), посмотри как `id` пробрасывается. Возможно
   придётся wrap в `<div>` с aria.
2. **CRLF → LF** на Windows — git может само нормализовать через
   `.gitattributes`. Проверь: если в `.gitattributes` уже `*.ps1 text eol=lf`,
   то pre-commit hook'у не нужно править — спроси owner'а почему он
   re-сurfaced.
3. **ADR-0057 cross-refs.** Их два типа: (a) ссылки на ADR-номер в тексте
   («см. ADR-0057» — обнови), (b) markdown-link на файл («[ADR-0057](./0057-X.md)»
   — обнови путь). Используй `grep -rn "ADR-0057\|0057-inference\|0057-mobile"
   docs/ packages/ services/ apps/`.
4. **Worktree pune commands.** В отчёте давай команды через `if (Test-Path ...)`
   чтобы owner мог dry-run'нуть. Не делай `Remove-Item -Force -Recurse`
   без подтверждения.

## Conventional Commits шаблоны

```text
fix(web): biome a11y noLabelWithoutControl in waitlist-form (consent checkboxes)
docs(adr): add language tags to fenced blocks in 0003-versioning-strategy
chore(scripts): normalize ps1 line endings to LF
chore: ignore dev-only scripts (.bat, dev.log.cd, auto-merge.ps1)
docs(adr): rename two ADR-0057 collisions to 0065 and 0066
docs(reports): add worktree + stash audit (2026-05-01)
```

Можно squash в 1-2 коммита если чувствуешь, что review проще таким.
