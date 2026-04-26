# Architecture Decision Records

Архитектурные решения проекта.

## Что такое ADR

Короткий документ (~1 страница), фиксирующий важное решение: контекст,
рассмотренные варианты, выбранный вариант, последствия. Помогает новым
участникам и будущему себе понять, *почему* что-то сделано именно так.

## Шаблон

См. [`0000-template.md`](./0000-template.md).

## Конвенции

- Имя файла: `NNNN-short-kebab-name.md` (4 цифры, начиная с 0001).
- Не редактируем принятые ADR — создаём новый со статусом `Supersedes ADR-NNNN`.
- Status: `Proposed | Accepted | Deprecated | Superseded by ADR-XXXX`.

## Список (планируется)

См. `ROADMAP.md` §Б — список первых ADR.

| № | Заголовок | Status |
|---|---|---|
| 0001 | Выбор Python + FastAPI для backend | TBD |
| 0002 | [Структура монорепо](./0002-monorepo-structure.md) | Accepted |
| 0003 | Стратегия версионирования данных | TBD |
| 0004 | PostgreSQL + pgvector vs отдельный векторный store | TBD |
| 0005 | Стратегия entity resolution | TBD |
| 0006 | Хранение DNA-сегментов | TBD |
| 0007 | [GEDCOM 5.5.5 как канонический формат](./0007-gedcom-555-as-canonical.md) | Accepted |
| 0008 | Стратегия мультиязычности и транслитерации | TBD |
| 0009 | Подход к гипотезам и evidence-graph | TBD |
| 0010 | Аутентификация (Clerk vs Auth0 vs self-hosted) | TBD |
| 0011 | [Brand system v1.0](./0011-brand-system-v1.md) | Accepted |
