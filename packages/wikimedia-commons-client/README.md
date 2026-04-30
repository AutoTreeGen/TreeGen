# wikimedia-commons-client

Async read-only клиент Wikimedia Commons (MediaWiki Action API) для
TreeGen — Phase 9.1 (ADR-0058).

Используется парсер-сервисом для подтягивания изображений мест
(synagogue photos, town views, gravestone shots) из Wikimedia Commons
с обязательным сохранением license + attribution metadata.

## Quickstart

```python
from wikimedia_commons_client import WikimediaCommonsClient, WikimediaCommonsConfig

config = WikimediaCommonsConfig(
    user_agent="AutoTreeGen/0.1 (https://github.com/AutoTreeGen/TreeGen; autotreegen@gmail.com)",
)
async with WikimediaCommonsClient(config=config) as client:
    images = await client.search_by_coordinates(latitude=54.687, longitude=25.279, radius_m=5000)
    for img in images:
        print(img.title, img.license.short_name, img.attribution.credit_html)
```

## Required: User-Agent

Wikimedia Foundation requires every API call to carry a descriptive
User-Agent (см. <https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy>).
Generic UA → 403. Format:
`<client>/<version> (<contact-info>) <library/version>`.

## Why no auth

Anonymous read access is sufficient for the Phase 9.1 use-case
(public images + public metadata). OAuth daha higher quotas, но not
needed at our request volume — см. ADR-0058 §«Anonymous vs OAuth».
