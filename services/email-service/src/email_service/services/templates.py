"""Sandboxed jinja2 рендеринг шаблонов (Phase 12.2, ADR-0039 §«Templates»).

Шаблоны живут в ``services/email-service/templates/{kind}/{locale}.{ext}``,
где ``ext`` ∈ {``html``, ``txt``, ``subject.txt``}.

* ``SandboxedEnvironment`` — защита от RCE через user-controlled
  ``params``. ``SandboxedEnvironment`` блокирует вызовы dunder-атрибутов
  (``{{ obj.__class__.__bases__ }}`` и т.п.). Безопаснее, чем
  default ``Environment``.
* ``select_autoescape(["html"])`` — auto-escape для html-шаблонов;
  txt-шаблоны не escape'аются (там и не нужно, plain text).
* StrictUndefined — отсутствующая переменная в шаблоне → исключение,
  а не silent ``""``. Лучше падать в тестах, чем посылать письмо
  с пустыми скобками.
* Локали fallback'ятся на ``en`` если запрошенной нет.

См. ADR-0039 §«Templates» для дизайн-решений.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import (
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)
from jinja2.sandbox import SandboxedEnvironment
from shared_models.enums import EmailKind

_TEMPLATES_ROOT: Final = Path(__file__).resolve().parents[3] / "templates"
_DEFAULT_LOCALE: Final = "en"
_SUPPORTED_LOCALES: Final = ("en", "ru")


@dataclass(frozen=True)
class RenderedEmail:
    """Результат рендеринга шаблона."""

    subject: str
    html_body: str
    text_body: str


def _build_environment() -> SandboxedEnvironment:
    """Сконструировать sandboxed jinja2 environment."""
    return SandboxedEnvironment(
        loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        keep_trailing_newline=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


_ENV: Final = _build_environment()


def _normalize_locale(locale: str | None) -> str:
    """Привести locale к одному из поддерживаемых, fallback на ``en``."""
    if not locale:
        return _DEFAULT_LOCALE
    short = locale.lower().split("-", maxsplit=1)[0]
    if short in _SUPPORTED_LOCALES:
        return short
    return _DEFAULT_LOCALE


def render_email(
    kind: EmailKind,
    locale: str | None,
    context: dict[str, Any],
) -> RenderedEmail:
    """Отрендерить шаблон ``kind`` под ``locale``.

    Возвращает ``RenderedEmail`` с subject + html + text. Поднимает
    ``jinja2.TemplateNotFound`` если шаблона нет (caller отдаёт 500 —
    это misconfig, не user-error).
    """
    resolved = _normalize_locale(locale)

    subject_tpl = _ENV.get_template(f"{kind.value}/{resolved}.subject.txt")
    html_tpl = _ENV.get_template(f"{kind.value}/{resolved}.html")
    text_tpl = _ENV.get_template(f"{kind.value}/{resolved}.txt")

    subject = subject_tpl.render(**context).strip()
    html_body = html_tpl.render(**context)
    text_body = text_tpl.render(**context)

    return RenderedEmail(subject=subject, html_body=html_body, text_body=text_body)


__all__ = ["RenderedEmail", "render_email"]
