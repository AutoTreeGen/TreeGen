# ADR-0031: GCP deployment architecture (staging)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `infra`, `gcp`, `terraform`, `phase-13`

## Контекст

Phase 13.0 — первый раз поднимаем платформу в облаке. До сих пор всё крутилось
локально в `docker compose` (Postgres + Redis + MinIO + arq-worker). Нужно
определиться:

1. Куда катимся — Cloud Run vs GKE.
2. Какая БД в проде — managed AlloyDB vs AlloyDB Omni vs обычный Cloud SQL.
3. Чем заменить arq в проде — Cloud Tasks vs продолжать гнать Redis.
4. Один проект GCP на staging+prod или два отдельных.
5. Сколько денег это будет жрать.

Контекст: соло-разработчик, бюджет на staging — желательно меньше $100/мес.
Прод-нагрузка пока умозрительная (≤100 active users). DNA = special-category
data (GDPR Art. 9), это ограничивает «сэкономим на безопасности».

## Рассмотренные варианты

### Compute: Cloud Run vs GKE Autopilot vs обычный GCE

#### A. Cloud Run

- ✅ Pay-per-request, scale-to-zero. Staging при отсутствии трафика — почти $0.
- ✅ Минимум ops: один YAML/Terraform-блок на сервис, deploy = `gcloud run deploy`.
- ✅ Встроенный HTTPS, ingress, autoscaling, request-level CPU billing.
- ✅ Serverless VPC connector подключает к private subnet → AlloyDB.
- ❌ Cold start 1–3 сек на FastAPI с asyncpg. Допустимо для не-real-time API.
- ❌ Лимит 60 минут на запрос (нашим импортам хватает; bulk hypothesis может упереться).
- ❌ Нет sticky sessions / WebSockets ровно через Cloud Run v1; v2 — есть, но с лимитами.

#### B. GKE Autopilot

- ✅ Полная гибкость: long-running jobs, stateful sets, любые ресурсы.
- ❌ Минимум $73/мес за control plane (даже на Autopilot). Плюс worker nodes.
- ❌ Нужно знать k8s — на соло-разработчика это дополнительный ops-tax.
- ❌ Deploy сложнее (kustomize/helm, image promotion, manifests review).

#### C. Compute Engine + systemd

- ✅ Дёшево и понятно.
- ❌ Сами таскаем reload, certs, autoscaling, blue/green.
- ❌ Не вписывается в GCP-managed философию ADR-0001.

### Database: AlloyDB managed vs AlloyDB Omni vs Cloud SQL

#### A. AlloyDB managed

- ✅ Native HA, automated backups, PITR, vector index ускоренный.
- ✅ В проде это правильный выбор.
- ❌ Минимум ~$200/мес даже для самого маленького кластера (1 primary, 0 read replicas).
  Слишком дорого для staging.

#### B. AlloyDB Omni на GCE VM

- ✅ Та же кодовая база (postgres-superset wire protocol, тот же `vector`),
  тот же SQL → миграция staging → prod = смена DSN.
- ✅ ~$30/мес за `e2-standard-2` + 50 ГБ pd-ssd.
- ✅ pgvector (через `CREATE EXTENSION vector`) — staging-данные re-importable из `Ztree.ged`.
- ❌ Нет автоматического PITR / failover. Допустимо для staging (данные синтетические).
- ❌ Бэкапы — наша забота. На staging решается ежедневным `pg_dump` в GCS.

#### C. Cloud SQL Postgres + pgvector

- ✅ Полностью managed, ~$50/мес за `db-g1-small` + storage.
- ❌ pgvector в Cloud SQL медленнее, чем в AlloyDB, и не поддерживает все
  фичи AlloyDB ScaNN.
- ❌ Несовместим по wire-протоколу с AlloyDB managed (есть мелкие отличия в
  EXPLAIN, расширениях). Миграция staging → prod не будет drop-in.

### Очереди: Cloud Tasks vs Redis-в-проде

#### A. Cloud Tasks (push HTTP)

- ✅ Managed, autoscale, dead-letter, exponential backoff, retry policy в одном
  месте, цена ~$0.40 за миллион dispatches.
- ✅ Cloud Run ↔ Cloud Tasks — стандартный паттерн в GCP.
- ✅ HTTP-target воркер = тот же FastAPI-сервис, просто отдельный endpoint
  типа `POST /internal/jobs/{name}`. Никакого долгоживущего worker-процесса.
- ❌ Нет pub/sub-стриминга прогресса (нам нужен Redis pub/sub для SSE — оставляем
  Redis для этой роли через Memorystore).
- ❌ Лимит payload 100 КБ — большие GED-аргументы передаём через GCS-blob, в
  task кладём только GCS path (мы уже так делаем локально с `tmp_path`).

#### B. Redis (Memorystore) + arq

- ✅ Кодовая база уже на arq (ADR-0026), не надо переписывать.
- ❌ Memorystore Basic Tier — минимум 1 ГБ × $0.049/час ≈ $35/мес только за инстанс.
- ❌ Долгоживущий worker = либо Cloud Run min-instances=1 (платим за idle), либо GKE.
- ❌ Сами рулим retry / DLQ.

### Layout: один проект staging+prod vs два

#### A. Один проект, env-prefix на ресурсах (`staging-*`, `prod-*`)

- ✅ Один билд credentials, один state bucket, проще управлять.
- ❌ Blast radius: ошибка в IAM — задевает оба окружения. GDPR DPIA проще
  доказывать на изоляции.
- ❌ Quotas общие — staging-эксперимент может выжрать prod-квоты.

#### B. Два проекта (`autotreegen-staging`, `autotreegen-prod`)

- ✅ Полная изоляция IAM, billing alerts отдельно, audit logs отдельно.
- ✅ Удалить staging = `gcloud projects delete` без риска для прод.
- ✅ Разные VPC, разные KMS keyrings — соответствует «privacy by design».
- ❌ Лишние +0$ — проектов в GCP сколько угодно, биллинг прозрачный.
- ❌ Чуть больше boilerplate в setup.

## Решение

- **Compute: Cloud Run v2.** Cold start приемлем, scale-to-zero на staging
  даёт почти-нулевой idle. GKE придёт только когда у нас будут реальные
  long-running ML-нагрузки (Phase 11+).
- **БД: AlloyDB Omni на GCE для staging, managed AlloyDB для прода.** Wire-
  совместимость гарантирует, что код staging работает в проде без правок.
  Прод пишем отдельным root-модулем терраформа в Phase 13.1, когда
  понадобится — сейчас 200 $/мес платить не за что.
- **Очереди: Cloud Tasks в проде, arq локально.** Абстракция —
  `parser_service.queue.enqueue_job()` смотрит на `PARSER_SERVICE_QUEUE_BACKEND={arq|cloud_tasks}`.
  Локально продолжаем юзать docker-compose Redis + arq-worker (быстрый dev-loop).
  Cloud Tasks вызывает HTTP endpoint самого parser-service, поэтому отдельного
  worker-процесса в проде нет.
- **Pub/Sub-стриминг прогресса: оставляем Redis (Memorystore Basic 1 ГБ).**
  Cloud Tasks не умеет publish-subscribe; SSE-канал `job-events:{job_id}` нам
  нужен и в проде. ~$35/мес — не критично.
- **Layout: два проекта.** `autotreegen-staging` и `autotreegen-prod`.
  Изоляция IAM перевешивает удобство одного state bucket. Этот PR создаёт
  только staging-root; прод-root = `environments/prod/` в Phase 13.1.

## Последствия

- ✅ Staging deploy = `terraform apply` + `gcloud run deploy ...` без k8s-знаний.
- ✅ Кодовая база приобретает abstraction queue → код переносим без рефактора.
- ✅ Минимальный bill при отсутствии трафика (Cloud Run idle = $0).
- ❌ Cold start 1–3 с на первом запросе после простоя. Лечим `min_instances=0`
   на staging (терпим), `min_instances=1` на prod-критичных эндпоинтах позже.
- ❌ AlloyDB Omni — наша забота: backup, security patches, kernel updates.
   Митигация: на VM enable-oslogin + IAP-only SSH; ежедневный `pg_dump` в GCS
   через cron внутри VM (TODO Phase 13.1).
- ❌ Pub/Sub Redis ($35/мес) — единственная фиксированная стоимость на staging.
   Альтернатива (выкинуть SSE и поллить через `GET /jobs/{id}`) — обсудим если
   биллинг прижмёт.

### Cost estimate (staging, EU region)

| Component | Idle (no traffic) | 100 active users / 1 import/day |
|---|---|---|
| Cloud Run × 4 services | $0 | $5–10 |
| AlloyDB Omni VM (e2-standard-2 + 50 GB SSD) | $30 | $30 |
| Memorystore Basic 1 GB (для SSE pub/sub) | $35 | $35 |
| Cloud Tasks | $0 | <$1 |
| GCS (3 buckets, ≤10 GB) | <$1 | $2 |
| Cloud NAT | $1 | $2 |
| Egress (заведомо мелкий) | $0 | $1 |
| Secret Manager (6 секретов) | <$1 | <$1 |
| Logging/Monitoring (free tier) | $0 | $0 |
| **Total** | **~$67/мес** | **~$80/мес** |

Прод-оценка (managed AlloyDB + LB + Cloud Armor): +$200–250/мес поверх. Туда
полезем в Phase 13.1.

### Что нужно сделать в коде

- ✅ `parser_service/queue.py` — добавить `enqueue_job()` abstraction.
- ✅ Cloud Tasks adapter (`google-cloud-tasks` dep).
- ✅ Cloud Run-friendly Dockerfiles (этот PR).
- 🟡 Phase 13.1: ежедневный `pg_dump` cron на AlloyDB Omni VM в GCS.
- 🟡 Phase 13.1: `environments/prod/` с managed AlloyDB и LB+Armor.
- 🟡 Phase 13.1: HTTP-endpoint `POST /internal/jobs/run-import` в parser-service
   как Cloud Tasks target (сейчас arq dispatches напрямую к python функции;
   в проде нужно через HTTP).

## Когда пересмотреть

- Cloud Run cold-start превышает 3 сек на p95 → рассмотреть `min_instances=1`
  или GKE Autopilot.
- AlloyDB Omni VM упирается в e2-standard-2 → апгрейд или переезд на managed.
- Импорты регулярно выходят за лимит 60 мин Cloud Run → Cloud Run jobs (batch)
  или GKE.
- Пользователей ≥ 1000 — пора закладывать managed AlloyDB и read replicas.
- ENG. требование «scale to N regions» → меняем root layout (multi-region GCS,
  read replicas, Anycast LB).

## Ссылки

- ADR-0001 — выбор стека (фиксирует GCP).
- ADR-0026 — arq как локальный движок очереди.
- ADR-0027 — Fernet at-rest encryption для FS OAuth токенов (потребляет
  `staging-fs-token-key` секрет).
- ADR-0028 — bulk-compute rate-limiting (поверх абстракции очередей).
- ROADMAP.md §17 (Phase 13).
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [AlloyDB Omni docs](https://cloud.google.com/alloydb/omni/docs)
