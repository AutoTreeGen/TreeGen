# DNA data handling — runbook

> Phase 6.0+ runbook для пользователей и разработчиков. Контекст —
> ADR-0012 (privacy & architecture). Этот документ — операционный,
> а не дизайнерский: ADR описывает **почему**, runbook — **как**.

---

## Для пользователя AutoTreeGen

### Безопасная загрузка DNA

DNA — это special category personal data по GDPR Art. 9. AutoTreeGen
обрабатывает её под более жёстким режимом, чем генеалогические записи:
явный consent на каждый kit, hard delete при запросе на удаление,
default retention 365 дней.

**Шаги загрузки (Phase 6.1+, UI ещё не финализирован):**

1. **Экспортировать raw файл из источника:**
   - 23andMe: Account → Browse Raw Data → Download → выбрать v5,
     получить `.zip` с одним `.txt` внутри.
   - AncestryDNA: Settings → Privacy → Download Raw DNA Data → подтвердить
     по email. ZIP с одним `.txt` внутри.
   - MyHeritage: ⏳ Phase 6.1 (стаб-парсер пока возвращает
     `UnsupportedFormatError`).
   - FamilyTreeDNA: ⏳ Phase 6.1.
   - Living DNA: ⏳ Phase 6.x (запрос → roadmap).

2. **Проверить, что файл не модифицирован.** Не редактируйте файл вручную
   и не открывайте в Excel — последний может незаметно изменить
   разделители (TSV → CSV) или исказить большие числа в позициях.

3. **Загрузить через `/dna/upload` (Phase 6.1+).** Перед загрузкой UI
   потребует:
   - Явный opt-in checkbox: «Я даю явное согласие на обработку моих
     генетических данных в соответствии с GDPR Art. 9 и
     ADR-0012».
   - Установить retention period (default 365 дней, минимум 30, максимум
     — бессрочно с подтверждением раз в год).
   - Создать passphrase для шифрования (Phase 6.1 ADR-0013): сильная,
     отдельная от пароля логина AutoTreeGen.

4. **Сохранить recovery code.** Phase 6.1+ покажет одноразовый
   recovery-код в момент upload. Сохраните его офлайн (бумага,
   менеджер паролей). Без него восстановление зашифрованных данных
   невозможно.

### Где хранится

Phase 6.0 (текущая фаза): **нигде на сервере.** Парсеры — pure functions
без persistence. Если вы вызвали парсер локально, raw данные живут
только в памяти процесса до завершения.

Phase 6.1+: ciphertext в Postgres + GCS (прод) / MinIO (dev).
Encryption — application-level AES-256-GCM, ключ derive-ится из
пользовательского passphrase (Argon2id), **не хранится** server-side.
Server владеет только ciphertext.

### Право на удаление (GDPR Art. 17)

В отличие от генеалогических записей (которые soft-deleted —
см. ADR-0003), DNA удаляется **hard delete:**

- ciphertext стирается из Postgres + object storage;
- ключевой материал затирается (overwrite + delete);
- audit log сохраняет только factum события
  (`dna_kit_deleted_at`, `consent_revoked_at`) — БЕЗ хеша файла, БЕЗ
  identifier'а kit'а, чтобы deletion действительно стирала associativity;
- из всех аналитических индексов (Phase 6.2+ — matching, AutoCluster) —
  удаляется немедленно.

**Как запросить удаление (Phase 6.1+):**

1. UI: Account → Privacy → DNA → «Delete kit» (per kit) или «Delete all
   DNA» (одним действием).
2. Email: <privacy@autotreegen.example> (TBD, Phase 6.1).
3. Подтверждение (для безопасности — email confirm + повторный
   passphrase). Удаление выполняется в течение 24 часов согласно SLA.

### Что AutoTreeGen НЕ делает

- НЕ передаёт raw DNA третьим сторонам.
- НЕ продаёт агрегаты, не использует для рекламы.
- НЕ имеет доступа к raw данным без вашей активной passphrase-сессии
  (zero-knowledge — см. ADR-0012).
- НЕ копирует данные в другие регионы без явного согласия.

GEDmatch (Phase 6.3, см. ADR-0009) — отдельный flow с дополнительным
explicit consent: данные явно покидают AutoTreeGen и идут в third-party
платформу с community-maintained API. Default — off.

---

## Для разработчиков

### Что МОЖНО

- Использовать **синтетические fixture-файлы** (`tests/fixtures/synthetic_*.txt`,
  generators в `tests/_generators.py`) — они генерируются с
  `random.seed=42`, никаких реальных rsids.
- Загружать **анонимизированный** мини-corpus в `packages/dna-analysis/test_data/`
  для отладки — но **только** если каждый rsid и position преобразованы
  (например, заменены на `rs1..rsN` и random positions). Каталог
  `test_data/real/` исключён из git.
- Логировать **агрегаты:** количество SNP, sha256-prefix файла,
  parsing duration. См. примеры в `parsers/twentythreeand_me.py:118`
  и `parsers/ancestry.py:90`.
- Поднимать `DnaParseError` с line number и **типом** ошибки
  («invalid chromosome», «unknown allele»), без отвергнутого raw value.
- Импортировать `dna_analysis.parsers` — это pure-functions package,
  не имеет storage-зависимостей.

### Что НЕЛЬЗЯ

- **❌ Коммитить реальные DNA-файлы.** Даже свои, даже в тестовый
  каталог, даже под `.gitignore`-патрулём — раз в неделю кто-нибудь
  забудет про `git add -A`. `.gitignore` блокирует `*.dna`,
  `*.dna.csv`, `*.dna.zip`, `*_dna_*.csv`, `**/dna-data/`,
  `**/dna_kits/`, `packages/dna-analysis/test_data/real/` —
  не пытайтесь обойти.
- **❌ Логировать raw rsids / positions / genotypes** в любом виде:
  ни через `logger.info(snp)`, ни через f-string в сообщении
  exception. Тесты `test_parser_does_not_log_raw_values` явно проверяют
  caplog — если добавляете новый лог в парсер, прогоните этот тест.
- **❌ Echoing raw value в exception message:**

  ```python
  # BAD — утекает в Sentry, в HTTP error response, в логи.
  raise DnaParseError(f"invalid chromosome '{chrom_token}'")

  # GOOD — называет тип проблемы, line number из контекста.
  raise DnaParseError("invalid chromosome", line_number=line_idx)
  ```

- **❌ Добавлять heavy dependencies в `packages/dna-analysis/pyproject.toml`.**
  Сейчас зависимости — `pydantic` + `cryptography`. Никаких
  `sqlalchemy`, `httpx`, `fastapi` — это анти-паттерн D из ADR-0012:
  такие зависимости делают `from dna_analysis.parsers import ...`
  тяжёлым импортом, и любой случайный `logger.info(arg)` в storage-слое
  ломает privacy-инвариант.
- **❌ Persistence в `packages/dna-analysis/`.** Storage + HTTP уйдут
  в `services/dna-service/` (Phase 6.1). dna-analysis — pure functions.
- **❌ Реальные тесты в CI.** Если нужен larger corpus для perf-тестов
  — генерируйте синтетический в conftest, не подгружайте файл с диска
  пользователя.

### При обнаружении утечки

Если в коде / логах / exception обнаружено raw DNA value:

1. Немедленно ротировать любые секреты, которые могли попасть в тот же
   log/Sentry stream (если применимо).
2. Удалить заражённые log-файлы и Sentry events (через Sentry UI —
   bulk delete).
3. Открыть incident-issue с тегом `security` + `dna-leak`.
4. Если утечка попала в git history — **немедленно** force-push с
   `git filter-repo` (требует координации владельца репо и всех
   коллабораторов) + ротация любых compromised credentials. Никогда
   не полагайтесь на «GitHub force-push скроет историю» — данные
   уже могли быть scraped.
5. Если утечка попала к пользователю (DNA одного оказалась в логе,
   видном другому) — это GDPR breach, обязательное уведомление в
   течение 72 часов (Art. 33). Эскалировать к owner проекта.

### Локальная разработка

```bash
# Запустить только dna-analysis тесты (быстро):
uv run pytest packages/dna-analysis/ --no-cov

# Сгенерировать синтетический fixture для дебага (если правили генератор):
uv run python -c "
from packages.dna_analysis.tests._generators import generate_synthetic_23andme
print(generate_synthetic_23andme(num_snps=10))
"

# Парсер на реальном файле (НЕ коммитить файл!):
uv run python -c "
from pathlib import Path
from dna_analysis.parsers import TwentyThreeAndMeParser
content = Path('~/Downloads/genome_v5.txt').expanduser().read_text()
test = TwentyThreeAndMeParser().parse(content)
print(f'{test.provider} {test.version}: {len(test.snps)} SNPs')
"
```

---

## Ссылки

- ADR-0012 — DNA processing privacy & architecture (`docs/adr/0012-dna-privacy-architecture.md`).
- ADR-0009 — Genealogy integration strategy (DNA gap, GEDmatch).
- ADR-0003 — Versioning strategy (DNA opts out of soft-delete).
- CLAUDE.md §3.5 — Privacy by design.
- CLAUDE.md §5 — Запреты (DNA в репо).
- ROADMAP §10 — Phase 6 DNA Analysis Service.
- [GDPR Art. 9](https://gdpr-info.eu/art-9-gdpr/) — special categories.
- [GDPR Art. 17](https://gdpr-info.eu/art-17-gdpr/) — right to erasure.
- [GDPR Art. 33](https://gdpr-info.eu/art-33-gdpr/) — breach notification.
