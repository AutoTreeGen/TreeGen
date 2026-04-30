# archive-service (Phase 9.0)

Read-only прокси к внешним генеалогическим архивам.
**Не пишет** в наше дерево (запись — Phase 9.1+; см. ROADMAP §13).

Первый адаптер — FamilySearch (Tier A, integrated). Read-only поверх
[`packages/familysearch-client`](../../packages/familysearch-client/) (ADR-0011);
service-слой добавляет:

- token-bucket rate-limit (FS quota — 1500 req/hour) на `(client_id, user_id)`;
- ETag-кэш ответов на 24h в Redis;
- at-rest-encryption refresh-токенов (Fernet);
- security middleware (Phase 13.2 / ADR-0053).

См. [ADR-0055](../../docs/adr/0055-archive-service-and-familysearch-adapter.md).

## Запуск (локально)

```bash
uv run uvicorn archive_service.main:app --reload --port 8003
curl http://localhost:8003/healthz
```

## Конфигурация

| ENV var | Зачем |
|---|---|
| `FAMILYSEARCH_CLIENT_ID` | App-key из FamilySearch developer console. Если пусто → endpoints возвращают 503. |
| `FAMILYSEARCH_CLIENT_SECRET` | Резерв для confidential-flow; в PKCE-flow не используется. |
| `FAMILYSEARCH_REDIRECT_URI` | URL, на который FamilySearch редиректит после логина. |
| `FAMILYSEARCH_BASE_URL` | Default `https://api.familysearch.org` (prod). Для sandbox — `https://api-integ.familysearch.org`. |
| `ARCHIVE_SERVICE_REDIS_URL` | `redis://...`; для тестов — fakeredis. |
| `ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY` | Fernet key (urlsafe-base64, 32 bytes). Если пусто — refresh-токены не сохраняются (503 на callback). |
| `ARCHIVE_SERVICE_FS_RATE_LIMIT_PER_HOUR` | Default 1500 (FS quota). |
| `ARCHIVE_SERVICE_FS_CACHE_TTL_SECONDS` | Default 86400 (24h). |
| `ARCHIVE_SERVICE_CLERK_ISSUER` | Clerk issuer URL (Phase 4.10). Без него — 503 на защищённых ручках. |

## Эндпоинты (read-only)

```text
GET /archives/familysearch/oauth/start
GET /archives/familysearch/oauth/callback
GET /archives/familysearch/search?q=&surname=&given=&year=&year_range=
GET /archives/familysearch/person/{fsid}
GET /healthz
```

## Тесты

```bash
uv run pytest services/archive-service
```

`familysearch_real` маркер — для тестов на живых credentials, в CI skipped.
