# ADR-0027: FamilySearch OAuth token storage (Phase 5.1)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `integration`, `oauth`, `security`, `familysearch`, `phase-5`

## Контекст

Phase 5.0 (ADR-0011) завёл OAuth 2.0 Authorization Code + PKCE для
FamilySearch. Phase 5.1 (ADR-0017) — синхронный импорт через
`POST /imports/familysearch`, в котором каждый раз приходил
`access_token` от пользователя; сервис **не сохранял** его.

Этот ADR закрывает следующий шаг: **полный server-side OAuth flow**,
в котором parser-service сам инициирует авторизацию (`GET
/familysearch/oauth/start`), принимает callback от FamilySearch
(`GET /familysearch/oauth/callback`) и сохраняет токен на стороне
сервера, чтобы:

1. Async-импорт через arq (Phase 3.5) мог дёргать FamilySearch без
   передачи `access_token` через очередь (msgpack-payload в Redis —
   plaintext по сути).
2. UI «Connect FamilySearch» / «Preview» / «Import» имели единый
   жизненный цикл: подключился один раз → preview → confirm → импорт;
   токен не вводится руками.
3. Refresh-токен (90 дней native app) переживал отдельные импорты —
   user не должен повторять OAuth dance каждый раз.

Ограничения и силы давления:

- **GDPR / privacy.** FamilySearch token = доступ к личным данным
  пользователя на FamilySearch. Утечка БД = риск персональных данных.
- **Размер команды.** Один разработчик, нет dedicated security ops.
  Cloud KMS / Vault — лишние моргания, которые тормозят MVP.
- **Прод-инфра ещё не выбрана.** Phase 12 (deploy) фиксирует GCP, но
  на момент написания ADR cluster не поднят. KMS-привязка на этом
  этапе была бы дизайн-долгом «на бумаге».
- **CLAUDE.md §3.5 «Privacy by design».** ДНК — special category
  (Art. 9 GDPR), но FS-токен — обычные personal data; уровень защиты
  — «encrypted at rest with rotated key», а не FIPS-140 hardware module.
- **Совместимость с Phase 12.** Решение должно мигрироваться в Cloud
  KMS / Secret Manager без переписывания всего impórt-кода.

## Рассмотренные варианты

### Вариант A — Application-level Fernet (recommend для MVP)

Колонка `users.fs_token_encrypted` (`text`, nullable). Шифрование —
`cryptography.Fernet` (AES-128-CBC + HMAC-SHA256 + version byte) с
ключом из ENV `PARSER_SERVICE_FS_TOKEN_KEY` (32-байтный URL-safe base64,
тот же формат, что генерирует `Fernet.generate_key()`).

Хранится JSON-payload `{access_token, refresh_token, expires_at, scope}`
после `Fernet.encrypt(json.dumps(...).encode())`.

Плюсы:

- ✅ Zero сторонних managed services. Локальный dev (docker-compose),
  CI и прод-staging работают одинаково.
- ✅ Ключ в ENV — стандартный pattern для FastAPI / pydantic-settings.
  Совпадает с тем, как мы ставим `DATABASE_URL`.
- ✅ Ротация ключа решается через `MultiFernet([new_key, old_key])` —
  стандартный приём cryptography library.
- ✅ Тесты не требуют поднимать Vault — фикстура подставляет
  фиксированный ключ.

Минусы / риски:

- ⚠️ Ключ в ENV / `.env` — это плохая практика для финального прода.
  Нужен миграционный путь к KMS (см. «Когда пересмотреть»).
- ⚠️ Один компрометированный admin = весь корпус токенов. Нет
  HSM-обвязки.
- ⚠️ Ключ должен быть **разный** на dev / staging / prod. Это нужно
  записать в README и в template `.env.example`.

### Вариант B — Cloud KMS Envelope Encryption

`users.fs_token_encrypted` хранит DEK-encrypted токен; сам DEK
зашифрован KMS-master key. Decrypt — KMS-call на runtime.

Плюсы:

- ✅ Master key никогда не лежит вне HSM.
- ✅ Audit log на сторону Cloud (kto когда decrypt'ил).
- ✅ Ротация master без миграции данных (DEK переоборачивается).

Минусы:

- ❌ Требует выбранную cloud-platform — Phase 12 ещё не закрыта.
- ❌ В CI / локальном dev нужен fake KMS (LocalStack / fake-gcs) —
  +1 движущаяся часть.
- ❌ KMS round-trip на каждый decrypt (~20–50 мс) на горячем пути
  фонового импорта. Терпимо, но добавляет latency.
- ❌ Цена: GCP KMS считает ~$0.03 / 10 000 операций. На нашем масштабе
  копейки, но дополнительная биллинг-зависимость.

### Вариант C — Plaintext с access-control на уровне БД

`users.fs_token_encrypted` без шифрования, отдельный role с GRANT
только для parser-service.

Плюсы:

- ✅ Просто.

Минусы:

- ❌ Backup БД = plaintext-токены в дампе.
- ❌ DBA / sql-консоль = доступ к токенам без аудита.
- ❌ Не закрывает «GDPR Art. 32 — защита от случайного раскрытия».

## Решение

**Принимаем вариант A — Application-level Fernet** для Phase 5.1
с обязательным миграционным roadmap'ом на Cloud KMS в Phase 12.

Конкретика:

- **Колонка:** `users.fs_token_encrypted text NULLABLE`. Содержит
  Fernet ciphertext URL-safe base64. NULL = пользователь не подключал
  FS-аккаунт.
- **Payload format (внутри ciphertext, JSON):**

  ```json
  {
    "access_token": "eyJraW...",
    "refresh_token": "...",
    "expires_at": "2026-04-28T15:30:00+00:00",
    "scope": "openid profile",
    "fs_user_id": "MMMM-MMM",
    "stored_at": "2026-04-28T14:30:00+00:00"
  }
  ```

- **Ключ:** ENV `PARSER_SERVICE_FS_TOKEN_KEY` (`Fernet.generate_key()`
  output). На локальном dev — закоммиченный в `.env.example` пример
  (явно «for dev only»); на staging/prod — секрет в Cloud Secret
  Manager / k8s Secret, инжектится в pod env.
- **Ротация:** `MultiFernet([new, old])` — старый ключ в `OLD_KEY`,
  новый в `KEY`. После переезда всех row'ов на новый ключ
  (фоновый pass) — `OLD_KEY` удаляется. Документировано в
  Phase-12 brief.
- **Refresh:** перед использованием токена `parser_service` проверяет
  `expires_at` (с 60-секундным запасом). Если протух — вызывает
  `FamilySearchAuth.refresh()` и перезаписывает row. Если рефреш
  упал с `AuthError` — стираем row (`fs_token_encrypted = NULL`)
  и просим user'а пройти OAuth заново (UI редиректит на `connect`).
- **Логирование:** **никогда** не логируем сам access/refresh-токен.
  Для traceability — `sha256(access_token)[:8]` (как уже принято
  в Phase 5.1, см. `_token_fingerprint`).
- **CSRF / state:** OAuth `state` хранится в Redis с TTL 10 минут,
  ключ `fs:oauth:state:{state}` → `{user_id, code_verifier,
  redirect_uri}`. Не в БД, чтобы не плодить short-lived таблицу.
- **CSRF cookie:** сам state-token попадает к user'у в `Set-Cookie:
  fs_oauth_state=<state>; HttpOnly; Secure; SameSite=Lax`. Callback
  сравнивает cookie со значением query-param `state` и
  немедленно удаляет cookie.
- **Удаление:** `DELETE /familysearch/disconnect` обнуляет колонку
  и стирает Redis state. Вызывается, если user явно отключает FS
  (Phase 5.2 UI), либо при detected-revoke (refresh вернул
  `invalid_grant`).

## Соответствие принципам CLAUDE.md

- **§3.5 Privacy by design:** at-rest шифрование на app-level, явный
  consent через OAuth flow, чистая точка удаления.
- **§3.3 Provenance everywhere:** провенанс импортированных персон
  получает `provenance.fs_user_id` (sha256 fingerprint, не fs id) —
  даёт traceability «откуда импортировал», не раскрывая сам fs_user.
- **§5 «никаких автоматических merge»:** токен сам по себе ничего
  не merge'ит. Решение про conflict-resolution живёт в ADR-0017.

## Когда пересмотреть

- **Phase 12 (deploy):** мигрируем на Cloud KMS envelope encryption,
  как только cluster поднят. ENV-key остаётся как fallback для
  локального dev'а.
- **Если появится >1 OAuth-провайдер** (Geni, MyHeritage в
  Phase 5.3+): выносим колонку в отдельную таблицу
  `oauth_credentials(user_id, provider, ciphertext)` — иначе
  `users` обрастёт `*_token_encrypted` для каждой платформы.
- **Если появится требование SOC2 / HIPAA:** пересматриваем под
  HSM-only ключевой материал.

## Альтернативы, которые отвергли

- **Cookies-only сессия с токеном в JWT.** Проигрывает A: токен
  виден на стороне клиента, утекает через `localStorage` / расширения
  браузера.
- **Хранение в Redis.** Reset Redis = потеря всех токенов = всем
  user'ам нужно заново пройти OAuth. Слишком хрупко.
