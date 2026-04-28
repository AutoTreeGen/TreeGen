"""Phase 13.1 — общие хелперы наблюдаемости для всех сервисов.

* :func:`init_sentry` — env-gated init Sentry SDK. Если ``SENTRY_DSN`` пуст —
  no-op, без падений и без зависимости sentry-sdk во время import (lazy).
* :func:`configure_json_logging` — переключает корневой logger на JSON-формат,
  совместимый с парсером Cloud Logging (``severity`` вместо ``levelname``,
  message в ``message``, остальные поля как top-level keys). Stdlib-only,
  без сторонних зависимостей.

Использование (любой из сервисов в ``main.py`` / startup hook)::

    from shared_models.observability import init_sentry, configure_json_logging

    configure_json_logging(service_name="parser-service")
    init_sentry(service_name="parser-service", environment=os.environ.get("ENVIRONMENT", "local"))

См. ADR-0031 §observability и ADR-0032 §monitoring.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Sentry — env-gated, lazy import.
# ---------------------------------------------------------------------------


def init_sentry(
    *,
    service_name: str,
    environment: str | None = None,
    traces_sample_rate: float = 0.0,
) -> bool:
    """Инициализировать Sentry SDK, если задан ``SENTRY_DSN``.

    Если переменная окружения пуста или sentry-sdk не установлен — функция
    возвращает ``False`` и не падает. Это позволяет держать Sentry
    опциональной зависимостью (extra ``[observability]`` в pyproject.toml
    каждого сервиса).

    Args:
        service_name: Логическое имя сервиса (теги: parser-service, dna-service, ...).
        environment: ``staging`` | ``prod`` | ``local`` — обычно из ENV
            ``ENVIRONMENT``. Если None — Sentry автоматически берёт ``production``.
        traces_sample_rate: Доля транзакций с performance-tracing (0.0 = только
            ошибки, что обычно достаточно для PII-чувствительных сервисов).

    Returns:
        ``True`` если Sentry проинициализирован, ``False`` если SDK или DSN отсутствуют.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk  # noqa: PLC0415 — lazy import, опциональная зависимость
    except ImportError:
        # sentry-sdk не установлен — логируем и продолжаем без observability.
        logging.getLogger(__name__).warning(
            "SENTRY_DSN provided but sentry-sdk is not installed; "
            "skipping Sentry init. Install with `uv add sentry-sdk`.",
        )
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        # Не отсылать PII по умолчанию — у нас DNA-данные и пользовательский PII.
        # Включить через ENV если нужно.
        send_default_pii=os.environ.get("SENTRY_SEND_PII", "").lower() == "true",
        release=os.environ.get("SERVICE_VERSION") or None,
    )
    sentry_sdk.set_tag("service", service_name)
    return True


# ---------------------------------------------------------------------------
# Structured JSON logging.
# ---------------------------------------------------------------------------


class CloudLoggingJSONFormatter(logging.Formatter):
    """JSON-formatter, совместимый с ingestion в Cloud Logging.

    Cloud Logging парсит stdout как JSON, если message — валидный JSON-объект.
    Особые поля:

    * ``severity`` — взамен ``levelname`` (``DEBUG``, ``INFO``, ``ERROR`` —
      Cloud Logging знает эти значения).
    * ``message`` — основной текст.
    * ``logger.name`` — имя logger'а (для фильтрации).
    * Любые ``extra={"key": value}`` поля вытаскиваются на верхний уровень
      (поэтому log-based metric из мониторинг-модуля видит ``jsonPayload.event``).

    https://cloud.google.com/logging/docs/structured-logging
    """

    # Поля, которые добавляет stdlib logging автоматически — пропускаем
    # из ``extra``, иначе перезатрут наши ключи или раздуют payload.
    _RESERVED: frozenset[str] = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "asctime",
            "taskName",
        }
    )

    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "service": self.service_name,
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Вытаскиваем `extra=...` из record как top-level поля.
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                payload[key] = repr(value)
            else:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_json_logging(
    *,
    service_name: str,
    level: int | str = logging.INFO,
    enabled: bool | None = None,
) -> None:
    """Перенастроить корневой logger на JSON-вывод (Cloud Logging-friendly).

    Args:
        service_name: Тег ``service`` в каждой записи лога.
        level: Лог-уровень. Принимает int (``logging.INFO``) или str (``"INFO"``).
        enabled: Если ``False`` — функция no-op (для локальной разработки удобнее
            читаемый text-формат). По умолчанию читает ENV ``LOG_FORMAT_JSON``
            (truthy values: ``1``, ``true``, ``yes``).

    Безопасно вызывать несколько раз — старые handler'ы убираются перед
    добавлением нового.
    """
    if enabled is None:
        env = os.environ.get("LOG_FORMAT_JSON", "").lower()
        enabled = env in {"1", "true", "yes", "on"}
    if not enabled:
        return

    root = logging.getLogger()
    # Удаляем чужие handler'ы — иначе uvicorn/arq добавят свой text-формат.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(CloudLoggingJSONFormatter(service_name=service_name))
    root.addHandler(handler)
    root.setLevel(level)
