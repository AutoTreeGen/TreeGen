"""Helper-stubs для отправки transactional-email из parser-service / других сервисов.

**Phase 12.2a status: STUB.** Реальный HTTP-вызов к ``email-service``
ждёт **Phase 12.2b** — там же приедут полноценные call-site'ы (Stripe
webhook → payment_succeeded, Clerk webhook → welcome, Agent 5's
erasure_confirmation, ...).

Зачем эти заглушки существуют уже сейчас:

* Agent 4 (Phase 11.1 share-invite call-site) и Agent 5 (Phase 13.x
  erasure pipeline) получают готовые импорты — их PR'ы не блокируются
  и могут писать call-site, он не падает (только log.info).
* Когда 12.2b приходит, единственное место, где меняется код, —
  внутренности этих функций; call-site'ы (welcome, payment_*,
  share_invite, erasure_confirmation, ...) остаются как есть.

Контракт:

* fire-and-forget — never raises.
* ``idempotency_key`` обязан быть caller-supplied (см. ADR-0039
  §«Idempotency convention»).
* ``params`` — non-PII payload, **никаких DNA-данных** (CLAUDE.md §3.5).

# Phase 11.1 share-invite tension (см. ADR-0040 §email-integration)

``send_share_invite()`` — call-site wrapper для invite-flow. У него
проблема: invitation-row создан с raw ``invitee_email``, а у Agent 3's
``send_transactional_email`` параметр ``recipient_user_id`` (UUID).
Если invitee ещё не зарегистрирован, ``users.id`` отсутствует — Phase
11.1 принимает это как первое ограничение wrapper'а: пока stub-режим,
это лишь log-line, без реальной отправки. Phase 12.2b должен либо:

1. расширить ``send_transactional_email`` отдельным kind для
   raw-email recipients (``share_invite`` принимает ``recipient_email``
   напрямую, email-service сам резолвит users.id если такая запись есть),
2. добавить здесь предварительный ``email → users.id`` lookup
   (None для незарегистрированных → стробить отправку или класть
   pending-row для post-signup follow-up).

До тех пор обе функции живут параллельно, обе stub-нутые.
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


async def send_share_invite(
    invitation_token: str,
    recipient_email: str,
    tree_name: str,
    inviter_name: str,
) -> None:
    """Послать приглашение в дерево по email — log-only stub (Phase 11.1).

    Args:
        invitation_token: UUID токена приглашения. Используется как корень
            ``idempotency_key`` (``invite:{token}``). Гарантирует, что
            повторный resend в рамках TTL email-service не приведёт к
            двойной отправке.
        recipient_email: Email адресата. Может не иметь соответствующего
            ``users.id`` — invitation выписывается на email до регистрации
            (см. модуль docstring §«Phase 11.1 share-invite tension»).
        tree_name: Человеко-читаемое имя дерева, попадает в subject.
        inviter_name: Display name пригласившего (или его email если NULL).

    Phase 12.2b заменит тело либо на ``send_transactional_email("share_invite",
    ...)`` после резолва email→user_id, либо на отдельный raw-email path.
    Сигнатура и call-site не меняются.
    """
    _LOG.info(
        "share_invite stub kind=share_invite token=%s recipient=%s tree=%s inviter=%s",
        invitation_token,
        recipient_email,
        tree_name,
        inviter_name,
        extra={
            "event": "share_invite_dispatched",
            "idempotency_key": f"invite:{invitation_token}",
            "kind": "share_invite",
        },
    )


__all__ = ["send_share_invite", "send_transactional_email"]
