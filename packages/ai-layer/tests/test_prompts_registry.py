"""Тесты ``PromptRegistry``: рендеринг, версионирование, защита от undefined."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_layer.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    _split_sections,
)
from jinja2 import UndefinedError


def test_all_registered_templates_load() -> None:
    """Каждый шаблон в реестре существует на диске и парсится."""
    templates = PromptRegistry.all_templates()
    # hypothesis_suggester + explanation + person_normalizer + source_extractor
    # + place_normalizer + name_normalizer.
    assert len(templates) >= 6
    for tpl in templates:
        assert tpl.path.exists(), f"missing prompt file: {tpl.path}"


def test_place_normalizer_v1_renders() -> None:
    tpl = PromptRegistry.PLACE_NORMALIZER_V1
    rendered = tpl.render(
        raw="Юзерин, Гомельская обл",
        locale_hint="ru",
        context="Family lived there until 1905.",
    )
    assert "Юзерин" in rendered.user
    assert "ru" in rendered.user
    assert "1905" in rendered.user
    assert "senior" not in rendered.system.lower(), (
        "place prompt should not invoke the genealogist persona"
    )
    assert "pale of settlement" in rendered.system.lower()


def test_place_normalizer_v1_renders_without_optional_fields() -> None:
    tpl = PromptRegistry.PLACE_NORMALIZER_V1
    rendered = tpl.render(raw="Brody", locale_hint=None, context=None)
    assert "Brody" in rendered.user
    # locale_hint conditional не сработал → нет "Locale hint:".
    assert "locale hint" not in rendered.user.lower()


def test_name_normalizer_v1_renders() -> None:
    tpl = PromptRegistry.NAME_NORMALIZER_V1
    rendered = tpl.render(
        raw="מאיר בן אברהם הכהן",
        script_hint="hebrew",
        locale_hint="he",
        context=None,
    )
    assert "מאיר" in rendered.user
    assert "hebrew" in rendered.user.lower()
    assert "kohen" in rendered.system.lower()
    assert "infer from a surname" in rendered.system.lower()


def test_hypothesis_explanation_v1_renders_en() -> None:
    """Базовый рендеринг hypothesis_explanation_v1 с en-локалью."""
    tpl = PromptRegistry.HYPOTHESIS_EXPLANATION_V1
    rendered = tpl.render(
        subjects=[
            {"id": "p:1", "summary": "Iosif Kaminskii, b. 1872 Vilna"},
            {"id": "p:2", "summary": "Joseph Kaminsky, b. 1872 Vilna"},
        ],
        evidence=[
            {
                "rule_id": "rule.birth.exact",
                "direction": "supports",
                "confidence": 0.95,
                "details": "Birth year exact match",
            }
        ],
        composite_score="0.85",
        locale="en",
    )
    assert "senior genealogist" in rendered.system.lower()
    assert "respond in **english**" in rendered.system.lower()
    assert "Vilna" in rendered.user
    assert "rule.birth.exact" in rendered.user
    assert "0.85" in rendered.user


def test_hypothesis_explanation_v1_renders_ru() -> None:
    """Локаль ru переключает language directive в системном промпте."""
    tpl = PromptRegistry.HYPOTHESIS_EXPLANATION_V1
    rendered = tpl.render(
        subjects=[
            {"id": "p:1", "summary": "A"},
            {"id": "p:2", "summary": "B"},
        ],
        evidence=[],
        composite_score=None,
        locale="ru",
    )
    assert "respond in **russian**" in rendered.system.lower()


def test_hypothesis_suggester_v1_renders_with_facts() -> None:
    """Базовый рендеринг hypothesis_suggester_v1 с reasonable input."""
    tpl = PromptRegistry.HYPOTHESIS_SUGGESTER_V1
    rendered = tpl.render(
        facts=[
            {"id": "p:1:birth", "text": "Person 1 born 1850 in Vilna"},
            {"id": "p:2:birth", "text": "Person 2 born 1855 in Vilna"},
        ],
        existing_hypotheses=[],
    )
    assert "system" in rendered.system.lower() or "research assistant" in rendered.system.lower()
    assert "p:1:birth" in rendered.user
    assert "Vilna" in rendered.user
    assert "(none)" in rendered.user  # ветка пустых existing_hypotheses


def test_hypothesis_suggester_v1_renders_with_existing() -> None:
    """Ветка with existing_hypotheses — список рендерится bullet'ами."""
    tpl = PromptRegistry.HYPOTHESIS_SUGGESTER_V1
    rendered = tpl.render(
        facts=[{"id": "x", "text": "y"}],
        existing_hypotheses=["A is parent of B (rejected)"],
    )
    assert "A is parent of B" in rendered.user


def test_person_normalizer_v1_renders() -> None:
    """person_normalizer_v1 рендерится с минимальным person dict."""
    tpl = PromptRegistry.PERSON_NORMALIZER_V1
    rendered = tpl.render(person={"name": "Иосиф", "birth": "1850"})
    assert "Иосиф" in rendered.user
    assert "1850" in rendered.user
    # default-фильтры заполняют отсутствующие поля
    assert "(unknown)" in rendered.user
    assert "(none)" in rendered.user


def test_strict_undefined_raises_on_missing_variable() -> None:
    """``StrictUndefined`` ловит забытые переменные на этапе рендера."""
    tpl = PromptRegistry.HYPOTHESIS_SUGGESTER_V1
    with pytest.raises(UndefinedError):
        tpl.render()  # facts / existing_hypotheses не переданы


def test_template_filename_format() -> None:
    """Имя файла соответствует формату ``{name}_v{version}.md``."""
    tpl = PromptRegistry.HYPOTHESIS_SUGGESTER_V1
    assert tpl.filename == "hypothesis_suggester_v1.md"
    assert tpl.path.name == tpl.filename


def test_missing_template_file_raises(tmp_path: Path) -> None:
    """Несуществующий файл → FileNotFoundError, не silent."""
    with pytest.raises(FileNotFoundError):
        PromptTemplate("does_not_exist", 1, prompts_dir=tmp_path)


def test_template_with_missing_user_section_raises(tmp_path: Path) -> None:
    (tmp_path / "broken_v1.md").write_text(
        "## system\nonly system, no user section\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="must contain"):
        PromptTemplate("broken", 1, prompts_dir=tmp_path)


def test_split_sections_strips_and_orders() -> None:
    raw = "## system\nhello\n\n## user\nworld\n"
    system, user = _split_sections(raw, source="(unit-test)")
    assert system == "hello"
    assert user == "world"


def test_split_sections_inverted_order_raises() -> None:
    raw = "## user\nfirst\n## system\nsecond\n"
    with pytest.raises(ValueError, match="must contain"):
        _split_sections(raw, source="(unit-test)")


def test_split_sections_ignores_h1_title() -> None:
    """Опциональный H1-заголовок в начале файла не ломает парсинг."""
    raw = "# My Prompt\n\n## system\nA\n\n## user\nB\n"
    system, user = _split_sections(raw, source="(unit-test)")
    assert system == "A"
    assert user == "B"
