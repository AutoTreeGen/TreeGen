# Worktree + stash audit — 2026-05-01

> **Status:** Report only. Cleanup commands at the bottom — owner runs after review.

Snapshot taken from `F:\Projects\TreeGen` repo. 38 worktrees registered, 11 stashes.
Categorisation logic:

- **PRUNE:** branch points at a commit reachable from `main` *or* matches a phase
  already squash-merged into `main` (commit `(#NN)` line found in `git log main`).
- **KEEP:** active phase work, in-flight PR, or system worktree (canonical / `main`).
- **REVIEW:** ambiguous — open it and decide.

## Worktrees

| Path | Branch | Phase / PR in main | Recommendation |
|---|---|---|---|
| `F:\Projects\TreeGen` | `docs/phase-10-9-voice-to-tree-spec` | PR #160 in flight | **KEEP** (canonical, active spec PR) |
| `F:\Projects\TreeGen-bg-adr` | `main` | — | **KEEP** (background `main` worktree) |
| `F:\Projects\TreeGen-wt\chore-main-cleanup-may-2026` | `chore/main-cleanup-may-2026` | this PR | **KEEP** (active work) |
| `F:\Projects\TreeGen-wt\chore-frontend-fixes` | `chore/web-lockfile-and-snapshot` | #134 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\docs-adr-evidence-tiers` | `docs/adr-evidence-tiers` | merged into main | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-10-0-ai-layer` | `feat/phase-10-0-ai-layer-skeleton` | #130 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-10-1-explanation` | `feat/phase-10.1-ai-hypothesis-explanation` | #152 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-10-2a-ai-source` | `feat/phase-10-2a-ai-source-extraction` | #147 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-10-2b-ai-source-vision` | `feat/phase-10-2b-ai-source-vision` | #157 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-10-3-ai-normalization` | `feat/phase-10-3-ai-normalization` | #153 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-10-9a-web-ui` | `feat/phase-10-9a-web-ui` | parallel agent #11 (in flight) | **KEEP** |
| `F:\Projects\TreeGen-wt\phase-11-1-sharing-ui` | `feat/phase-11.1-sharing-ui` | #151 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-11-2-public-share` | `feat/phase-11-2-public-share` | #138 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-12-0-stripe` | `feat/phase-12-0-stripe-billing` | #129 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-13-1a-foundation` | `feat/phase-13-1a-foundation` | #128 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-13-1b-sentry` | `feat/phase-13-1b-sentry-services` | #139 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-13-1c-monitoring` | `feat/phase-13-1c-monitoring` | #133 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-13-2-security` | `feat/phase-13-2-security-hardening` | #142 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-14-1-telegram-cmds` | `feat/phase-14-1-telegram-commands` | #140 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-14-2-telegram-digest` | `feat/phase-14.2-telegram-digest` | #149 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-15-1-evidence-panel` | `feat/phase-15-1-evidence-panel` | #155 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-15-4a-proposals-data-model` | `feat/phase-15-4a-proposals-data-model` | not in main log | **REVIEW** (open PR? or abandoned?) |
| `F:\Projects\TreeGen-wt\phase-4-10b-account-settings` | `feat/phase-4-10b-account-settings` | #122 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-4-11a-gdpr` | `feat/phase-4-11a-gdpr-export` | #132 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-4-11b-erasure` | `feat/phase-4-11b-erasure-worker` | #135 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-4-11c-ownership-transfer` | `feat/phase-4-11c-ownership-transfer` | #136 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-4-13b-i18n-rollout` | `feat/phase-4-13b-i18n-full` | #141 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-4-14a-mobile` | `feat/phase-4-14a-mobile-responsive` | #145 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-4-15-onboarding` | `feat/phase-4-15-onboarding-tour` | #143 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-5-5a-quarantine-roundtrip` | `feat/phase-5-5a-quarantine-roundtrip` | #156 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-6-4-dna-triangulation` | `feat/phase-6.4-dna-triangulation` | #148 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-6-4-person-merge-ui` | `feat/phase-6-4-person-merge-ui` | #131 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-6-5-tree-stats` | `feat/phase-6-5-tree-stats` | #137 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-6-7a-clusters` | `feat/phase-6-7a-autoclusters-data-and-leiden` | #159 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-7-4-hypothesis-queue` | `feat/phase-7-4-hypothesis-review` | branch reachable from main (`git branch --merged`) | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-7-5-inference-v2` | `feat/phase-7-5-inference-confidence-v2` | #146 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-9-0-familysearch` | `feat/phase-9.0-familysearch-adapter` | #150 merged | **PRUNE** |
| `F:\Projects\TreeGen-wt\phase-9-1-wikimedia` | `feat/phase-9-1-wikimedia-commons-adapter` | not in main log | **REVIEW** (open PR? or abandoned?) |
| `F:\Projects\TreeGen-wt\phase-9-2-jewishgen` | `feat/phase-9-2-jewishgen-integration` | not in main log | **REVIEW** (open PR? or abandoned?) |

**Summary:** 30 PRUNE, 5 KEEP (canonical / `main` / active 10.9a / spec / this PR),
3 REVIEW (15.4a, 9.1, 9.2 — phases not on main yet).

## Stashes

| Ref | Date | Branch when stashed | Message | Recommendation |
|---|---|---|---|---|
| `stash@{0}` | 2026-05-01 01:18 | `feat/landing-rebuild` | `phase-5.5a-setup-stash: uncommitted landing changes` | **REVIEW** (today, may contain useful landing WIP — owner check) |
| `stash@{1}` | 2026-04-30 21:15 | `main` | `phase-9-2-pre-worktree` | **REVIEW** (1 day old, related to 9.2 setup which is still in flight) |
| `stash@{2}` | 2026-04-28 22:01 | `feat/phase-4-10b-account-settings` | `phase-4.10b WIP before rebase v2` | **DROP** (4.10b shipped via #122) |
| `stash@{3}` | 2026-04-28 21:55 | `feat/phase-4-10b-account-settings` | `phase-4.10b WIP before rebase` | **DROP** (4.10b shipped via #122) |
| `stash@{4}` | 2026-04-28 15:14 | `feat/phase-3.5-arq-worker` | `WIP on feat/phase-3.5-arq-worker` | **DROP** (3.5 shipped via #101+#103+#104) |
| `stash@{5}` | 2026-04-28 15:11 | `feat/phase-3.5-import-progress-ui` | `phase-3.5-WIP-from-parallel-agent` | **DROP** (3.5 shipped) |
| `stash@{6}` | 2026-04-27 13:27 | `feat/phase-3.3-sources-import` | `agent-foreign-changes-during-task4-merge` | **DROP** (3.3 long shipped) |
| `stash@{7}` | 2026-04-27 12:34 | `feat/phase-3.2-places-import` | `task3-staged` | **DROP** (3.2 long shipped) |
| `stash@{8}` | 2026-04-27 12:33 | `feat/phase-4.1-persons-list` | `agent2-foreign-changes-during-task1` | **DROP** (4.1 long shipped) |
| `stash@{9}` | 2026-04-27 12:20 | `feat/phase-4.1-persons-list` | `agent1-wip-phase4.1-persons-list` | **DROP** (4.1 long shipped) |
| `stash@{10}` | 2026-04-27 10:34 | `feat/phase-3.1-events-import` | `spurious LF/CRLF in scripts` | **DROP** (this is the very CRLF artifact `chore/main-cleanup-may-2026 §C` resolves) |

**Summary:** 9 DROP, 2 REVIEW.

## Recommended cleanup commands

> **Не запускать сходу.** Запустить сначала dry-run-блок ниже, проверить вывод,
> потом запустить prune-блок. У владельца репо свой воркфлоу — отчёт даёт
> готовые команды, не выполняет.

### Dry-run check (PowerShell, проверить что всё на месте)

```powershell
# 1. Перечитать список worktrees, убедиться что нет в-пути коммитов
git worktree list

# 2. Для каждого PRUNE-worktree проверить, что нет uncommitted работы
$prune = @(
  "F:\Projects\TreeGen-wt\chore-frontend-fixes",
  "F:\Projects\TreeGen-wt\docs-adr-evidence-tiers",
  "F:\Projects\TreeGen-wt\phase-10-0-ai-layer",
  "F:\Projects\TreeGen-wt\phase-10-1-explanation",
  "F:\Projects\TreeGen-wt\phase-10-2a-ai-source",
  "F:\Projects\TreeGen-wt\phase-10-2b-ai-source-vision",
  "F:\Projects\TreeGen-wt\phase-10-3-ai-normalization",
  "F:\Projects\TreeGen-wt\phase-11-1-sharing-ui",
  "F:\Projects\TreeGen-wt\phase-11-2-public-share",
  "F:\Projects\TreeGen-wt\phase-12-0-stripe",
  "F:\Projects\TreeGen-wt\phase-13-1a-foundation",
  "F:\Projects\TreeGen-wt\phase-13-1b-sentry",
  "F:\Projects\TreeGen-wt\phase-13-1c-monitoring",
  "F:\Projects\TreeGen-wt\phase-13-2-security",
  "F:\Projects\TreeGen-wt\phase-14-1-telegram-cmds",
  "F:\Projects\TreeGen-wt\phase-14-2-telegram-digest",
  "F:\Projects\TreeGen-wt\phase-15-1-evidence-panel",
  "F:\Projects\TreeGen-wt\phase-4-10b-account-settings",
  "F:\Projects\TreeGen-wt\phase-4-11a-gdpr",
  "F:\Projects\TreeGen-wt\phase-4-11b-erasure",
  "F:\Projects\TreeGen-wt\phase-4-11c-ownership-transfer",
  "F:\Projects\TreeGen-wt\phase-4-13b-i18n-rollout",
  "F:\Projects\TreeGen-wt\phase-4-14a-mobile",
  "F:\Projects\TreeGen-wt\phase-4-15-onboarding",
  "F:\Projects\TreeGen-wt\phase-5-5a-quarantine-roundtrip",
  "F:\Projects\TreeGen-wt\phase-6-4-dna-triangulation",
  "F:\Projects\TreeGen-wt\phase-6-4-person-merge-ui",
  "F:\Projects\TreeGen-wt\phase-6-5-tree-stats",
  "F:\Projects\TreeGen-wt\phase-6-7a-clusters",
  "F:\Projects\TreeGen-wt\phase-7-4-hypothesis-queue",
  "F:\Projects\TreeGen-wt\phase-7-5-inference-v2",
  "F:\Projects\TreeGen-wt\phase-9-0-familysearch"
)
foreach ($p in $prune) {
  if (Test-Path $p) {
    $status = & git -C $p status --porcelain 2>$null
    if ($status) {
      Write-Warning "${p}: uncommitted changes — DO NOT prune yet:"
      Write-Output $status
    } else {
      Write-Host "${p}: clean — safe to prune" -ForegroundColor Green
    }
  } else {
    Write-Host "${p}: not present — already pruned" -ForegroundColor DarkGray
  }
}
```

### Prune worktrees (PowerShell, после dry-run check)

```powershell
# Запустить из F:\Projects\TreeGen (canonical worktree).
# git worktree remove не удалит worktree с uncommitted изменениями без --force —
# это правильное поведение, не передавайте --force автоматически.
$prune | ForEach-Object {
  if (Test-Path $_) {
    Write-Host "Removing worktree: $_" -ForegroundColor Cyan
    git worktree remove $_
  }
}

# Удалить ссылку из admin-метаданных (на всякий случай, если worktree уже физически удалён)
git worktree prune --verbose
```

### Удалить локальные branch'ы после worktree-prune

```powershell
# После удаления worktree branch остаётся в .git/refs/heads/. Удалить безопасно
# только те, что merged в main (squash-merge коммит присутствует в main log).
$mergedBranches = @(
  "chore/web-lockfile-and-snapshot",
  "feat/phase-10-0-ai-layer-skeleton",
  "feat/phase-10.1-ai-hypothesis-explanation",
  "feat/phase-10-2a-ai-source-extraction",
  "feat/phase-10-2b-ai-source-vision",
  "feat/phase-10-3-ai-normalization",
  "feat/phase-11.1-sharing-ui",
  "feat/phase-11-2-public-share",
  "feat/phase-12-0-stripe-billing",
  "feat/phase-13-1a-foundation",
  "feat/phase-13-1b-sentry-services",
  "feat/phase-13-1c-monitoring",
  "feat/phase-13-2-security-hardening",
  "feat/phase-14-1-telegram-commands",
  "feat/phase-14.2-telegram-digest",
  "feat/phase-15-1-evidence-panel",
  "feat/phase-4-10b-account-settings",
  "feat/phase-4-11a-gdpr-export",
  "feat/phase-4-11b-erasure-worker",
  "feat/phase-4-11c-ownership-transfer",
  "feat/phase-4-13b-i18n-full",
  "feat/phase-4-14a-mobile-responsive",
  "feat/phase-4-15-onboarding-tour",
  "feat/phase-5-5a-quarantine-roundtrip",
  "feat/phase-6.4-dna-triangulation",
  "feat/phase-6-4-person-merge-ui",
  "feat/phase-6-5-tree-stats",
  "feat/phase-6-7a-autoclusters-data-and-leiden",
  "feat/phase-7-4-hypothesis-review",
  "feat/phase-7-5-inference-confidence-v2",
  "feat/phase-9.0-familysearch-adapter",
  "docs/adr-evidence-tiers"
)
$mergedBranches | ForEach-Object {
  # -d (lowercase) откажется удалить unmerged branch — это safety net.
  git branch -d $_
}
```

### Drop стэшей

```powershell
# Дропать с конца (highest index) чтобы не сдвигать индексы.
# Проверить ещё раз: git stash list
@(10, 9, 8, 7, 6, 5, 4, 3, 2) | ForEach-Object {
  git stash drop "stash@{$_}"
}
# stash@{0} (landing-rebuild today) и stash@{1} (phase-9-2-pre-worktree) —
# вручную: git stash show -p stash@{0} | less; git stash show -p stash@{1} | less
```

### Bash вариант (Linux / Git Bash / WSL)

```bash
# Dry-run
for p in F:/Projects/TreeGen-wt/{chore-frontend-fixes,docs-adr-evidence-tiers,phase-10-0-ai-layer,phase-10-1-explanation,phase-10-2a-ai-source,phase-10-2b-ai-source-vision,phase-10-3-ai-normalization,phase-11-1-sharing-ui,phase-11-2-public-share,phase-12-0-stripe,phase-13-1a-foundation,phase-13-1b-sentry,phase-13-1c-monitoring,phase-13-2-security,phase-14-1-telegram-cmds,phase-14-2-telegram-digest,phase-15-1-evidence-panel,phase-4-10b-account-settings,phase-4-11a-gdpr,phase-4-11b-erasure,phase-4-11c-ownership-transfer,phase-4-13b-i18n-rollout,phase-4-14a-mobile,phase-4-15-onboarding,phase-5-5a-quarantine-roundtrip,phase-6-4-dna-triangulation,phase-6-4-person-merge-ui,phase-6-5-tree-stats,phase-6-7a-clusters,phase-7-4-hypothesis-queue,phase-7-5-inference-v2,phase-9-0-familysearch}; do
  if [ -d "$p" ]; then
    s=$(git -C "$p" status --porcelain)
    if [ -n "$s" ]; then echo "WARN $p: uncommitted"; echo "$s"; else echo "OK $p"; fi
  fi
done

# Prune (после dry-run review)
for p in F:/Projects/TreeGen-wt/{chore-frontend-fixes,...full-list...}; do
  [ -d "$p" ] && git worktree remove "$p"
done
git worktree prune --verbose
```

## Что в REVIEW

- **`feat/phase-15-4a-proposals-data-model`** — последний коммит `dad607e` от
  агента, не в main. Возможно, открытый PR. Проверить через `gh pr list --search
  "head:feat/phase-15-4a-proposals-data-model"`. Если PR закрыт без merge —
  prune; если open — keep до merge.
- **`feat/phase-9-1-wikimedia-commons-adapter`** — то же.
- **`feat/phase-9-2-jewishgen-integration`** — то же.
- **`stash@{0}`** (landing-rebuild) — сегодняшний, посмотреть `git stash show -p
  stash@{0}`. Если совпадает с уже committed/staged изменениями в
  `apps/landing/src/components/{providers,theme-toggle}.tsx` — drop.
- **`stash@{1}`** (phase-9-2-pre-worktree) — связан с REVIEW worktree выше,
  решить вместе.
