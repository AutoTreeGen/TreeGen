# ADR-0012: DNA processing — privacy & architecture

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `dna`, `privacy`, `gdpr`, `security`, `phase-6`

## Контекст

Phase 6 открывает работу с DNA-данными. По GDPR Art. 9 это **special
category personal data** — режим строже, чем для генеалогии: явный consent,
отдельная политика хранения, право на удаление с hard-delete, минимизация
обработки. CLAUDE.md §3.5 («Privacy by design») и §5 («запреты») задают
рамку, но не отвечают на инженерные вопросы:

1. **Где и как хранить raw DNA-файлы?** Размер от 5 до 50 МБ на kit, формат
   зависит от провайдера (23andMe TSV, Ancestry TSV, MyHeritage CSV, FTDNA CSV).
2. **Кто владеет ключом шифрования?** Если ключ у нас — мы технически можем
   читать данные пользователя; это ослабляет «privacy by design» и расширяет
   blast radius при компрометации сервера.
3. **Где проходит граница между анализом (вычисления) и хранением?**
   Парсеры — это pure functions; БД-сервис — это persistence. Если они
   живут в одном пакете, любой тест, импортирующий parsers, тянет за собой
   storage-зависимости (cryptography, БД-клиент).
4. **Что попадает в логи?** Современные стэки (FastAPI + structlog + Sentry)
   логируют request bodies / arguments по умолчанию. Один SNP в exception
   stack trace — это уже утечка genetic information.
5. **Какие форматы поддерживаем в Phase 6.0?** ADR-0009 уже зафиксировал
   стратегию интеграций (FamilySearch, Geni; DNA gap закрывается через
   GEDmatch в Phase 6.3). Phase 6.0 — только parsers raw-файлов от
   четырёх крупнейших direct-to-consumer провайдеров.

Силы давления:

- **GDPR Art. 9** (special category) и Art. 17 (right to erasure):
  hard-delete, audit trail, явный consent, минимальные сроки хранения.
- **Zero-knowledge как differentiator.** Конкуренты (Ancestry, MyHeritage)
  владеют ключами. Если AutoTreeGen изначально строится без серверного
  доступа к raw DNA — это и compliance, и marketing-аргумент.
- **AI-агентский режим разработки.** Вероятность, что один из агентов в
  отладочном rush напишет `logger.info(snp)` или `print(content)`, выше
  нуля. Privacy guards должны быть **в коде** (linter rule, pre-commit hook,
  unit-test), а не только в политике.
- **Phase 6.0 timeline.** Один пакетный скелет + два парсера, ~неделя.
  Ключи / encryption / БД-сервис — Phase 6.1, отдельный ADR.

## Рассмотренные варианты

### Вариант A — Application-level encryption + per-user key (zero-knowledge)

Raw DNA-файлы шифруются на клиенте (или на сервере с user-derived ключом,
который **не хранится** server-side). Ключ derive-ится через PBKDF2 / Argon2
из user passphrase, удерживается в session memory во время analysis,
сбрасывается на logout. Postgres хранит ciphertext, server не может
расшифровать без активной пользовательской сессии.

- ✅ Compromise сервера не раскрывает DNA — только ciphertext.
- ✅ Соответствует «Privacy by design» в полной мере.
- ✅ Отвечает на «зачем доверять AutoTreeGen» лучше любого маркетинга.
- ❌ UX-стоимость: пользователь обязан помнить passphrase, нет recovery
  без потери данных. Mitigation — recovery code на момент upload, который
  пользователь хранит сам.
- ❌ Background jobs (matching, AutoCluster) требуют активной сессии или
  ephemeral session-token-derived key с TTL. Дополнительная сложность.
- ❌ Реализация (KDF, key wrapping, session-bound decrypt) — Phase 6.1,
  отдельный ADR с key management deep-dive.

### Вариант B — Postgres TDE / диск-уровень

Полагаемся на встроенное шифрование диска (GCP CMEK, AlloyDB encryption
at-rest). Application работает с plaintext.

- ✅ Минимум кода. Прозрачно для приложения.
- ✅ Защищает от физической кражи диска.
- ❌ **Не защищает от компрометации приложения** — а это и есть основной
  threat model для SaaS. Любой SQL-injection / RCE → plaintext DNA.
- ❌ Сервер технически владеет данными → не zero-knowledge.
- ❌ Не соответствует duculus «special category» Art. 9 в строгом
  прочтении (GDPR ожидает технические меры выше дискового шифрования).

### Вариант C — Никакого encryption (пользователь сам решает)

Хранится plaintext, политика «вы доверяете нам».

- ✅ Простейшая реализация.
- ❌ Прямое нарушение CLAUDE.md §3.5 и GDPR Art. 9. Нерассматриваемо.
- ❌ Любой incident — public-disclosure обязательство.

### Вариант D — Совмещённый пакет (анализ + storage)

`packages/dna-analysis/` содержит и парсеры, и БД-слой, и encryption-логику.

- ✅ Один пакет — простая навигация.
- ❌ Тесты парсеров требуют БД и cryptography в зависимостях. Долгие
  CI-прогоны, лишние deps в pure-functions модуле.
- ❌ Любой импорт `dna_analysis.parsers` потенциально цепляет storage-код,
  где легче случайно залогировать чувствительное.
- ❌ Нет архитектурного барьера — privacy guards держатся только на
  дисциплине ревью.

## Решение

Принят **гибрид Вариантов A + раздельная архитектура (анти-D)**.

**1. Encryption strategy — Вариант A с уточнениями.**

- DNA raw файлы хранятся **зашифрованными** application-level (AES-256-GCM).
- Per-user encryption key derive-ится из user passphrase (Argon2id, не PBKDF2 —
  более устойчив к GPU-атакам на момент 2026).
- Decrypt происходит **только в memory** во время активной analysis-сессии.
- Server **никогда не хранит** plaintext key и не отправляет его наружу.
- Recovery: одноразовый recovery-code, выдаваемый пользователю в момент
  первого upload и существующий только на стороне пользователя.
- **Реализация шифрования — Phase 6.1**, отдельный ADR с key-management
  deep-dive (KDF parameters, key wrapping, session-bound TTL, rotation).
  Phase 6.0 декларирует контракт, но не реализует его.

**2. Architectural separation — анти-D.**

- `packages/dna-analysis/` — **pure functions only.** Парсеры (`parse_*`),
  validators, analysis-алгоритмы (Phase 6.1+: shared cM, AutoCluster).
  Не знает про БД, не знает про сеть, не пишет логи с raw values.
- `services/dna-service/` (Phase 6.1) — HTTP API + persistence, encryption,
  consent management, retention policies. Зависит от `dna-analysis`,
  но не наоборот.
- Барьер заметен через `pyproject.toml`: `dna-analysis` в dependencies
  имеет только `pydantic` (+ `cryptography` для будущих helpers Phase 6.1).
  Никакого `sqlalchemy`, `httpx`, `fastapi`.

**3. Consent model.**

- Explicit opt-in на DNA upload — отдельный checkbox/flow от общего
  AutoTreeGen consent. UI и flow — Phase 6.1 (`apps/web/`).
- **Default retention: 365 дней**, пользователь может изменить в любую сторону
  (минимум 30 дней — нужно для async-обработки upload, максимум —
  бессрочно с явным подтверждением раз в год).
- **Right to deletion: hard delete** для DNA. В отличие от генеалогических
  записей (ADR-0003 — soft-delete), для DNA выполняется immediate hard delete
  ciphertext + ключевого материала + audit-логов с метаданными upload.
- Audit trail сохраняется как **event-only** (`dna_kit_uploaded_at`,
  `dna_kit_deleted_at`, `consent_signed_at`) — без raw данных, без хеша
  файла, чтобы deletion действительно стирала associativity.

**4. Поддерживаемые форматы Phase 6.0 (только parsers).**

- 23andMe v5 raw (TSV, GRCh37) — Phase 6.0 Task 3.
- AncestryDNA v2 raw (TSV, GRCh37) — Phase 6.0 Task 4.
- MyHeritage raw (CSV) — Phase 6.0 stub, реализация Phase 6.1.
- FamilyTreeDNA Family Finder (CSV) — Phase 6.0 stub, реализация Phase 6.1.
- Living DNA / другие форматы — Phase 6.x, по запросу.

**5. Privacy guards в коде (Phase 6.0).**

- **Logging convention:** парсеры логируют только metadata —
  `logger.debug("parsed %d SNPs from file [%s]", n, file_hash[:8])`.
  Никогда не логируется `rsid`, `position`, `genotype`, `chromosome` для
  конкретных SNP. Тесты парсеров включают assertion на `caplog` —
  проверка отсутствия raw rsids в логах.
- **Test fixtures:** только синтетические, генерируются в `tests/conftest.py`
  с `random.seed(42)` для детерминированности. Никаких реальных rsids
  (используется паттерн `rs1`, `rs2`, …, `rsN`).
- **`.gitignore`:** real DNA-файлы добавлены wildcard'ами в корневом
  `.gitignore` (`**/dna-data/`, `*.dna.csv`, `*.dna.zip`, `*_dna_*.csv`,
  `*.dna`) и дополняется `packages/dna-analysis/test_data/real/`.
- **Ошибки:** `DnaParseError` сообщения не содержат raw value SNP.
  Указывается line number и тип ошибки, но не содержимое ячейки.

## Последствия

**Положительные:**

- Compromise сервера AutoTreeGen в Phase 6.1+ не раскрывает DNA пользователей.
- `packages/dna-analysis/` остаётся легковесным (pydantic only) → быстрые
  CI-прогоны, простая reuse-семантика.
- Privacy guards формализованы в коде (тесты + linter), не только в
  политике — устойчиво к AI-агентскому режиму разработки.
- Hard-delete политика для DNA снимает class GDPR-рисков, которые
  soft-delete не закрывает.

**Отрицательные / стоимость:**

- UX-стоимость passphrase: пользователь обязан помнить или хранить
  recovery-code. Mitigation — UX-flow при upload явно объясняет trade-off,
  показывает recovery-code, требует подтверждения «я сохранил».
- Background-jobs усложняются: shared cM matching (Phase 6.2) требует
  активной user session или короткоживущего session-token-derived ключа.
  Дизайн — Phase 6.2.
- Двухпакетная архитектура (`packages/dna-analysis` + `services/dna-service`)
  требует чёткого contract → больше boilerplate чем монолитный модуль.

**Что нужно сделать в коде (Phase 6.0):**

1. `packages/dna-analysis/src/dna_analysis/` — структура с `models.py`,
   `parsers/` (base + 4 provider files), `analysis/` (stub), `errors.py`,
   `py.typed`.
2. `tests/conftest.py` — синтетические fixture-генераторы для каждого
   формата, `random.seed(42)`.
3. `tests/fixtures/` — pre-generated `synthetic_*.txt` (committed, чтобы
   не зависеть от random run-to-run).
4. Реализация 23andMe v5 parser (Phase 6.0 Task 3) и AncestryDNA v2
   parser (Phase 6.0 Task 4) с тестами на синтетических fixtures.
5. README с privacy notice, ссылающимся на этот ADR.
6. `.gitignore` дополнить package-level правилами (`test_data/real/`).

**Что отложено (явно out of scope для Phase 6.0):**

- Encryption implementation — Phase 6.1, отдельный ADR.
- `services/dna-service/` (HTTP API, persistence) — Phase 6.1.
- Matching между двумя людьми — Phase 6.2.
- AutoCluster, triangulation, endogamy detection — Phase 6.2+.
- GEDmatch / Genetic Affairs интеграции — Phase 6.3 (см. ADR-0009).
- MyHeritage / FTDNA полные парсеры — Phase 6.1 (Phase 6.0 — только stub).

**Риски:**

- **Passphrase loss = data loss.** Пользователь теряет recovery-code →
  данные безвозвратно потеряны. *Mitigation:* explicit UX warning,
  optional split-key (часть у нас, часть у пользователя) — рассмотреть
  в Phase 6.1 ADR.
- **Background jobs vs zero-knowledge.** Если matching работает только
  при активной сессии — UX страдает. *Mitigation:* short-TTL
  session-derived key, явный re-auth для long-running jobs.
- **AI-агент случайно логирует raw value.** *Mitigation:* unit-test на
  `caplog` с assertion-блоком (no rsids in logs); pre-commit grep на
  паттерн `rs\d+` в новом Python-коде вне tests/fixtures.
- **Synthetic fixtures «утекают» в реальный анализ.** Если кто-то
  feed-нёт `synthetic_*.txt` в matching — получит мусорные результаты,
  не privacy-инцидент. Низкий риск.

## Когда пересмотреть

- **Phase 6.1 запускается** → отдельный ADR с key-management deep-dive
  (KDF parameters, session TTL, key rotation, recovery flow).
- **Argon2id ослабевает** (квантовые атаки, GPU-прорыв) → миграция на
  следующий KDF, key rotation для всех существующих kits.
- **Регулирование меняется** (например, EU AI Act расширяет требования
  к genetic data processing, или US HIPAA-style federal rule) → правовой
  ревью, возможное ужесточение retention.
- **Browser-side WebCrypto зрелость** позволяет полностью client-side
  encrypt без passphrase recovery sub-flow → пересмотр UX trade-off.
- **GEDmatch / Genetic Affairs интеграции** в Phase 6.3 → отдельный ADR
  про cross-platform DNA flow и consent extension.
- **Появление key-management как managed-service** в GCP (KMS HSM с
  client-bound keys) → пересмотреть, можно ли упростить self-managed
  key derivation.

## Ссылки

- Связанные ADR:
  - ADR-0003 (versioning strategy) — определяет soft-delete для
    генеалогии; этот ADR явно отступает к hard-delete для DNA.
  - ADR-0009 (genealogy integration strategy) — фиксирует DNA gap и
    GEDmatch как community workaround в Phase 6.3.
  - Будущий ADR-0013 (Phase 6.1) — key management deep-dive.
- CLAUDE.md §3.5 («Privacy by design»), §5 (запреты — DNA в репо),
  §11 (работа с GEDCOM, аналогичный privacy подход для генеалогии).
- ROADMAP §10 (Phase 6 — DNA Analysis Service), §17 (Phase 13 —
  безопасность и деплой), §20 (юридические аспекты).
- Внешние:
  - [GDPR Art. 9 — special categories of personal data](https://gdpr-info.eu/art-9-gdpr/)
  - [GDPR Art. 17 — right to erasure](https://gdpr-info.eu/art-17-gdpr/)
  - [Argon2 RFC 9106](https://datatracker.ietf.org/doc/html/rfc9106)
  - [NIST SP 800-132 — recommendation for password-based key derivation](https://csrc.nist.gov/pubs/sp/800/132/final)
