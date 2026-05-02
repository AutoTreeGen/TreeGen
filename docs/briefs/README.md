# Phase briefs

Working notes for upcoming phases (paste-ready specs handed to AI agents).

## Alembic / ADR numbering

Before creating a new alembic migration or ADR, run:

```powershell
pwsh scripts/next-chain-number.ps1 -Type alembic
pwsh scripts/next-chain-number.ps1 -Type adr
```

This returns the next safe number considering `origin/main` + every active
worktree under `F:\Projects\TreeGen-wt\` (untracked + locally-committed files).
Prevents the 2026-05-02 triple-collision pattern (alembic 0033 claimed by 4
agents, ADR-0070 claimed by 2) when phases run in parallel.

Add `-Json` for machine-readable output.
