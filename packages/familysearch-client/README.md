# familysearch-client

Read-only клиент FamilySearch API для AutoTreeGen. См. [ADR-0011](../../docs/adr/0011-familysearch-client-design.md)
для контекста и [ADR-0009](../../docs/adr/0009-genealogy-integration-strategy.md)
для общей стратегии интеграций.

## Состав (Phase 5.0 skeleton)

- `auth.FamilySearchAuth` — OAuth 2.0 Authorization Code + PKCE flow
- `client.FamilySearchClient` — async HTTP-клиент
- `models.*` — Pydantic v2 модели для GEDCOM-X (`FsPerson`, `FsName`, `FsFact`,
  `FsGender`, `FsRelationship`)
- `errors.*` — типизированная иерархия исключений
- `config.FamilySearchConfig` — sandbox vs production endpoints

## Quickstart (sandbox)

> **Note:** требует sandbox app key с
> [developers.familysearch.org](https://developers.familysearch.org/). Без
> ключа можно прогнать только mock-тесты.

```python
import asyncio

from familysearch_client import (
    FamilySearchAuth,
    FamilySearchClient,
    FamilySearchConfig,
)


async def main() -> None:
    config = FamilySearchConfig.sandbox()
    auth = FamilySearchAuth(client_id="your-app-key", config=config)

    auth_url, code_verifier = auth.start_flow(redirect_uri="http://localhost:8765/cb")
    # 1. Открыть auth_url в браузере, авторизоваться, забрать ?code=... из redirect.
    # 2. Передать сюда полученный code и сохранённый code_verifier.
    code = input("paste authorization code: ")
    token = await auth.complete_flow(
        code=code,
        code_verifier=code_verifier,
        redirect_uri="http://localhost:8765/cb",
    )

    async with FamilySearchClient(access_token=token.access_token, config=config) as fs:
        person = await fs.get_person("KW7S-VQJ")
        print(person.id, person.display_name)


asyncio.run(main())
```

## Тесты

```bash
# Mock-тесты (всегда зелёные, не требуют ключа):
uv run --package familysearch-client pytest

# Real-API тесты (skipped без ключа; пометка @pytest.mark.familysearch_real):
FAMILYSEARCH_SANDBOX_KEY=... uv run pytest packages/familysearch-client -m familysearch_real
```

## Что НЕ входит в Phase 5.0

См. ADR-0011 §«Что отложить»: write endpoints (POST/PUT/DELETE), Memories
upload, DNA Match resources (partner-only), интеграция с `parser-service` —
всё это Phase 5.1+.
