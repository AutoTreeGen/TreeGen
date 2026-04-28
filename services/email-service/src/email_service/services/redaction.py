"""PII-redaction для ``email_send_log.params`` (Phase 12.2, ADR-0039).

Любой `params` payload, который попадёт в БД, проходит через
``redact_email_params``. Это **defense-in-depth** на случай, если
caller случайно положил PII (full name, address, phone, DNA-данные).

Стратегия — **allowlist, не blocklist**:

* Разрешены только non-PII ключи, которые мы контролируем (см.
  ``_ALLOWED_PARAM_KEYS``).
* Остальные ключи маркируются ``__redacted__`` и заменяются на
  pшумок ``"[redacted]"``. Логируем warning — code-review увидит,
  что caller передал что-то неожиданное.

DNA-rule (ADR-0039 §«DNA hard rule»): ключи, содержащие
``dna``/``segment``/``rsid``/``cm`` (case-insensitive) → ALWAYS
``[redacted]``. Это HARD STOP, даже если ключ случайно попал в
allowlist.
"""

from __future__ import annotations

import logging
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)

# Allowlist — ключи которые можно класть в params без редакции.
# Расширяется по мере появления новых kind'ов; добавление нового ключа
# должно проходить code-review (см. ADR-0039 §«Adding new params»).
_ALLOWED_PARAM_KEYS: Final[frozenset[str]] = frozenset(
    {
        # Brand-related
        "brand_name",
        "support_email",
        "web_base_url",
        # Common UI
        "user_display_name",
        "locale",
        # payment_succeeded / payment_failed
        "amount_cents",
        "currency",
        "plan_name",
        "invoice_url",
        "billing_period_start",
        "billing_period_end",
        "next_attempt_at",
        # share_invite (Agent 4 hook — Phase 11.0)
        "tree_name",
        "inviter_display_name",
        "invite_url",
        # export_ready (Phase 4.x)
        "export_url",
        "export_size_bytes",
        "export_format",
        # erasure_confirmation (Phase 13.x)
        "erasure_completed_at",
        "data_retention_until",
        # password_reset_notice
        "reset_initiated_at",
        # welcome
        "signup_at",
    }
)

# DNA-rule: подстроки в ключах, которые ALWAYS редактируются.
# См. CLAUDE.md §3.5 («Privacy by design») и ADR-0039 §«DNA hard rule».
_DNA_FORBIDDEN_SUBSTRINGS: Final[tuple[str, ...]] = (
    "dna",
    "segment",
    "rsid",
    "kit",  # ловит "kit_id", "kit_summary"
    "centimorgan",
    "_cm",  # ловит "shared_cm", "segment_cm", "total_cm"
    "haplotype",
    "genotype",
    "snp",
    "chromosome",
)

_REDACTED_VALUE: Final = "[redacted]"


def _key_is_dna_forbidden(key: str) -> bool:
    """True если ключ выглядит как DNA-данные (case-insensitive)."""
    lower = key.lower()
    return any(needle in lower for needle in _DNA_FORBIDDEN_SUBSTRINGS)


def redact_email_params(params: dict[str, Any]) -> dict[str, Any]:
    """Отфильтровать params: allowlist + DNA-rule.

    Возвращает **новый** dict; original не мутируется.

    * DNA-ключ → ``[redacted]`` + warning в лог.
    * Не в allowlist → ``[redacted]`` + warning.
    * Нормальный allowlist'ed key → значение копируется как-есть
      (без рекурсии в nested dict — Phase 12.x может расширить
      на nested структуры с allowlist'ом по dotted-path).

    DNA-проверка идёт до allowlist'а (DNA wins даже если случайно
    попал в allowlist).
    """
    redacted: dict[str, Any] = {}
    for key, value in params.items():
        if _key_is_dna_forbidden(key):
            _LOG.warning(
                "DNA-related key %r in email params — REDACTED. "
                "Caller MUST NOT pass DNA data to email-service.",
                key,
            )
            redacted[key] = _REDACTED_VALUE
            continue

        if key not in _ALLOWED_PARAM_KEYS:
            _LOG.warning(
                "Unknown param key %r in email — REDACTED. "
                "Add to _ALLOWED_PARAM_KEYS in services/redaction.py "
                "if intentionally non-PII.",
                key,
            )
            redacted[key] = _REDACTED_VALUE
            continue

        redacted[key] = value

    return redacted


__all__ = ["redact_email_params"]
