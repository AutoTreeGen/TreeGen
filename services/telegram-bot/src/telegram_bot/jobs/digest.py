"""Weekly digest job (Phase 14.2).

Cron-job, запускается каждый понедельник 09:00 UTC из
``telegram_bot.worker.WorkerSettings.cron_jobs``. Шаги:

1. Найти всех linked users с ``notifications_enabled=True``.
2. Для каждого: skip если уже отправляли digest за этот period (Redis
   idempotency-flag) или если user opt-out'нул (другой Redis-flag).
3. HTTP GET parser-service ``/users/{id}/digest-summary?since=<7d_ago>``
   c ``X-Internal-Service-Token``.
4. Если в окне 0 событий — skip без отправки (не спамим пустыми
   сообщениями).
5. Формирование локализованного HTML-сообщения по ``user.locale``
   (ru/en).
6. ``bot.send_message(chat_id, ..., parse_mode="HTML")`` + inline
   keyboard с кнопкой «Отписаться/Unsubscribe».
7. Set Redis flag ``digest:sent:{user_id}:{period_start_iso}`` с
   TTL 60 days (idempotency window — re-runs в течение 2 месяцев
   не дублируют push).

Идемпотентность через Redis (а не через таблицу ``digest_send_log``)
сделана сознательно: alembic-миграция в Phase 14.2 запрещена task-spec'ом,
а 60-day window достаточен — старше двух месяцев нам не интересно
гарантировать «не отправили дважды», т.к. там UI-вопрос а не SLA.

Спека digest-summary в parser-service — см.
``parser_service.api.digest`` (Phase 14.2 sibling commit).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any, Final

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis
from shared_models.orm import TelegramUserLink, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# 7 дней — окно, за которое собираем «новые персоны» / pending hypotheses.
_WINDOW_DAYS: Final = 7

# 60 дней — TTL Redis-флага idempotency (см. модуль docstring).
_IDEMPOTENCY_TTL_SECONDS: Final = 60 * 24 * 60 * 60

# 1 год — TTL Redis-флага opt-out. Достаточно длинный чтобы пользователь
# не получал digest snowballing после повторного `/start` через год.
_OPTOUT_TTL_SECONDS: Final = 365 * 24 * 60 * 60

# Web base URL по умолчанию. В проде override'ится через worker context'а
# (settings.web_base_url).
_DEFAULT_WEB_BASE_URL: Final = "http://localhost:3000"

# HTTP-таймаут для одного digest-summary вызова. Каждый user — один
# запрос, и при N=1000 user'ов и 5 sec timeout мы блокируем cron-tick на
# до 80 минут — приемлемо для weekly job.
_HTTP_TIMEOUT_SECONDS: Final = 5.0


# Локализованные шаблоны. ru/en — единственные зарегистрированные locale в
# users.locale (Phase 4.10b). Default — en (ADR-0033).
_TEMPLATES: Final[dict[str, dict[str, str]]] = {
    "ru": {
        "header": "<b>Дайджест за неделю</b>",
        "stats_persons": "{count} новых персон",
        "stats_hypotheses": "{count} гипотез ждут проверки",
        "no_persons_block": "Свежих карточек пока нет.",
        "top_persons_header": "Свежие персоны:",
        "unsubscribe": "Отписаться от дайджеста",
    },
    "en": {
        "header": "<b>Weekly digest</b>",
        "stats_persons": "{count} new persons",
        "stats_hypotheses": "{count} hypotheses await review",
        "no_persons_block": "No new person cards this week.",
        "top_persons_header": "Recent persons:",
        "unsubscribe": "Unsubscribe from digest",
    },
}


def _t(locale: str, key: str) -> str:
    """Достать строку шаблона с fallback'ом на en."""
    locale = (locale or "en").lower()
    bundle = _TEMPLATES.get(locale, _TEMPLATES["en"])
    return bundle[key]


def optout_redis_key(user_id: uuid.UUID) -> str:
    """Redis-ключ для digest opt-out флага."""
    return f"digest:optout:{user_id}"


def sent_redis_key(user_id: uuid.UUID, period_start: dt.datetime) -> str:
    """Idempotency-ключ: «уже отправили digest за этот period»."""
    iso_day = period_start.date().isoformat()
    return f"digest:sent:{user_id}:{iso_day}"


def render_digest_message(
    *,
    locale: str,
    new_persons_count: int,
    new_hypotheses_pending: int,
    top_persons: list[dict[str, Any]],
    web_base_url: str,
) -> str:
    """Построить HTML-текст digest'а. Pure function для unit-теста.

    ``top_persons`` ожидаются в формате response'а parser-service:
    список dict'ов c ``id``/``tree_id``/``primary_name``/``birth_year``.
    """
    web = web_base_url.rstrip("/")
    parts: list[str] = [_t(locale, "header"), ""]

    parts.append("• " + _t(locale, "stats_persons").format(count=new_persons_count))
    parts.append("• " + _t(locale, "stats_hypotheses").format(count=new_hypotheses_pending))
    parts.append("")

    if top_persons:
        parts.append(_t(locale, "top_persons_header"))
        for card in top_persons:
            display = card.get("primary_name") or "—"
            year = card.get("birth_year")
            link = f"{web}/persons/{card['id']}?from=tg"
            line = f'• <a href="{link}">{display}</a>'
            if year is not None:
                line += f" ({year})"
            parts.append(line)
    else:
        parts.append(_t(locale, "no_persons_block"))

    return "\n".join(parts)


def build_unsubscribe_keyboard(locale: str) -> InlineKeyboardMarkup:
    """Inline keyboard с одной кнопкой ``digest:unsubscribe``.

    Callback handler — :func:`telegram_bot.services.handlers.handle_digest_unsubscribe`.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔕 " + _t(locale, "unsubscribe"),
                    callback_data="digest:unsubscribe",
                )
            ]
        ]
    )


async def _fetch_summary(
    http_client: httpx.AsyncClient,
    *,
    base_url: str,
    user_id: uuid.UUID,
    since: dt.datetime,
    token: str,
) -> dict[str, Any] | None:
    """Дёрнуть parser-service ``/users/{id}/digest-summary``.

    Возвращает parsed body либо ``None`` при ошибке (мы НЕ raise'им —
    digest worker не должен валиться на одном пользователе).
    """
    url = f"{base_url.rstrip('/')}/users/{user_id}/digest-summary"
    try:
        resp = await http_client.get(
            url,
            params={"since": since.isoformat()},
            headers={"X-Internal-Service-Token": token},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("digest-summary HTTP error for user_id=%s: %s", user_id, exc)
        return None
    if resp.status_code != 200:
        logger.warning(
            "digest-summary non-200 for user_id=%s: status=%s body=%r",
            user_id,
            resp.status_code,
            resp.text[:200],
        )
        return None
    body: dict[str, Any] = resp.json()
    return body


async def _process_one_user(
    *,
    bot: Bot,
    redis: Redis,
    http_client: httpx.AsyncClient,
    parser_base_url: str,
    parser_token: str,
    web_base_url: str,
    user: User,
    link: TelegramUserLink,
    period_start: dt.datetime,
) -> str:
    """Прогнать digest-цикл для одного user'а.

    Возвращает строку-исход для агрегата stats (логи / тесты): ``"sent"``,
    ``"skipped_optout"``, ``"skipped_already_sent"``, ``"skipped_empty"``,
    ``"skipped_api_error"``, ``"skipped_send_error"``.
    """
    # opt-out
    if await redis.get(optout_redis_key(user.id)):
        return "skipped_optout"

    # idempotency
    sent_key = sent_redis_key(user.id, period_start)
    if await redis.get(sent_key):
        return "skipped_already_sent"

    summary = await _fetch_summary(
        http_client,
        base_url=parser_base_url,
        user_id=user.id,
        since=period_start,
        token=parser_token,
    )
    if summary is None:
        return "skipped_api_error"

    new_persons = int(summary.get("new_persons_count") or 0)
    new_hyps = int(summary.get("new_hypotheses_pending") or 0)
    top = list(summary.get("top_3_recent_persons") or [])

    if new_persons == 0 and new_hyps == 0:
        # Не спамим пустотой; mark idempotency anyway чтобы ровно в эту
        # неделю не дёргать parser-service повторно на ретраях.
        await redis.set(sent_key, "1", ex=_IDEMPOTENCY_TTL_SECONDS)
        return "skipped_empty"

    text = render_digest_message(
        locale=user.locale or "en",
        new_persons_count=new_persons,
        new_hypotheses_pending=new_hyps,
        top_persons=top,
        web_base_url=web_base_url,
    )
    keyboard = build_unsubscribe_keyboard(user.locale or "en")

    try:
        await bot.send_message(
            chat_id=link.tg_chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except TelegramAPIError as exc:
        # Не помечаем idempotency — следующая cron-tick попробует ещё раз
        # (rate-limit / blocked-by-user / chat-not-found).
        logger.warning(
            "digest send_message failed user_id=%s chat_id=%s: %s",
            user.id,
            link.tg_chat_id,
            exc,
        )
        return "skipped_send_error"

    await redis.set(sent_key, "1", ex=_IDEMPOTENCY_TTL_SECONDS)
    return "sent"


async def send_weekly_digest(ctx: dict[str, Any]) -> dict[str, int]:
    """arq cron-job: разослать weekly digest всем подписанным linked users.

    Возвращает stats-dict (для arq job-result + наблюдаемости): счётчики
    по исходам ``_process_one_user`` (sent / skipped_*). Этот же dict
    логируется на уровне INFO в конце цикла.

    Вызывается arq'ом раз в неделю (см. ``WorkerSettings.cron_jobs``);
    также безопасен для ручного запуска через ``arq.enqueue_job`` для
    smoke-теста.
    """
    bot: Bot = ctx["bot"]
    redis: Redis = ctx["redis"]
    http_client: httpx.AsyncClient = ctx["http_client"]
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    parser_base_url: str = ctx["parser_base_url"]
    parser_token: str = ctx["parser_token"]
    web_base_url: str = ctx.get("web_base_url") or _DEFAULT_WEB_BASE_URL

    if not parser_token:
        logger.warning("digest worker: parser-service token not configured, skipping cycle")
        return {"skipped_misconfigured": 1}

    period_start = dt.datetime.now(dt.UTC) - dt.timedelta(days=_WINDOW_DAYS)

    stats: dict[str, int] = {}
    async with session_factory() as session:
        result = await session.execute(
            select(TelegramUserLink, User)
            .join(User, User.id == TelegramUserLink.user_id)
            .where(
                TelegramUserLink.revoked_at.is_(None),
                TelegramUserLink.notifications_enabled.is_(True),
                User.deleted_at.is_(None),
            )
        )
        rows = list(result.all())

    for link, user in rows:
        outcome = await _process_one_user(
            bot=bot,
            redis=redis,
            http_client=http_client,
            parser_base_url=parser_base_url,
            parser_token=parser_token,
            web_base_url=web_base_url,
            user=user,
            link=link,
            period_start=period_start,
        )
        stats[outcome] = stats.get(outcome, 0) + 1

    logger.info("weekly digest cycle finished: %s", stats)
    return stats
