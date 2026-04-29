# ai-layer

AI-инфраструктура AutoTreeGen (Phase 10.0). Тонкие обёртки вокруг Anthropic
Claude и Voyage AI, типизированный реестр prompt-шаблонов, скелет use-cases.

См. `docs/adr/0043-ai-layer-architecture.md` — обоснование выбора провайдеров,
prompt versioning и границ deterministic-vs-LLM.

## Public API

```python
from ai_layer import (
    AnthropicClient,             # async wrapper c retries и Pydantic structured output
    VoyageEmbeddingClient,       # async embeddings + dedup батчей
    PromptRegistry,              # типизированный доступ к Jinja2-шаблонам
    HypothesisSuggester,         # use-case stub (Phase 10.0)
    HypothesisSuggestion,        # Pydantic structured-output модель
    AILayerConfig,               # конфигурация из ENV
    AILayerDisabledError,        # подняется, если AI_LAYER_ENABLED=false
)
```

## Конфигурация

| ENV var | Default | Назначение |
|---|---|---|
| `AI_LAYER_ENABLED` | `false` | Master kill-switch. Когда `false`, конструкторы клиентов отказываются делать сетевые вызовы. |
| `ANTHROPIC_API_KEY` | — | Передаётся в `anthropic.AsyncAnthropic`. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Default-модель, можно переопределить в каждом вызове. |
| `VOYAGE_API_KEY` | — | Передаётся в `voyageai.AsyncClient`. |
| `VOYAGE_MODEL` | `voyage-3` | Default-модель эмбеддингов (1024-dim). |

## Тестирование

Все внешние API замоканы через `httpx.MockTransport` / Pydantic-фикстуры.
В CI реальные API не вызываются (см. `tests/conftest.py`).

```bash
uv run --package ai-layer pytest -v
```
