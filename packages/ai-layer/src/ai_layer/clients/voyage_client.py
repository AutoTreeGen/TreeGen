"""Async-обёртка над Voyage AI embeddings API.

Дизайн (см. ADR-0043 §«Voyage vs OpenAI embeddings»):

- **Default model:** ``voyage-3`` (1024-dim, multilingual, retrieval-tuned).
  Совместима с ``pgvector(1024)`` колонкой Phase 10.1+.
- **Dedup батча.** Voyage биллит за токены input'а, поэтому одинаковые
  тексты в батче склеиваются перед запросом и разворачиваются обратно
  через ``index_map``. Это дёшево для нашего случая (генеалогические
  имена / места часто повторяются), но правильно — выгружать кэш в
  Postgres (Phase 10.1, см. ADR-0043 §«Embedding cache»).
- **Async-only.** Voyage SDK имеет ``AsyncClient``; используем его.
- **Injectable клиент.** Тот же pattern, что у ``AnthropicClient``.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from ai_layer.config import AILayerConfig, AILayerConfigError, AILayerDisabledError
from ai_layer.types import EmbeddingResult


class VoyageEmbeddingClient:
    """Async-обёртка для embeddings через Voyage AI.

    Args:
        config: Конфигурация (ключ, default model, kill-switch).
        client: Опциональный SDK-клиент. Если ``None`` — создаётся лениво.
    """

    def __init__(
        self,
        config: AILayerConfig,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        input_type: str = "document",
    ) -> EmbeddingResult:
        """Получить эмбеддинги для списка текстов.

        Args:
            texts: Входные строки. Дубликаты после Unicode-нормализации
                склеиваются перед вызовом и разворачиваются через
                ``EmbeddingResult.index_map``.
            model: Override модели; ``None`` → ``config.voyage_model``.
            input_type: ``"document"`` (для индексации) или ``"query"``
                (для retrieval). Voyage делит namespace по input_type.

        Raises:
            AILayerDisabledError: Если ``config.enabled is False``.
            AILayerConfigError: Если API-ключ не настроен.
            ValueError: Если ``texts`` пустой.
        """
        if not self._config.enabled:
            msg = "AI_LAYER_ENABLED is false; refusing to call Voyage API"
            raise AILayerDisabledError(msg)
        if not texts:
            msg = "texts must be non-empty"
            raise ValueError(msg)

        client = self._get_client()
        chosen_model = model or self._config.voyage_model

        unique_texts, index_map = _dedup(texts)

        result = await client.embed(
            texts=unique_texts,
            model=chosen_model,
            input_type=input_type,
        )
        vectors = _extract_embeddings(result)

        return EmbeddingResult(
            vectors=vectors,
            index_map=index_map,
            model_version=chosen_model,
        )

    def _get_client(self) -> Any:
        """Лениво создать ``voyageai.AsyncClient`` или вернуть переданный."""
        if self._client is not None:
            return self._client

        if not self._config.voyage_api_key:
            msg = "VOYAGE_API_KEY is not set; cannot instantiate Voyage client"
            raise AILayerConfigError(msg)

        # Лениво: ``voyageai`` тянет heavy transitive deps (huggingface_hub,
        # tokenizers); не загружаем при enabled=false. PLC0415 — намеренное.
        import voyageai  # noqa: PLC0415

        self._client = voyageai.AsyncClient(api_key=self._config.voyage_api_key)
        return self._client


def _dedup(texts: list[str]) -> tuple[list[str], list[int]]:
    """Свернуть дубликаты в ``texts``.

    Нормализация: Unicode NFKC + strip — типичные «варианты одного и того
    же» для генеалогических имён («Иосиф » vs «Иосиф» vs «Иосиф »).

    Returns:
        ``(unique_texts, index_map)``: ``unique_texts[index_map[i]]`` —
        нормализованная версия ``texts[i]``.
    """
    unique: list[str] = []
    seen: dict[str, int] = {}
    index_map: list[int] = []
    for raw in texts:
        norm = unicodedata.normalize("NFKC", raw).strip()
        if norm in seen:
            index_map.append(seen[norm])
        else:
            seen[norm] = len(unique)
            unique.append(norm)
            index_map.append(seen[norm])
    return unique, index_map


def _extract_embeddings(result: Any) -> list[list[float]]:
    """Достать список векторов из объекта-ответа Voyage SDK.

    Voyage возвращает объект с атрибутом ``embeddings: list[list[float]]``
    (см. voyageai docs). Делаем доступ через ``getattr`` для устойчивости
    к стабам в тестах (которые могут вернуть dict).
    """
    if hasattr(result, "embeddings"):
        return [list(v) for v in result.embeddings]
    if isinstance(result, dict) and "embeddings" in result:
        return [list(v) for v in result["embeddings"]]
    msg = f"Voyage response has no 'embeddings' field: {result!r}"
    raise ValueError(msg)
