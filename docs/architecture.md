# Архитектура AutoTreeGen

> **Статус:** черновик. Заполняется по мере прохождения фаз. См. `ROADMAP.md`.

---

## 1. Высокоуровневая схема (Фаза 0)

```text
┌──────────────┐
│  Web (Next)  │
└──────┬───────┘
       │ HTTPS
┌──────▼─────────┐
│  api-gateway   │  ← FastAPI, аутентификация (Clerk), routing
└──────┬─────────┘
       │
       ├────────────────────────────────────────────────────────┐
       │                                                         │
┌──────▼──────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────▼──────┐
│ parser-svc  │  │  dna-svc    │  │ archive-svc  │  │ inference-svc   │
└──────┬──────┘  └──────┬──────┘  └──────┬───────┘  └──────┬──────────┘
       │                │                │                  │
       └────────────────┴────┬───────────┴──────────────────┘
                              │
                ┌─────────────▼─────────────┐
                │  PostgreSQL 16 + pgvector │
                └─────────────┬─────────────┘
                              │
                  ┌───────────▼──────────┐
                  │  Object storage      │  (MinIO локально / GCS прод)
                  │  (GEDCOM, DNA, docs) │
                  └──────────────────────┘
```

Очереди и фоновые задачи — Redis + `arq` (локально), Cloud Tasks (прод).

---

## 2. Сервисы

| Сервис | Ответственность | Фаза |
|---|---|---|
| `api-gateway` | Auth, routing, rate limiting, OpenAPI | 3 |
| `parser-service` | Импорт/экспорт GEDCOM, идемпотентность | 3 |
| `dna-service` | Импорт ДНК, кластеризация, triangulation | 6 |
| `archive-service` | Адаптеры FamilySearch / Geni / MyHeritage / Wikimedia / … | 9 |
| `inference-service` | Rules engine + scoring + LLM-агент гипотез | 8 |
| `notification-service` | Email + Telegram уведомления | 14 |

---

## 3. Поток данных при импорте GEDCOM

```text
1. Web → POST /trees/{id}/imports (multipart, файл до 500 MB)
2. api-gateway сохраняет в S3 (gedcom bucket), enqueue job
3. parser-service:
   a. Скачивает файл из S3
   b. Парсит (gedcom-parser package)
   c. Entity resolution против существующих персон (фаза 7)
   d. Пишет в Postgres (с provenance + version)
   e. Триггерит inference-service для пересмотра гипотез
4. WebSocket / SSE: прогресс импорта в Web
```

---

## 4. Ключевые архитектурные решения

См. `docs/adr/` — каждое решение со своим ADR.

- ADR-0001: Python + FastAPI для backend.
- ADR-0002: Структура монорепо.
- ADR-0003: Стратегия версионирования (audit-log).
- ADR-0004: Postgres + pgvector vs отдельный векторный store.
- ADR-0005: Entity resolution.
- ADR-0006: Хранение DNA-сегментов.
- ADR-0007: GEDCOM 5.5.5 как канонический формат.
- ADR-0008: Транслитерация.
- ADR-0009: Гипотезы и evidence-graph.
- ADR-0010: Аутентификация.

---

## 5. Безопасность

- TLS 1.3 везде.
- CMEK на storage и БД (прод).
- Application-level envelope encryption для DNA-сегментов.
- Secret Manager для всех секретов.
- VPC Service Controls вокруг чувствительных API.
- Audit log → BigQuery (прод).
- Rate limiting на всех публичных эндпоинтах.

См. `ROADMAP.md` секция 17 для деталей GCP-деплоя.

---

## 6. Производительность (целевые метрики)

| Операция | Target |
|---|---|
| Парсинг 100 МБ GEDCOM | < 2 мин |
| Поиск персоны по имени (10⁵ персон) | < 100 мс |
| Запрос предков 10 поколений | < 200 мс |
| p95 API latency (без LLM) | < 300 мс |

---

## 7. Открытые вопросы

См. `ROADMAP.md` секция 24.
