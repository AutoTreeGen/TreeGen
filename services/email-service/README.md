# email-service

Phase 12.2 transactional email-сервис для AutoTreeGen. Архитектура — **ADR-0039**.

## Что делает

- Принимает `POST /email/send` с `{kind, recipient_user_id, idempotency_key, params}`.
- Рендерит jinja2-шаблон из `templates/{kind}/{locale}.{html,txt,subject.txt}`.
- Отправляет через **Resend** (`RESEND_API_KEY` в env).
- Идемпотентен: повторный POST с тем же `idempotency_key` → cached
  result, без второй отправки. Ровно как webhook-idempotency у
  billing-service (ADR-0034 §«Webhook security»).
- Респектит `users.email_opt_out` — `status=skipped_optout` без вызова
  Resend.

## Поддерживаемые `kind` (`shared_models.enums.EmailKind`)

- `welcome` — Phase 4.10 Clerk signup.
- `payment_succeeded` / `payment_failed` — Phase 12.0 Stripe webhook.
- `share_invite` — Phase 11.0 tree share (call-site by Agent 4).
- `export_ready` — Phase 4.x async GEDCOM export.
- `erasure_confirmation` — Phase 13.x GDPR right-to-erasure (Agent 5).
- `password_reset_notice` — Phase 4.10 Clerk password-reset hook.

## ENV

- `EMAIL_SERVICE_RESEND_API_KEY` — `re_*` от Resend.
- `EMAIL_SERVICE_RESEND_FROM` — `noreply@smartreedna.com` (must be a
  verified Resend domain).
- `EMAIL_SERVICE_BRAND_NAME` — `SmarTreeDNA` (footer).
- `EMAIL_SERVICE_SUPPORT_EMAIL` — `support@smartreedna.com` (footer).
- `EMAIL_SERVICE_DATABASE_URL` — async-DSN postgres.
- `EMAIL_SERVICE_ENABLED` — feature flag (default `true`). Если
  `false`, `/send` ставит `status=skipped_optout` для всех (отладка
  без реальных писем). См. ADR-0039 §«Feature flag».

## Privacy / DNA-rule (ADR-0039 §«DNA hard rule»)

- DNA-данные **никогда** не пересекают границу email-service.
  Никакие segments, rsids, kit-IDs, cM-значения не должны попадать
  в `params`. Ревьюер обязан проверить call-sites.
- `params` хранит только non-PII payload (suммы, даты, locale, plan).
  Email-адрес получателя **не сохраняется** — берётся из
  `users.email` на send-time.

## Запуск локально

```bash
uv run uvicorn email_service.main:app --reload --port 8005
```
