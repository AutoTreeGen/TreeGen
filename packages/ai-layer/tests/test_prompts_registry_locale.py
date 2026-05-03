"""Tests for prompts.registry locale-aware lookup (Phase 10.9e slice A)."""

from __future__ import annotations

from ai_layer.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    select_for_locale,
)


def test_select_for_locale_returns_base_for_none() -> None:
    base = PromptRegistry.NAME_NORMALIZER_V1
    assert select_for_locale(base, None) is base


def test_select_for_locale_returns_base_for_en() -> None:
    base = PromptRegistry.NAME_NORMALIZER_V1
    assert select_for_locale(base, "en") is base


def test_select_for_locale_returns_base_for_empty_string() -> None:
    base = PromptRegistry.NAME_NORMALIZER_V1
    assert select_for_locale(base, "") is base


def test_select_for_locale_returns_ru_variant_when_present() -> None:
    base = PromptRegistry.NAME_NORMALIZER_V1
    chosen = select_for_locale(base, "ru")
    assert chosen.locale == "ru"
    assert chosen.filename == "name_normalizer_v1_ru.md"


def test_select_for_locale_returns_he_variant_when_present() -> None:
    base = PromptRegistry.NAME_NORMALIZER_V1
    chosen = select_for_locale(base, "he")
    assert chosen.locale == "he"
    assert chosen.filename == "name_normalizer_v1_he.md"


def test_select_for_locale_falls_back_to_base_for_unknown_locale() -> None:
    """Locale файла нет → возвращаем base, никаких exceptions."""
    base = PromptRegistry.NAME_NORMALIZER_V1
    chosen = select_for_locale(base, "xx-unknown")
    assert chosen is base


def test_locale_variants_render_without_undefined() -> None:
    """RU/HE variants должны рендериться с теми же variables что и base."""
    rendered = PromptRegistry.NAME_NORMALIZER_V1_RU.render(
        raw_name="Иван Петрович",
        context="meeting at synagogue",
    )
    assert "Иван Петрович" in rendered.user
    assert rendered.system  # non-empty


def test_he_variant_render_with_default_context() -> None:
    rendered = PromptRegistry.NAME_NORMALIZER_V1_HE.render(raw_name="מאיר בן אברהם")
    assert "מאיר בן אברהם" in rendered.user
    assert "(none)" in rendered.user  # default context filter


def test_locale_template_constants_reference_existing_files() -> None:
    """Both locale templates must exist on disk and be parseable."""
    assert PromptRegistry.NAME_NORMALIZER_V1_RU.path.exists()
    assert PromptRegistry.NAME_NORMALIZER_V1_HE.path.exists()


def test_select_for_locale_does_not_mutate_base() -> None:
    """Pure function — base template instance unchanged after call."""
    base = PromptRegistry.NAME_NORMALIZER_V1
    base_filename_before = base.filename
    _ = select_for_locale(base, "ru")
    assert base.filename == base_filename_before
    assert base.locale is None


def test_isinstance_locale_template() -> None:
    chosen = select_for_locale(PromptRegistry.NAME_NORMALIZER_V1, "ru")
    assert isinstance(chosen, PromptTemplate)
