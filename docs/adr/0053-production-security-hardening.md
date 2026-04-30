# ADR-0053: Production security hardening (Phase 13.2)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @AutoTreeGen
- **Tags:** `security`, `infrastructure`, `phase-13.2`

## Контекст

Phase 13.1 (a/b/c) закрыла observability. До prod-launch остаётся блок security
hardening: CORS, rate limiting, request-size limits, security headers (HSTS,
X-Frame-Options и др.), CSP для Next.js. Без этого первый же staging-trial
рискует получить:

- абуз на public endpoints (`/waitlist`, `/webhooks/clerk` HMAC-проверка
  не защищает от storm'а);
- межсайтовое внедрение через iframe / form / inline script;
- утечку Referer на внешние домены;
- 1+ ГБ uploads на `/imports/*` пока application успевает дотянуться до
  валидации.

Все 5 deployable сервисов (parser, dna, notification, email, telegram-bot) —
FastAPI + uvicorn за Cloud Run. Apps/web — Next.js 15 (App Router) на Cloud Run.
Мы хотим **единую wiring-функцию**, чтобы сервис не дублировал middleware-стек
и не расходился по версиям.

## Рассмотренные варианты

### Вариант A — Каждый сервис вешает middleware вручную

- ✅ Никакой shared-зависимости, явно в `main.py` каждого сервиса.
- ❌ Drift'ит за 2–3 PR'а: один сервис обновит CSP, другой нет. Уже
  парсер-сервис имел свой `CORSMiddleware(allow_origins=["http://localhost:3000"])`
  с `allow_credentials=False` — для prod это сломанный конфиг.
- ❌ Harder to test consistency.

### Вариант B — Helper в `shared_models.security`, единый вход `apply_security_middleware(app, ...)`

- ✅ Один источник правды, `pwsh scripts/check.ps1` ловит drift.
- ✅ Существующий паттерн (см. `shared_models.observability`, ADR-0032).
- ✅ Тестируется централизованно + per-service smoke-тест на наличие headers.
- ❌ Доп. зависимость `shared-models[security]` → `slowapi` в каждом образе
  (~200 KB pip wheel, transitively `limits` ~120 KB). Acceptable.

### Вариант C — Reverse proxy / Cloud Armor для CORS+headers, app только для rate limit

- ✅ Headers применяются на edge, нагрузки на Python нет.
- ❌ Cloud Armor требует Global LB, мы пока на Cloud Run direct. Phase 13.0
  явно отложила Global LB до Phase 14+ (см. ADR-0031 §«Edge networking»).
- ❌ CORS preflight всё равно должен ответить корректно — proxy усложняет
  отладку, неоднородный с локальным dev.

## Решение

Выбран **Вариант B** — `shared_models.security.apply_security_middleware(app, service_name=...)`.

### Rate limit — slowapi с in-memory storage

slowapi выбран потому что:

- Готовый Starlette-middleware, проверенный в FastAPI-сообществе.
- Per-route декораторы (`@limiter.limit("10/minute")`) для строгих ручек.
- Storage backends подключаемы — `memory://` сейчас, `redis://` потом.

**In-memory trade-off.** Cloud Run автомасштабируется до N инстансов; каждый
держит свой счётчик. Эффективный лимит при N=4 = 4× per-IP лимит. Это
acceptable для current scale (waitlist, MVP traffic) — а distributed Redis
backend подключим в Phase 13.3 если эффективный лимит начнёт пропускать
абуз. Альтернатива (Redis сразу) добавляет runtime-зависимость на каждый
сервис → Redis должен быть HA → Memorystore HA = ~$50/mo overhead. Не
оправдано пока MVP.

**Тариф per-route:**

- Default: `100/minute` per IP (большинство ручек).
- Auth/refresh/sign-in: `10/minute` (явно через `@limiter.limit("10/minute")`).
- Webhooks (`/webhooks/clerk`, `/webhook/telegram`): `30/second` (легитимный
  burst при массовой регистрации).

### Request size limit

Custom middleware (`MaxBodySizeMiddleware`) на основе `Content-Length` header.
**1 МБ default, 200 МБ для `/imports/*`** (типичный GEDCOM до 150 МБ —
Ztree.ged). Не читаем body заранее (memory-pressure attack vector); просто
проверяем заголовок. Chunked-uploads без `Content-Length` обходят
проверку — это accepted (защита через uvicorn `--limit-request-line` и
deadline).

### Security headers

Стандартный пакет:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
  (только для https; не ломаем local dev на http).
- `X-Content-Type-Options: nosniff`.
- `X-Frame-Options: DENY` (для legacy-clientов; CSP `frame-ancestors 'none'` —
  modern equivalent).
- `Referrer-Policy: strict-origin-when-cross-origin`.
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`.

### CSP для Next.js

CSP жёсткий, **но без nonce'а в этой фазе** (compromise — см. roadmap ниже).

```text
default-src 'self';
script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.clerk.com https://*.clerk.accounts.dev https://challenges.cloudflare.com;
style-src 'self' 'unsafe-inline';
img-src 'self' blob: data: https:;
font-src 'self' data:;
connect-src 'self' https://*.clerk.com https://*.clerk.accounts.dev wss://*.clerk.com $NEXT_PUBLIC_API_URL;
frame-src 'self' https://*.clerk.com https://challenges.cloudflare.com;
frame-ancestors 'none';
form-action 'self';
base-uri 'self';
object-src 'none';
upgrade-insecure-requests
```

`'unsafe-inline'` для `script-src` — необходимый для Next.js hydration.
`'unsafe-eval'` — Next.js dev/prod обоим. Это слабее, чем nonce-based
строгий CSP с `'strict-dynamic'`, но избегает ловушки middleware-нонса при
нашей пары `clerkMiddleware + next-intl plugin`.

Image domains whitelist через `next.config.ts → images.remotePatterns`:
`*.clerk.com`, `img.clerk.com`, `www.gravatar.com`.

### CSP nonce roadmap (Phase 13.3)

Когда вешаем строгий nonce-based CSP:

1. Сгенерировать nonce в `middleware.ts` (рядом с Clerk + i18n).
2. Передать через response header (`X-CSP-Nonce`) → React-сервер-компоненты
   читают `headers()` API → пробрасывают в `<Script nonce={...}>` и
   `<ClerkProvider nonce={...}>`.
3. Заменить `'unsafe-inline'` на `'nonce-...' 'strict-dynamic'`.

Эта работа — отдельная PR (~150 LOC + e2e-валидация); не блокирует launch.

## Последствия

**Положительные:**

- Все 5 сервисов wired одной строкой; новые сервисы добавляются без
  copy-paste.
- pwsh `scripts/check.ps1` + per-service smoke-тест ловят drift.
- HSTS preload-list eligible после 6+ месяцев непрерывной работы.

**Отрицательные / стоимость:**

- `shared-models[security]` extra → +1 транзитивная зависимость
  (slowapi+limits) в каждом prod-образе. ~320 KB suммарно. Acceptable.
- CSP без nonce — на 1 step weaker, чем industry best practice; задокументировано.

**Риски:**

- slowapi `memory://` storage — эффективный лимит зависит от количества
  Cloud Run-инстансов. Митигация: Phase 13.3 миграция на Redis backend,
  если abuse metrics покажут проблему.
- CORS `allow_origins` теперь читается из env `CORS_ORIGINS` —
  unconfigured deploy (без env) даёт `http://localhost:3000` (broken
  prod). Митигация: deploy-staging.yml явно указывает `CORS_ORIGINS=https://app.autotreegen.dev`.

**Что сделать в коде:**

- ✅ `packages/shared-models/src/shared_models/security.py` — helper.
- ✅ `[security]` extra в shared-models pyproject.toml (`slowapi>=0.1.9`).
- ✅ 5 сервисов: import + `apply_security_middleware(app, service_name=...)`.
- ✅ 5 сервисов: `pyproject.toml` → `shared-models[security]`.
- ✅ `apps/web/next.config.ts` — security headers + CSP + image domains.
- ✅ Тесты — unit на helper + per-service smoke + integration (rate-limit fires).

## Когда пересмотреть

- Когда `429` в abuse-metrics превысит 0.1 % от всех запросов — апгрейд
  rate-limit storage на Redis (shared счётчик).
- Когда добавляем новый внешний CDN (например, аналитика) — CSP
  пересматривается, ADR обновляется.
- Когда мигрируем за Global LB / Cloud Armor — часть headers/CSP
  переедут на edge (см. вариант C). ADR обновляется со ссылкой на новый.
- Когда Clerk начнёт поддерживать стандартный `nonce` пропс без
  middleware-обвязки (или когда соберёмся писать middleware-нонсы) —
  Phase 13.3, ужесточаем CSP до strict-dynamic.

## Ссылки

- ADR-0031: GCP deployment architecture (Cloud Run, networking).
- ADR-0032: Secrets management (HSTS preload, OIDC).
- ADR-0033: Auth & Clerk integration.
- ADR-0035: i18n strategy (next-intl middleware).
- OWASP Secure Headers Project: <https://owasp.org/www-project-secure-headers/>
- slowapi docs: <https://slowapi.readthedocs.io/>
- MDN CSP: <https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP>
