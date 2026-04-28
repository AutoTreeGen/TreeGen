# llm-services

Phase 10.0 AI-layer skeleton: тонкая обёртка над Anthropic Claude API +
2 high-value LLM-операции для AutoTreeGen.

## Принципы

- **Deterministic > magic** (CLAUDE.md §6): LLM применяется только в
  gray-zone случаях, где rule-based подход неоднозначен. Базовый pipeline
  остаётся детерминированным.
- **Cost-aware:** все вызовы учитываются (см. ADR-0030 §«Cost ceiling»).
  Лимит на дерево — `parser_service.config.llm_max_calls_per_import` (default 100).
- **Privacy by design:** сырые DNA-данные **никогда** не отправляются в LLM
  (ADR-0030 §«Privacy»). Имена и места — только при явном consent пользователя.

## Public API

```python
from llm_services import (
    claude_client,
    normalize_place_name,
    disambiguate_name_variants,
)
```

- `claude_client(api_key=None, *, model=DEFAULT_MODEL)` — фабрика
  `anthropic.AsyncAnthropic` с retry/timeout по умолчанию.
- `normalize_place_name(raw, context)` — async; канонизирует
  «Slonim, Russian Empire» / «Слоним» / «Slonim, BLR» в
  `NormalizedPlace(name, country_code, historical_period)`.
- `disambiguate_name_variants(variants)` — async; группирует
  «Vladimir / Volodya / Володя» в `list[NameCluster]`.

## Промпты

Шаблоны живут в `src/llm_services/prompts/*.txt` с версионным заголовком
(`# version: vN`). Версия коммитится в audit-лог при каждом вызове.

## Phase 10.x roadmap

- `10.1` — RAG-сервис над загруженными источниками.
- `10.2` — free-text extraction (метрические записи XIX в. → структура).
- `10.3` — OCR post-processing для исторических шрифтов.
- `10.4` — hypothesis explainer (rationale на естественном языке).
