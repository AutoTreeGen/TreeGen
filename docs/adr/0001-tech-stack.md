# ADR-0001: Выбор технологического стека

**Статус:** принято
**Дата:** 2026-04-25

## Контекст

Нужен стек для соло-разработчика, способный масштабироваться до публичной
платформы с ДНК-данными, AI и интеграциями с архивами.

## Решение

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async.
- **Менеджер зависимостей:** uv (вместо poetry/pip — быстрее, единый инструмент).
- **Frontend:** Next.js 15 + TypeScript + Tailwind 4 + shadcn/ui.
- **БД:** PostgreSQL 16 + pgvector (эмбеддинги в той же БД).
- **Контейнеризация:** Docker + docker-compose локально.
- **Облако:** GCP (AlloyDB, Cloud Run, GKE, Cloud Storage, Secret Manager, KMS).

## Обоснование

- Python — богатая экосистема для генеалогии/NLP/ML, скорость разработки.
- uv — на порядок быстрее poetry для install/sync, единый инструмент для Python и venv.
- PostgreSQL + pgvector — одна БД вместо отдельного векторного хранилища, проще для соло.
- Next.js — SSR для SEO, серверные компоненты, экосистема.
- GCP — AlloyDB совместим с Postgres, managed security, private networking встроен.

## Последствия

- Разработчик должен знать TypeScript для frontend (или использовать Claude Code).
- pgvector ограничен по производительности на масштабе > 10M векторов — тогда переедем на Vertex AI Vector Search.
- Привязка к GCP. Перенос на AWS/Azure возможен, но нетривиален.
