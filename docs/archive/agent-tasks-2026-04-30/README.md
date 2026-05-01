# Parallel agent task briefs

6 self-contained ТЗ для параллельной работы 6 инстансов Claude Code (или другого AI-агента) на проекте AutoTreeGen / SmarTreeDNA.

## Запуск

```powershell
# 1. Открыть 6 окон PowerShell в корне репо:
1..6 | ForEach-Object {
  $title = "Agent $_"
  Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-WorkingDirectory", "F:\Projects\TreeGen",
    "-Command", "`$Host.UI.RawUI.WindowTitle = '$title'; Write-Host 'Run: claude' -ForegroundColor Green"
  )
  Start-Sleep -Milliseconds 300
}

# 2. В каждом окне:
claude
# затем скопировать содержимое соответствующего файла .agent-tasks/0N-*.md
# и вставить как первое сообщение
```

## Правила параллельного запуска (важно)

| Ресурс | Кому разрешено трогать |
|---|---|
| Alembic-миграции (`infrastructure/alembic/`) | Только #4 (Stripe) → миграция 0016 |
| `packages/shared-models/` | Только #4 (новая модель `Subscription`) |
| `apps/web/messages/{en,ru}.json` | Только #6 (sharing UI, namespace `sharing.*`) |
| Корневой `pyproject.toml` | Только если регистрируешь новый workspace member (#2 archive-service, #4 payment-service) |
| `apps/web/` (всё остальное) | Только #6 |

Все ветки независимые: `feat/phase-X.Y-<short-name>`. Финальный merge в `main` — последовательно через PR (порядок: #1, #5, #2, #3, #6, #4 — миграция Stripe идёт последней, чтобы не блокировать остальных).

## Список задач

1. `01-phase-6.4-dna-triangulation.md` — DNA triangulation engine + endpoint
2. `02-phase-9.0-familysearch-adapter.md` — FamilySearch read-only adapter
3. `03-phase-14.2-telegram-digest.md` — Telegram inline-search + weekly digest
4. `04-phase-12.1-stripe-payments.md` — Stripe Checkout + Customer Portal scaffold
5. `05-phase-10.1-hypothesis-explanation.md` — AI hypothesis explanation use case
6. `06-phase-11.1-sharing-ui.md` — Tree sharing UI (owner page + accept-flow)
