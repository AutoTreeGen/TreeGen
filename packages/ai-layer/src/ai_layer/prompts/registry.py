"""Реестр prompt-шаблонов.

Дизайн (см. ADR-0043 §«Prompt versioning strategy»):

- **Версия в имени файла:** ``hypothesis_suggester_v1.md`` → ``v2`` появится
  как отдельный файл, оба останутся в репо. Это позволяет A/B-тестировать
  и катить откат без миграций.
- **Jinja2-рендеринг.** Системный + пользовательский промпты разделены
  заголовками ``# system`` / ``# user``. ``StrictUndefined`` гарантирует,
  что забытая переменная — ошибка на этапе рендера, а не silent-empty.
- **Типизированный доступ.** ``PromptRegistry.HYPOTHESIS_SUGGESTER_V1`` —
  атрибут-константа с ``PromptTemplate``, не магическая строка. IDE
  делает autocomplete, mypy ловит опечатки.

Phase 10.9e — locale-aware lookup:

- Шаблоны могут иметь locale-suffix: ``name_normalizer_v1.md`` (default,
  EN), ``name_normalizer_v1_ru.md``, ``name_normalizer_v1_he.md``.
- :func:`select_for_locale` выбирает per-locale шаблон если он
  существует, иначе fallback на base template. Это additive: добавление
  нового locale = новый файл + (опционально) запись в реестре, базовое
  поведение EN-callers не меняется (см. ADR-0080).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, StrictUndefined, Template

PROMPTS_DIR: Final[Path] = Path(__file__).parent

_SYSTEM_HEADER: Final[str] = "## system"
_USER_HEADER: Final[str] = "## user"


def _split_sections(raw: str, source: str) -> tuple[str, str]:
    """Разделить файл шаблона на system / user секции.

    Заголовки — H2 (``## system`` / ``## user``), а не H1, чтобы файлы
    оставались валидным markdown без MD025 (multiple top-level headings).
    Опциональный H1 в начале файла (``# Title``) разрешён и игнорируется.
    """
    lines = raw.splitlines()
    system_idx = _find_header(lines, _SYSTEM_HEADER)
    user_idx = _find_header(lines, _USER_HEADER)
    if system_idx is None or user_idx is None or user_idx <= system_idx:
        msg = (
            f"Prompt file {source} must contain '## system' before '## user' headers; "
            f"got system_idx={system_idx}, user_idx={user_idx}"
        )
        raise ValueError(msg)
    system_text = "\n".join(lines[system_idx + 1 : user_idx]).strip()
    user_text = "\n".join(lines[user_idx + 1 :]).strip()
    return system_text, user_text


def _find_header(lines: list[str], header: str) -> int | None:
    for idx, line in enumerate(lines):
        if line.strip().lower() == header.lower():
            return idx
    return None


@dataclass(frozen=True)
class RenderedPrompt:
    """Готовая к отправке пара system+user промптов."""

    system: str
    user: str


class PromptTemplate:
    """Один version'ированный шаблон (system + user, Jinja2).

    Args:
        name: Логическое имя без версии (``"hypothesis_suggester"``).
        version: Числовая версия (``1``, ``2``, ...). Совместно с ``name``
            мапится на файл ``{name}_v{version}.md``.

    Файл должен иметь две секции, разделённые заголовками ``# system``
    и ``# user``. Регистр заголовков — fixed (см. ``_SYSTEM_HEADER`` /
    ``_USER_HEADER``).
    """

    def __init__(
        self,
        name: str,
        version: int,
        *,
        prompts_dir: Path | None = None,
        locale: str | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.locale = locale
        self._prompts_dir = prompts_dir or PROMPTS_DIR
        self._system_template, self._user_template = self._load()

    @property
    def filename(self) -> str:
        suffix = f"_{self.locale}" if self.locale else ""
        return f"{self.name}_v{self.version}{suffix}.md"

    @property
    def path(self) -> Path:
        return self._prompts_dir / self.filename

    def render(self, **variables: Any) -> RenderedPrompt:
        """Отрендерить шаблон с подстановкой переменных.

        Raises:
            jinja2.UndefinedError: Если в шаблоне используется переменная,
                которой нет в ``variables`` (StrictUndefined).
        """
        return RenderedPrompt(
            system=self._system_template.render(**variables),
            user=self._user_template.render(**variables),
        )

    def _load(self) -> tuple[Template, Template]:
        if not self.path.exists():
            msg = f"Prompt template not found: {self.path}"
            raise FileNotFoundError(msg)
        raw = self.path.read_text(encoding="utf-8")
        system_text, user_text = _split_sections(raw, source=str(self.path))
        env = Environment(
            undefined=StrictUndefined,
            autoescape=False,  # промпты — plain text, не HTML
            trim_blocks=True,
            lstrip_blocks=True,
        )
        return env.from_string(system_text), env.from_string(user_text)


class PromptRegistry:
    """Типизированная точка доступа к шаблонам.

    Atributes-константы добавляются по мере появления prompt'ов. Тесты
    проверяют, что каждый зарегистрированный шаблон существует на диске
    и парсится без ошибок (см. ``tests/test_prompts_registry.py``).
    """

    HYPOTHESIS_SUGGESTER_V1: Final[PromptTemplate] = PromptTemplate("hypothesis_suggester", 1)
    HYPOTHESIS_EXPLANATION_V1: Final[PromptTemplate] = PromptTemplate("hypothesis_explanation", 1)
    PERSON_NORMALIZER_V1: Final[PromptTemplate] = PromptTemplate("person_normalizer", 1)
    # Phase 10.2 (ADR-0059): single-pass source-document extraction.
    SOURCE_EXTRACTOR_V1: Final[PromptTemplate] = PromptTemplate("source_extractor", 1)
    # Phase 10.3 (ADR-0060): AI normalization for places + names
    # (Eastern European Jewish genealogy specifics: Cyrillic/Hebrew/Yiddish,
    # Pale of Settlement, Kohanim/Levite markers, transliteration schemes).
    PLACE_NORMALIZER_V1: Final[PromptTemplate] = PromptTemplate("place_normalizer", 1)
    NAME_NORMALIZER_V1: Final[PromptTemplate] = PromptTemplate("name_normalizer", 1)
    # Phase 10.9b (ADR-0075): voice-to-tree 3-pass NLU extraction.
    VOICE_EXTRACT_PASS1_V1: Final[PromptTemplate] = PromptTemplate("voice_extract_pass1", 1)
    VOICE_EXTRACT_PASS2_V1: Final[PromptTemplate] = PromptTemplate("voice_extract_pass2", 1)
    VOICE_EXTRACT_PASS3_V1: Final[PromptTemplate] = PromptTemplate("voice_extract_pass3", 1)
    # Phase 10.9e (ADR-0080) — locale-specific name normalizer variants.
    # Wiring is via :func:`select_for_locale`; consumers should NOT branch
    # on these constants directly — pass NAME_NORMALIZER_V1 + locale instead.
    NAME_NORMALIZER_V1_RU: Final[PromptTemplate] = PromptTemplate("name_normalizer", 1, locale="ru")
    NAME_NORMALIZER_V1_HE: Final[PromptTemplate] = PromptTemplate("name_normalizer", 1, locale="he")

    @classmethod
    def all_templates(cls) -> list[PromptTemplate]:
        """Все зарегистрированные шаблоны — для batch-валидации в тестах."""
        return [value for value in vars(cls).values() if isinstance(value, PromptTemplate)]


# ---------------------------------------------------------------------------
# Phase 10.9e — locale-aware lookup helper
# ---------------------------------------------------------------------------


def select_for_locale(
    base: PromptTemplate,
    locale: str | None,
    *,
    prompts_dir: Path | None = None,
) -> PromptTemplate:
    """Выбрать per-locale вариант шаблона если он есть, иначе base.

    Args:
        base: Default-locale (EN) шаблон, например
            :data:`PromptRegistry.NAME_NORMALIZER_V1`.
        locale: ISO-639 код. ``None`` / ``""`` / ``"en"`` → base.
        prompts_dir: Override для тестов; обычно None (default ``PROMPTS_DIR``).

    Returns:
        :class:`PromptTemplate` — либо локализованный, либо base.

    Граф fallback'а: ``ru`` → ``name_normalizer_v1_ru.md`` if exists else
    ``name_normalizer_v1.md``. Никогда не raise'ит на missing locale файла —
    EN-fallback гарантирует regression-stable поведение для Geoffrey-демо.
    """
    if not locale or locale == "en":
        return base
    candidate_path = (prompts_dir or PROMPTS_DIR) / f"{base.name}_v{base.version}_{locale}.md"
    if not candidate_path.exists():
        return base
    return PromptTemplate(base.name, base.version, prompts_dir=prompts_dir, locale=locale)
