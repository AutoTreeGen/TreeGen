# ADR-0039: Transactional email — Resend + idempotent dispatch

- **Status:** Accepted (Phase 12.2a — partial; 12.2b extends)
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `email`, `transactional`, `phase-12.2`, `gdpr`, `idempotency`

**Phase 12.2a (this PR) ships:**

- email-service core (FastAPI, Pydantic, jinja2 sandboxed, Resend HTTP wrapper).
- `email_send_log` ORM + alembic migration 0017.
- `users.email_opt_out` column.
- Three kinds: `welcome`, `payment_succeeded`, `payment_failed`.
- en templates only; ru templates are en-copies as placeholders (12.2b
  replaces with real translations).
- Stub `parser_service.services.email_dispatcher.send_transactional_email`
  (5-line `log.info`) — Agent 4 / Agent 5 unblocked, real HTTP wiring in 12.2b.

**Phase 12.2b (deferred) will add:**

- docker-compose entry, Dockerfile, Terraform Cloud Run module + Resend
  Secret Manager binding.
- Remaining kinds: `share_invite`, `export_ready`, `erasure_confirmation`,
  `password_reset_notice`.
- Real ru translations (jinja content reused from 12.2a en files).
- Replace email_dispatcher stub with real HTTP call to email-service.
- Wire-up call-sites: Stripe webhook → payment_*, Clerk webhook → welcome,
  tree-share endpoints → share_invite (Agent 4), erasure pipeline →
  erasure_confirmation (Agent 5).

## Контекст

Phase 8.0 (ADR-0024) построил `notification-service` с in-app каналом
(колокольчик в шапке UI) и подготовил расширение на email/push в
будущем. Фаза 12.0 (ADR-0034 Stripe billing) сделала первый
production-каритичный flow, для которого in-app недостаточно: оплата
прошла → пользователь должен получить email-confirmation **до** того,
как откроет сайт. То же для failed payments (нужно «починить карту до
того, как Pro отвалится»), для signup welcome'а (Phase 4.10 Clerk),
для GDPR erasure-confirmation (Phase 13.x), для tree-sharing invite
(Phase 11.0).

In-app колокольчик и transactional email — два разных канала с
разной семантикой:

| | In-app (`notification-service`) | Email (this ADR) |
|---|---|---|
| Аудитория | only authenticated user-в-сессии | даже неактивные user'ы |
| Latency | <100ms (одна row INSERT) | seconds (через провайдера) |
| Read semantics | unread → read state | fire-and-forget |
| Idempotency | hash(user_id, event_type, ref_id) с 1h окном | full UNIQUE на caller-supplied key |
| Failure | log warning, drop | persist FAILED row, retry |
| Cost | нулевой | per-send fee |

Это две разные таблицы и два разных микросервиса. Никакая cross-канал
маршрутизация в Phase 12.2 не делается — сначала ровный transactional
email, расширения (digest emails, marketing, push) — отдельные ADR.

## Рассмотренные варианты

### Вариант A — Resend (выбран)

- ✅ DX: одна `POST /emails` ручка, минимум boilerplate.
- ✅ Free tier 3,000 emails/месяц + $20/мес за 50,000 — хватает на
  весь Phase 12.x под Beginner tier scale.
- ✅ EU и US data-residency опции (важно для GDPR).
- ✅ React-based templates (если захотим переходить с jinja, это
  open path).
- ✅ Webhook'и для bounces / complaints (Phase 12.x retention purge).
- ❌ Молодой провайдер (2023). Может закрыться или менять pricing.
  Mitigation: provider-agnostic dispatcher (см. ниже §«Schema»).

### Вариант B — AWS SES

- ✅ Дёшево ($0.10 / 1,000 emails) и стабильно.
- ✅ Полная инфра-интеграция через AWS account.
- ❌ Sandbox-mode requires manual verification per recipient — не
  работает для public-facing сервиса до production-access одобрения
  AWS (≤2 недели lead-time).
- ❌ Нет out-of-box bounce-handling, нужно подписаться на SNS.
- ❌ Нет dashboard со статистикой; только CloudWatch.
- ❌ Все наши GCP, добавление AWS account только ради email — overhead.

### Вариант C — Postmark

- ✅ Reputation для transactional (отделяют broadcast от transactional).
- ❌ $15 / 10,000 emails — дороже Resend на сравнимом объёме.
- ❌ Нет EU residency option (на дату ADR).

### Вариант D — Self-hosted SMTP (Postfix + DKIM)

- ❌ Inbox placement требует месяцы настройки SPF/DKIM/DMARC + IP
  warming + bounce handling.
- ❌ Caretake overhead для одного-двух разработчиков.
- Allowed как fallback (Mailcatcher / Mailpit для local dev), но не
  для production.

## Решение

Выбран **Вариант A (Resend)**.

### Schema

Новая таблица `email_send_log`:

```python
class EmailSendLog(IdMixin, TimestampMixin, Base):
    idempotency_key: str  # UNIQUE — caller-supplied
    kind: str              # EmailKind enum value
    recipient_user_id: UUID FK users.id ON DELETE CASCADE
    status: str            # EmailSendStatus: queued|sent|failed|skipped_optout
    provider_message_id: str | None  # re_* from Resend
    error: str | None
    params: jsonb          # non-PII payload, redacted on insert
    sent_at: datetime | None
```

Plus: `users.email_opt_out: bool`. Email-service ставит
`status=skipped_optout` без вызова Resend.

Идемпотентность через UNIQUE на `idempotency_key` — тот же pattern,
что в ADR-0034 (Stripe webhook events). Ровно одна copy email'а
на ключ.

### Architectural shape

```text
caller (billing-service / parser-service / future agents 4&5)
       │
       │ POST /email/send {kind, user_id, idempotency_key, params}
       ▼
email-service ────┬──► users (read email + locale + opt_out)
                  ├──► email_send_log (idempotency check, persist)
                  ├──► render template (jinja2 sandboxed)
                  └──► Resend HTTPS (httpx async, no SDK)
```

`parser_service.services.email_dispatcher.send_transactional_email(...)`
— shared HTTP-client helper, fire-and-forget с `ok: bool` return.
Caller'ы не raisят на email-failure — основная транзакция важнее
письма.

### DNA hard rule

DNA-данные **никогда** не пересекают границу email-service. Это
непреложное правило (CLAUDE.md §3.5 «Privacy by design»). Реализация —
defense-in-depth:

1. `services/redaction.py::redact_email_params` фильтрует ключи,
   содержащие `dna|segment|rsid|kit|cm|haplotype|genotype|snp|
   chromosome` (case-insensitive) → `[redacted]` + warning лог.
2. Allowlist для разрешённых ключей в `params`. Любой неизвестный ключ
   → `[redacted]`.
3. Code-review всех call-site'ов. Reviewers смотрят: «передаёт ли
   caller случайно DNA-related поля?»

### Privacy

- Email-адрес получателя берётся из `users.email` на send-time, **не**
  сохраняется в `email_send_log`. При rename/erasure user'а письмо
  идёт на актуальный адрес, а старые log'и не содержат stale
  reference.
- `params` redact'ится до insert: только non-PII allowlist (amounts,
  dates, locale, plan_name, brand). PII должно приходить из других
  источников (`user.email`, `user.display_name`) — не из `params`.
- `users.email_opt_out=True` — глобальный opt-out. Phase 12.x
  расширит на per-kind preferences (как `notification_preferences`).
  Сейчас один флаг для всех transactional email'ов.

### Templates

- Jinja2 SandboxedEnvironment (защита от RCE через user-controlled
  params).
- `select_autoescape(["html"])` — auto-escape для HTML; txt без escape.
- StrictUndefined — отсутствующая переменная → exception, а не silent
  empty. Лучше падать в тестах, чем посылать письмо с `{{ ... }}`.
- Локали: `en`, `ru` (mirror `apps/web` next-intl, но **server-side**
  jinja, не frontend локали — отдельная директория).
- Fallback на `en` если запрошенная locale нет.
- Расположение: `services/email-service/templates/{kind}/{locale}.{html,txt,subject.txt}`.

### Idempotency convention

Caller-supplied `idempotency_key` строится так:

| Kind | Key |
|---|---|
| `welcome` | `welcome:{clerk_user_id}` |
| `payment_succeeded` | `{stripe_event_id}` (UNIQUE per Stripe event) |
| `payment_failed` | `{stripe_event_id}` |
| `share_invite` | `invite:{invitation_id}` |
| `export_ready` | `export:{export_job_id}` |
| `erasure_confirmation` | `erasure:{user_id}` |
| `password_reset_notice` | `pwreset:{clerk_event_id}` |

Ключ переполняется max 255 символов (`String(255)` constraint). Если
будущему kind'у нужно больше — sha256 hash.

### Feature flag для local dev / CI

`EMAIL_SERVICE_ENABLED=false` (default `false` локально, `true`
на staging/prod) переводит сервис в bypass-mode:

- `dispatch_email` пишет `status=skipped_optout` для всех вызовов.
- Resend SDK не вызывается → нет real-email на dev-данных.
- `/healthz` остаётся 200 (resend_reachable=true short-circuit без ключа).

### Resilience

Phase 12.2:

- Sync HTTP к Resend с 10s timeout. Failure → `status=failed` row,
  без 5xx caller'у (caller получит 200 + `status=failed`, может
  ретраить с тем же key — мы вернём cached failure → caller
  поменяет key и попробует ещё раз).
- Retry handled by caller (e.g. arq job для signup-welcome).

Phase 12.x:

- Background-worker (arq) с автоматическими retry'ами bounced/failed.
- Webhook-обработчик для Resend complaint/bounce events → автоматический
  `email_opt_out=True` или per-address suppression.

## Последствия

**Положительные:**

- Один маленький сервис закрывает все transactional-email use-cases
  ROADMAP'а до Phase 14.
- Каждый каллер получает ровно одно письмо на event благодаря UNIQUE
  `idempotency_key`.
- Privacy-boundary phys'ически отдельный микросервис — code-review
  концентрируется на одном `dispatch_email` функции и redaction'е.

**Отрицательные / стоимость:**

- Один additional service в docker-compose и Cloud Run.
- Resend lock-in (SDK-less, но шаблоны и call-sites привязаны).
  Mitigation: `services/resend_client.py` — единственное место где
  упоминается провайдер. Замена на SES — переписать ровно этот файл.

**Риски:**

- Resend incident → транзакционные письма выпадают. UI показывает
  `status` в settings/billing для Stripe events, так что lost emails
  не лочат пользователя. Phase 12.x: SLO + alert.
- Заполнение `email_send_log` неограниченно. Phase 12.x: 90-day
  TTL prune.
- Если Agent 4 (tree-sharing) или Agent 5 (erasure) wire share_invite/
  erasure_confirmation, **обязательно нужен code-review на DNA-rule**.
  Reviewer проверяет: ничего из `params` не выглядит DNA-related.

## Когда пересмотреть

- Если месячный объём пройдёт 50,000 и Resend pricing станет дороже
  $200/месяц — пересмотр в пользу SES.
- Если нужны marketing emails (mass-send, segments) — отдельный
  ADR + отдельный сервис (или платформа SendGrid).
- Если потребуется in-app preview email'а перед send'ом —
  templates переехать в БД, добавить admin-UI.

## Ссылки

- ADR-0024 (notification-service architecture) — родительский для
  in-app канала, отделён от email.
- ADR-0029 (notification delivery model) — async enqueue + per-user
  prefs. Phase 12.x перенесёт email в тот же arq-worker model.
- ADR-0034 (payments architecture) — caller для `payment_*` kind'ов.
- Resend docs: <https://resend.com/docs>.
