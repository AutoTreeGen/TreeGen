"""Helper-stub для отправки transactional-email из parser-service / других сервисов.

**Phase 12.2a status: STUB.** Реальный HTTP-вызов к ``email-service``
ждёт **Phase 12.2b** — там же приедут call-site'ы (Stripe webhook
→ payment_succeeded, Clerk webhook → welcome, Agent 4's share_invite,
Agent 5's erasure_confirmation, и т.п.).

Зачем эта 5-строчная заглушка существует уже сейчас:

* Agent 4 (Phase 11.1 share-invite call-site) и Agent 5 (Phase 13.x
  erasure pipeline) получат готовый импорт ``send_transactional_email``
  ещё до 12.2b, и их PR'ы не блокируются. Они могут писать call-site,
  он не падает (только log.info).
* Когда 12.2b приходит, единственное место, где меняется код, —
  внутренности этой функции; call-site'ы (welcome, payment_*,
  share_invite, erasure_confirmation, ...) остаются как есть.

Контракт:

* fire-and-forget — never raises.
* ``idempotency_key`` обязан быть caller-supplied (см. ADR-0039
  §«Idempotency convention»).
* ``params`` — non-PII payload, **никаких DNA-данных** (CLAUDE.md §3.5).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)


async def send_transactional_email(
    kind: str,
    recipient_user_id: uuid.UUID,
    idempotency_key: str,
    params: dict[str, Any] | None = None,
) -> bool:
    """STUB: логирует намерение отправить email. Phase 12.2b добавит HTTP-вызов.

    Returns ``True`` всегда — caller'ы должны видеть стабильный API.
    Phase 12.2b изменит реализацию на real HTTP к ``email-service`` и
    будет возвращать ``False`` при provider-failure.
    """
    _LOG.info(
        "email-dispatcher STUB (Phase 12.2a) kind=%s user=%s key=%s params_keys=%s",
        kind,
        recipient_user_id,
        idempotency_key,
        sorted((params or {}).keys()),
    )
    return True


__all__ = ["send_transactional_email"]
