# dna-service

FastAPI service для DNA: consent management, encrypted blob storage,
matching API (Phase 6.2). Архитектурный контекст — `docs/adr/0020-dna-service-architecture.md`.

## Запуск (local dev)

```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn dna_service.main:app --reload --port 8001
```

## Конфигурация

Настройки через ENV (`DNA_SERVICE_*` префикс), см. `src/dna_service/config.py`:

- `DNA_SERVICE_DATABASE_URL` — async DSN postgres.
- `DNA_SERVICE_STORAGE_ROOT` — каталог для blob-файлов
  (default: `./var/dna-blobs/`).
- `DNA_SERVICE_REQUIRE_ENCRYPTION` — bool, default `true`. При `true`
  uploads без encryption-magic-header отвергаются (HTTP 400).

## Privacy

См. ADR-0012 + ADR-0020 + `docs/runbooks/dna-data-handling.md`.

- Plaintext DNA на диске **только** при `REQUIRE_ENCRYPTION=false`
  (dev / CI). В prod должен быть `true`.
- Логи — только агрегаты (counts, sha256-prefix). Никаких rsid /
  genotype / position в логах или error messages.
- Revocation = hard delete (blob + DB row). Audit-log factum-only.
