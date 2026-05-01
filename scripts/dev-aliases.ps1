# AutoTreeGen / SmarTreeDNA — invisible-git wrapper.
#
# Команды: save <msg> / sync / oops.
# Реализация и обоснование — см. docs/adr/0068-invisible-git-ux-wrapper.md.
# Этот wrapper НЕ нарушает CLAUDE.md §5 и ADR-0008: каждое сохранение проходит
# через PR/CI, прямого пуша в main нет.

function save {
  param([Parameter(Mandatory)][string]$msg)
  $ts = Get-Date -f "yyyyMMdd-HHmmss"
  $branch = "feat/auto-$ts"
  git checkout -b $branch
  git add -A
  git commit -m $msg
  git push -u origin $branch
  $pr = gh pr create --base main --head $branch --title $msg --body "auto-PR via save command (см. ADR-0068)" 2>&1
  Write-Host "PR opened: $pr" -ForegroundColor Green
  git checkout main
  git pull --ff-only
  Write-Host "save done. Auto-merger will land it when CI is green." -ForegroundColor Cyan
}

function sync { git pull --ff-only origin main }

function oops {
  $unpushed = git log "@{u}..HEAD" --oneline 2>$null
  if ($unpushed) {
    Write-Host "Last commit is not pushed. Soft-reset?" -ForegroundColor Yellow
    if ((Read-Host "y/n") -eq "y") { git reset --soft HEAD~1 }
  } else {
    Write-Host "Last commit is on origin. To undo: gh pr close <num> on the relevant PR." -ForegroundColor Yellow
    gh pr list --author "@me" --limit 5
  }
}
