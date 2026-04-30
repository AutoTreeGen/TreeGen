# api-gateway

AutoTreeGen API gateway service — tree-domain workflow endpoints.

Phase 16.1a (this commit): genealogy git data model + minimal CRUD for
`tree_change_proposals`. See `docs/adr/0062-genealogy-git-collaborative-review.md`.

Subsequent sub-phases:

- **16.1b** — review actions (approve / reject / evidence attach) + permissions.
- **16.1c** — atomic merge engine + rollback (audit_log linkage).
- **16.1d** — frontend (`apps/web/src/app/trees/[id]/proposals/...`).

## Run locally

```bash
uv run uvicorn api_gateway.main:app --reload --port 8007
```

## ENV

Префикс `API_GATEWAY_`. Минимум:

| Var | Default | Purpose |
|---|---|---|
| `API_GATEWAY_DATABASE_URL` | `postgresql+asyncpg://autotreegen:...` | Async-DSN postgres. |
| `API_GATEWAY_CLERK_ISSUER` | `""` | Clerk JWT issuer. Пустой → 503 на authenticated endpoint'ах. |
| `API_GATEWAY_CLERK_JWKS_URL` | `""` | Override JWKS URL. |
| `API_GATEWAY_CLERK_AUDIENCE` | `""` | Optional `aud`-claim. |
