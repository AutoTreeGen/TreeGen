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
| 0031 | [GCP deployment architecture (staging)](./0031-gcp-deployment-architecture.md) | Accepted |
| 0032 | [Secrets management](./0032-secrets-management.md) | Accepted |
| 0036 | [Sharing & permissions model](./0036-sharing-permissions-model.md) | Accepted |
| 0040 | [Sharing UI architecture (Phase 11.1)](./0040-sharing-ui-architecture.md) | Accepted |
| 0044 | [Person merge UI architecture (Phase 6.4)](./0044-person-merge-ui.md) | Accepted |
| 0051 | [Tree statistics philosophy (Phase 6.5)](./0051-tree-statistics.md) | Accepted |
| 0053 | [Production security hardening (Phase 13.2)](./0053-production-security-hardening.md) | Accepted |
| 0054 | [DNA triangulation engine (Phase 6.4)](./0054-dna-triangulation-engine.md) | Accepted |
| 0056 | [Telegram bot commands + subscription (Phase 14.1)](./0056-telegram-bot-commands.md) | Accepted |
