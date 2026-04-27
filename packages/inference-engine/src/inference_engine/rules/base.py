"""InferenceRule Protocol — контракт plugin'а для hypothesis engine.

Любой объект с этими атрибутами квалифицируется как InferenceRule и
может быть зарегистрирован через ``register_rule()`` (см.
``rules/registry.py``). Inheritance не требуется — Protocol проверяется
структурно через ``isinstance(obj, InferenceRule)`` благодаря
``runtime_checkable``.

Контракт (фиксируется ADR-0016):

- **Pure function.** Никакого I/O (БД, HTTP, файлы), никаких side-effects.
  Логирование агрегатов разрешено (без raw PII / DNA данных, см. ADR-0012).
- **Detеrminism.** Один и тот же вход → один и тот же выход. Никакой
  ``random.random()``, никакого ``datetime.now()``, никаких LLM-вызовов
  (LLM-rules — Phase 10, отдельный package с явным seed в context).
- **Provenance.** Каждый возвращаемый Evidence несёт ``rule_id`` равный
  ``self.rule_id`` — иначе composer не сможет проследить, какой rule
  произвёл какой Evidence.

Subject-формат: ``dict``, не строгая Pydantic-модель. В Phase 7.0 это
сознательный compromise — разные пакеты (entity-resolution, dna-analysis,
familysearch-client) держат свои представления Person/Place/Source, и
принуждение к одному типу создаёт hard coupling. Контракт между rule'ом
и его caller'ом — соглашение о ключах в dict (документируется в docstring
конкретного rule'а).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from inference_engine.types import Evidence


@runtime_checkable
class InferenceRule(Protocol):
    """Pure function: ``(subject_a, subject_b, context) -> list[Evidence]``.

    Атрибуты:
        rule_id: Уникальный идентификатор rule'а (например,
            ``"birth_year_match"``). Используется в registry, в provenance
            каждого Evidence, и в логах. Должен быть стабильным между
            релизами — изменение rule_id ломает persisted Evidence в Phase 7.2+.

    Methods:
        apply: Применить rule к паре subjects + context.

            Args:
                subject_a, subject_b: Произвольные dict'ы с полями субъектов.
                    Минимальный набор ключей — ответственность конкретного
                    rule'а (документируется в его docstring).
                context: Общий контекст вычисления гипотезы. Phase 7.0 —
                    пустой ``{}`` в большинстве случаев. Phase 7.1+ может
                    нести ``{"genetic_map": ..., "tree_id": ...,
                    "shared_segments": [...]}`` для rule's, которые
                    зависят от outputа других rules / других пакетов.

            Returns:
                Список Evidence. Пустой list — допустимый ответ, означает
                «rule неприменим к этой паре» (например, BirthYearMatchRule
                на subjects без birth_year).
    """

    rule_id: str

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]: ...
