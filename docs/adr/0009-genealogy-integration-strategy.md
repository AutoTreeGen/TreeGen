# ADR-0009: Genealogy platform integration strategy (Phase 5+)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `integration`, `dna`, `gedcom`, `phase-5`

## Контекст

Phase 5 — это слой интеграций с внешними генеалогическими платформами.
AutoTreeGen нужны четыре класса данных:

1. **Tree records** — persons, families, relationships от других платформ.
2. **Историческое evidence** — censuses, vital records, archival hints.
3. **DNA matches** — основа hypothesis-engine cousin matching.
4. **Региональное покрытие** — Восточная Европа и еврейская генеалогия
   (стратегическая ниша проекта).

Полный landscape доступа — `docs/research/genealogy-apis.md` (snapshot
April 2026). Ключевые findings оттуда:

- **Платформ с настоящим public API мало.** FamilySearch (free, OAuth,
  GEDCOM-X, non-profit), Geni (OAuth, sandbox), MyHeritage Family Graph
  (OAuth, freemium), WikiTree (REST, free), Findmypast (UK-only Hints API).
- **DNA match data — за стенами почти везде.**
  - Ancestry — public API нет вообще, internal API не публикуется.
  - 23andMe — third-party API закрыт в августе 2018.
  - MyHeritage — DNA не expose-ится через Family Graph (web-only + 2FA).
  - FamilyTreeDNA — нет public API.
  - **GEDmatch** — единственная open-access платформа для cross-platform
    DNA matching (~1.5M public profiles), пользователи владеют своими
    kit'ами; community-maintained Python wrapper `nh13/gedmatch-tools`,
    официального SLA нет.
- **JewishGen** — критический источник для еврейской/восточно-европейской
  генеалогии (gazetteer 3M+ мест, JOWBR, JGFF), но **public API
  отсутствует**. Только web search ($100/год full access).
- **Ancestry — заблокирован.** Internal API существует, но не публикуется;
  скрейпинг прямо запрещён ToS платформы и нашим CLAUDE.md §5.

Силы давления на решение:

1. **Запрет на скрейпинг** (CLAUDE.md §5, ROADMAP §13) закрывает
   несколько крупных источников целиком — это не tradeoff, а constraint.
2. **Phase 5 timeline** — нужно поставлять value быстро, не ждать
   юридических переговоров на 6–12 месяцев.
3. **DNA = core differentiation.** AutoTreeGen без cousin matching
   по сути ещё один GEDCOM-редактор. Hypothesis engine (см. ROADMAP
   Phase 6+) опирается на DNA evidence как first-class signal.
4. **Eastern European / Jewish coverage** — самый ценный data set
   (JewishGen) технически недоступен через автоматизацию.

## Рассмотренные варианты

### Вариант A — Только официальные публичные API

Phase 5 строит интеграции исключительно с FamilySearch, Geni, MyHeritage,
WikiTree. Без DNA. Без community-maintained wrapper'ов.

- ✅ Юридически чисто. Стандартный OAuth 2.0 flow.
- ✅ Нет договорной нагрузки, нет NDA, можно работать в open.
- ✅ Покрывает основной use case: tree records + GEDCOM + historical hints.
- ✅ FamilySearch GEDCOM-X — production-grade round-trip.
- ❌ **DNA-функциональность отложена в неопределённость.** Это закрывает
  hypothesis-engine cousin matching до того момента, как кто-то из tier-1
  откроет API (по research'у — никто не сигналит).
- ❌ Eastern European покрытие — частичное. FamilySearch + MyHeritage
  сильны, но JewishGen-уровня детализации нет.

### Вариант B — Гибрид: официальные API + GEDmatch для DNA

Всё из A, плюс GEDmatch wrapper за feature flag для cross-platform DNA
matching. Пользователь one-time загружает свой kit (от Ancestry / 23andMe /
MyHeritage / FTDNA) в GEDmatch, AutoTreeGen читает matches через
`nh13/gedmatch-tools`.

- ✅ Закрывает DNA gap **немедленно**, без ожидания партнёрств.
- ✅ Пользователь сохраняет ownership DNA-данных (GEDmatch ToS — users
  own data).
- ✅ Работает с пользователями любой DNA-платформы — kit upload в GEDmatch
  делает их matches видимыми единым каналом.
- ❌ **GEDmatch wrapper community-maintained**, без официального SLA.
  Если апстрим сломается или GEDmatch поменяет аутентификацию — наша
  интеграция тоже сломается.
- ❌ Юридический серый: ToS GEDmatch не запрещает автоматизацию, но
  и не описывает её. Прецедент: после случая Golden State Killer (2018) и
  закона North Carolina (2019) платформа уже ужесточала правила
  в одностороннем порядке.
- ❌ DNA = special category (GDPR Art. 9, CLAUDE.md §3.5). Даже при
  ownership пользователя, наша обработка требует явный consent flow
  и application-level encryption.

### Вариант C — Партнёрские договоры (Ancestry, 23andMe, JewishGen)

Заходим в переговоры с tier-1 платформами, ждём legal review,
подписываем data partnership agreements.

- ✅ Доступ к самым полным data sets: AncestryDNA, JewishGen historical
  records, 23andMe research-grade data.
- ✅ Юридически наиболее устойчиво.
- ❌ **Timeline 6–12+ месяцев на каждую платформу** — и это при условии,
  что переговоры в принципе возможны. Ancestry и 23andMe исторически
  не предоставляют доступ малым проектам без revenue track record.
- ❌ Partnership = NDA = closed development. Наш open evidence-graph plugin
  model плохо совместим с NDA-обвязкой.
- ❌ Phase 5 deliverable откладывается на год+. Не приемлемо
  для product-roadmap.

### Вариант D — Только пользовательский экспорт-импорт (GEDCOM upload)

Никаких API-интеграций. Пользователь экспортирует GEDCOM из любой
платформы (Ancestry, MyHeritage, …), загружает в AutoTreeGen вручную.

- ✅ Минимум технического долга. ADR-0007 (GEDCOM 5.5.5 как canonical)
  уже решает import/export.
- ✅ Юридически безупречно — пользователь сам владелец данных.
- ✅ Работает со всеми платформами одинаково, включая Ancestry/23andMe.
- ❌ **DNA не экспортируется в GEDCOM** — теряется core feature целиком.
- ❌ Нет инкрементальных синков. Каждое изменение в исходной платформе =
  ручной re-export → re-import. Этот UX отталкивает active users.
- ❌ Нет hint API (FamilySearch matches, Findmypast Hints). Теряется
  value prop "AutoTreeGen находит ваши записи и подсказывает гипотезы".

## Решение

Выбран **Вариант B** — гибрид официальных API + GEDmatch для DNA
за feature flag.

Обоснование (4 предложения, как требует шаблон):

1. **A слишком ограничивающий.** DNA — core feature; отказ от неё
   в Phase 5 = добровольно отдать рынок Ancestry/MyHeritage по ключевой
   дифференциации.
2. **C нереалистичен по срокам.** Partnership-track ведём параллельно
   как отдельную инициативу (см. "Когда пересмотреть"), но Phase 5
   на нём не блокируем.
3. **D отбрасывает hint API и инкрементальные синки.** GEDCOM upload
   остаётся как fallback для Ancestry / 23andMe / FTDNA, но не как
   единственный канал.
4. **B — pragmatic compromise.** Официальные API закрывают tree records
   и historical evidence (FamilySearch и Geni — highest priority по
   research'у), GEDmatch — единственный реалистичный путь к DNA в 2026.

Оговорки про GEDmatch (риск явный, его не игнорируем):

- Wrapper изолируется в отдельный модуль `packages/dna-analysis/integrations/gedmatch/`.
  Никакой код за пределами этого модуля не зависит от community API.
- Feature flag `dna_gedmatch_enabled` управляет включением. Default
  на старте Phase 5 — `off`, включаем после прохождения soak window
  на стабильности апстрима.
- При первом отказе GEDmatch wrapper — flag отключается, остальные
  интеграции продолжают работать. Никаких cascading failure.
- DNA-операции через GEDmatch требуют отдельный explicit consent UI
  с явным указанием, что данные идут через third-party platform
  с community-maintained library.

## Последствия

**Положительные:**

- Phase 5 поставляется в реалистичном timeline (3–6 месяцев),
  а не ждёт partnership-track.
- AutoTreeGen работает с пользователями любой DNA-платформы через
  GEDmatch upload bridge.
- FamilySearch GEDCOM-X становится internal canonical вторым после
  нашего GEDCOM 5.5.5 (см. ADR-0007 — round-trip конвертер обязателен).
- Open evidence-graph остаётся open, без NDA-обвязки.

**Отрицательные / стоимость:**

Что нужно построить в Phase 5 (примерный task list, не финальный):

1. **OAuth 2.0 client framework** — `packages/integrations-oauth/`,
   общий для FamilySearch, Geni, MyHeritage, WikiTree. Token refresh,
   scope management, encrypted at-rest storage в GCP Secret Manager
   (прод) и шифрованной локалке (dev).
2. **Per-platform adapters** — `packages/integrations/{familysearch,
   geni, myheritage, wikitree}/`. Маппинг платформенных моделей
   к нашему canonical в `shared-models`.
3. **GEDCOM-X ↔ GEDCOM 5.5.5 конвертер** — расширение
   `packages/gedcom-parser/`. FamilySearch native — GEDCOM-X JSON;
   наш canonical — 5.5.5 (ADR-0007). Round-trip обязателен.
4. **Rate limiter middleware** — per-platform, configurable, token
   bucket в Redis. Без агрессивного backoff (чтобы не ловить
   platform-side bans). Conservative defaults пока rate limits не
   опубликованы (Geni, MyHeritage).
5. **GEDmatch wrapper** — `packages/dna-analysis/integrations/gedmatch/`,
   изолированный, под feature flag.
6. **Sync orchestration** через `arq` (Redis queue), idempotent jobs,
   retry-with-backoff, dead-letter queue.
7. **Provenance tagging** — каждая запись из integration получает
   `provenance.source_files` с platform identifier + timestamp + API
   endpoint (CLAUDE.md §3.3, "provenance everywhere").
8. **Consent UI flow** — `apps/web/`, отдельный flow для DNA-scope,
   GDPR-compliant verbiage, audit log согласий.
9. **Миграции БД** — таблицы `integration_account`, `sync_job`,
   `external_id_mapping`. Versioning per ADR-0003.

Что **отложить** (явно out of scope для Phase 5 v1):

- Ancestry / 23andMe / FamilyTreeDNA — никаких попыток интеграции.
  Партнёрский трек — параллельная инициатива в Phase 7+, не блокирует.
- JewishGen — manual GEDCOM import от пользователей; partnership
  conversation ведём параллельно, не блокирует Phase 5.
- Findmypast — низкий приоритет (UK-фокус, минимальная ценность для
  Восточной Европы / еврейской диаспоры).

**Риски:**

- **GEDmatch SLA.** Community wrapper, без гарантий стабильности.
  *Mitigation:* изоляция в отдельный пакет + feature flag + явное
  consent + monitoring апстрима `nh13/gedmatch-tools` (подписка
  на releases / issues).
- **Rate limits не опубликованы** (Geni, MyHeritage). *Mitigation:*
  conservative client-side throttling до контакта с devsupport;
  до production rollout — явное подтверждение лимитов.
- **DNA как special category** (GDPR Art. 9). *Mitigation:* explicit
  consent UI, application-level encryption at-rest (CLAUDE.md §3.5),
  юридический ревью до prod rollout, политика удаления.
- **OAuth refresh token leakage.** *Mitigation:* GCP Secret Manager
  в проде, encrypted storage в dev, token rotation, scoped tokens
  (минимум привилегий).
- **Платформа меняет ToS / закрывает API.** *Mitigation:* мониторинг
  ToS / changelog'ов раз в квартал; abstraction layer на уровне
  adapter, чтобы заменить или отключить платформу без переписывания
  callers.

## Когда пересмотреть

- **DNA API появляется у любой большой платформы** (Ancestry открывает,
  23andMe возвращает API, FamilySearch открывает Match API всем
  developer-program members, MyHeritage добавляет DNA scope в Family
  Graph). → Переключаемся с GEDmatch на native API там, где это даёт
  лучший SLA или покрытие.
- **GEDmatch меняет ToS** (запрещает автоматизацию, требует API key
  с rate limits, которые нам не предоставляются, или меняет аутентификацию
  под закрытую партнёрскую программу). → Отключаем feature flag,
  деградируем до Варианта A до появления альтернативы.
- **Партнёрский договор подписан** (Ancestry / 23andMe / JewishGen).
  → Расширяем интеграционный набор, GEDmatch остаётся как cross-platform
  fallback для пользователей других провайдеров.
- **Rate limits Geni или MyHeritage** оказываются too restrictive
  (например, < 100 req/hour/user — недостаточно для bulk sync).
  → Переоцениваем целесообразность, возможно — больше rely на GEDCOM
  upload как primary канал (частичный fallback к Варианту D).
- **GDPR / законодательные изменения** по DNA processing (например,
  запрет cross-border DNA data transfer). → Юридический ревью + возможный
  отказ от GEDmatch до партнёрства с локальной инфраструктурой.

Кадровый ритм пересмотра: раз в квартал + при срабатывании любого
из триггеров выше.

## Ссылки

- Связанные ADR:
  - ADR-0007 (GEDCOM 5.5.5 как canonical) — определяет наш internal
    формат, к которому конвертируется GEDCOM-X из FamilySearch.
  - ADR-0003 (versioning strategy) — для `external_id_mapping`,
    provenance versioning, soft-delete политики.
  - ADR-0001 (tech stack) — OAuth client будет на Python 3.12 +
    FastAPI + httpx, очереди через `arq` на Redis.
- Research: `docs/research/genealogy-apis.md` (April 2026 landscape,
  full platform-by-platform analysis с источниками).
- Архитектурные принципы: CLAUDE.md §3 (Evidence-first, Provenance
  everywhere, Privacy by design, Domain-aware).
- Запрет неофициальной автоматизации: CLAUDE.md §5 (Запреты —
  скрейпинг без публичного API), `ROADMAP.md` §13.
- ROADMAP Phase 5 (Integration Layer) — детальный task list реализации.
- External:
  - [FamilySearch Developers](https://developers.familysearch.org/)
  - [Geni Developer Platform](https://www.geni.com/platform/developer/help)
  - [MyHeritage Family Graph API](https://www.familygraph.com/)
  - [WikiTree API Documentation](https://www.wikitree.com/wiki/Help:API_Documentation)
  - [GEDmatch Tools (community Python wrapper)](https://github.com/nh13/gedmatch-tools)
  - [GEDCOM-X Specification](https://developers.familysearch.org/main/docs/gedcom-x)
