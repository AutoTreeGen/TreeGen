# entity-resolution

Pure-function алгоритмы дедупликации для AutoTreeGen (Phase 3.4).

## Что внутри

| Модуль | Что считает |
|---|---|
| `phonetic` | Soundex + Daitch-Mokotoff (несколько кодов на имя) |
| `string_matching` | Levenshtein-ratio, token-set ratio, weighted score |
| `sources` | `source_match_score(a, b) -> 0..1` |
| `places` | `place_match_score(a, b) -> 0..1` |
| `persons` | `person_match_score(a, b) -> (composite, components)` |
| `blocking` | `block_by_dm(persons)` для O(N × bucket_size) |

Все функции pure: на вход примитивы / dataclass'ы, на выход
числа / dict'ы. Никаких side effects, никаких БД-зависимостей.

## Архитектурное решение

См. **ADR-0015** (`docs/adr/0015-entity-resolution.md`):

- Только suggestions, никакого auto-merge персон (CLAUDE.md §5).
- Hard sex filter: mismatch известных полов → discard pair.
- Daitch-Mokotoff основной кодер для еврейских / восточно-европейских
  фамилий, Soundex как fallback bucket.
- Threshold по умолчанию 0.80 (likely + verify).

## Использование (после Phase 3.4 Task 4)

`services/parser-service/services/dedup_finder.py` поверх этого пакета
реализует `find_*_duplicates(session, tree_id, threshold)` —
читает БД, применяет scoring, возвращает `list[DuplicateSuggestion]`.
**READ-ONLY** — никаких UPDATE / DELETE.

## Тесты

```bash
uv run --package entity-resolution pytest packages/entity-resolution/
```

Покрытие — на синтетических фикстурах (`Zhitnitzky` варианты,
`Lubelskie parish` дубликаты, `Slonim ⊂ Slonim, Grodno`).
