# ADR-0058: JewishGen deep-link helper (Phase 9.2)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `archive`, `jewishgen`, `phase-9`, `compliance`, `deep-link`,
  `no-scraping`, `frontend-only`

## Контекст

Phase 9 (ROADMAP §13) — интеграция с внешними генеалогическими
архивами. Phase 9.0 (ADR-0055) запустил `services/archive-service` и
первый адаптер для FamilySearch — у того есть public OAuth API и
формальная partner-программа. Phase 9.2 — следующий по приоритету
источник для нашей доменной ниши (восточноевропейская и еврейская
генеалогия, XIX–XX вв.) — **JewishGen** (включая его агрегацию JOWBR,
Holocaust Database, Country/Topical databases).

Фундаментальное отличие JewishGen от FamilySearch:

> **JewishGen не имеет публичного API.** UI-only поиск, требует
> бесплатной регистрации, программный доступ не предусмотрен.

Источники этого утверждения, верифицированные на 2026-04-30:

- Project-internal research note `docs/research/archive-integrations-2026.md`
  §«JewishGen (incl. JRI-Poland)» (line 198–242, 508–516, 561–564,
  shipped в main как часть Phase 9.0-pre):
  *"API URL: None published. Auth: N/A. … No engineering possible
  without a data partnership. Realistic Phase 9 outcome is either
  data-partnership outreach (8–16+ weeks) or deep-link smart-search
  helper (no scraping, no caching of results)."*
- Категоризация в той же note: JewishGen — Tier C (partnership-only),
  Phase 9.6, **не** Phase 9.x-tier-A.
- `https://www.jewishgen.org/robots.txt` (HTTP 200, 2026-04-30):
  `Content-Signal: search=yes, ai-train=no` с явной reservations of
  rights под EU DSM Article 4. Указанный пользователем path
  `/About/Terms.html` отдаёт **404** на тот же запрос; canonical
  Terms живут в другом месте сайта (которое нам сейчас не нужно
  верифицировать, поскольку мы не делаем requests к JG —
  см. «Решение»).

И регулирующие проектные нормы:

- **CLAUDE.md §5** — «Скрейпинг платформ без публичного API» — hard
  ban. Любой HTTP-клиент, который от имени TreeGen-сервера читает
  страницы JG-search'а или парсит их HTML, **нарушает это правило**.

Внутренний бриф изначально предлагал scaffolding для
`services/archive-service/adapters/jewishgen.py` с retry, rate-limit
1 req/sec и importer'ом, генерирующим hypotheses из JG-результатов.
Этот вариант мы рассмотрели и отклонили.

## Рассмотренные варианты

### Вариант A — HTTP-клиент + 1 req/sec + importer (исходный бриф)

- ✅ Один paste-ready brief, минимум координации.
- ✅ Дал бы автоматическую генерацию hypotheses без ручного клика.
- ❌ **Прямое нарушение CLAUDE.md §5.** «Polite scraping» с
  User-Agent + delays — всё равно scraping без public API.
- ❌ Прямо противоречит уже опубликованной в `main` research note
  (см. контекст), которая категоризирует JG как Tier C
  (partnership-only).
- ❌ Сами scraping endpoints (форма /databases/all/) построены так,
  что результаты возвращаются как HTML с серверной side-state'ой —
  парсинг fragile, изменится без warning'а.
- ❌ JG может изменить ToS / явно запретить programmatic access /
  забанить нас по IP — single-point-of-failure для нашей фичи.
- ❌ ToS у JG найти не удалось по brief-указанному URL (404);
  проводить первое engineering-затраты до понимания ToS — anti-pattern.

### Вариант B — Wait-for-partnership (Tier C, ~8–16 недель outreach)

- ✅ Соответствует research note и CLAUDE.md §5.
- ✅ В долгосроке открыл бы качественный data-feed (CSV/SQL dump
  скорее, чем live API) — оптимум для evidence-based hypotheses.
- ❌ Phase 9.2 в ROADMAP'е плановая на ближайший спринт. Ждать
  3+ месяца — блокирует UX-параллель с FamilySearch.
- ❌ Outreach — асинхронный процесс owner'а, не engineering-задача.
  Делается параллельно в любом варианте.

### Вариант C — Deep-link helper (research-note Phase 9.6a)

- ✅ Соответствует research note (явно прописанный fallback) и
  CLAUDE.md §5 (нет HTTP-клиента → нет scraping'а).
- ✅ Уважает ToS любых будущих изменений: мы не делаем requests'ов
  и не кэшируем результаты, поэтому что бы JG ни запретили в ToS,
  мы продолжаем не нарушать.
- ✅ Низкая стоимость: 300–500 LOC на frontend-only, без миграций,
  без backend'а, без записи в archive_search_log (нечего логировать
  — мы не делаем requests).
- ✅ UX-выигрыш — пользователь одной кнопкой переходит к
  предзаполненному поиску в JG'шном UI, где видит результаты в
  родном их формате (включая платный gating, который JG имеет на
  некоторых коллекциях, и мы всё равно не смогли бы агрегировать).
- ❌ Нет автоматических hypotheses — пользователь сам читает JG-сторону
  и вручную вносит факты в TreeGen. Acceptance: для high-trust
  доменных сущностей (JG-volunteer-indexed records) ручной review
  всё равно желателен (ROADMAP §3.3 evidence-first, ADR-0007).
- ❌ Нет структуры для будущей бесшовной миграции на data-partnership
  результат: когда (если) Phase 9.6 ляжет, фича deep-link'а
  сосуществует с importer'ом, не заменяется им.

## Решение

Выбран **Вариант C** — deep-link helper, frontend-only.

**Что делаем:**

- `apps/web/src/lib/jewishgen.ts` — pure URL builder
  `buildJewishGenSearchUrl({surname, givenName?, town?})` →
  возвращает URL на `https://www.jewishgen.org/databases/all/` с
  параметрами `srch1v/srch1t/srch1` series'а (формат JG'шного
  unified-search'а; параметры verified live one-shot HEAD'ом
  на 2026-04-30: pattern `srchNv` = data type {S=Surname,
  G=GivenName, T=Town}, `srchNt` = match type {Q=Phonetically Like
  — default}, AND boolean между линиями).
- `apps/web/src/components/jewishgen-search-button.tsx` —
  React-компонент: рендерит `<a target="_blank"
  rel="noopener noreferrer">` со styled-кнопкой и disclaimer'ом,
  что TreeGen ничего не передаёт. `noopener noreferrer` — стандартная
  защита от tabnabbing'а и Referer-leak'а на untrusted outbound.
- Интеграция в `apps/web/src/app/persons/[id]/page.tsx` — новая
  секция «External searches» под Sources. Кнопка скрывается, если у
  персоны нет ни surname'а, ни given_name'а (URL builder возвращает
  `null`).
- i18n (en + ru) для всех визуальных строк, включая disclaimer.

**Чего НЕ делаем:**

- Нет HTTP-клиента. Нет requests'ов к jewishgen.org с TreeGen-стороны.
- Нет таблицы `archive_search_log` для JG (нечего логировать —
  событие «пользователь кликнул» наблюдается на JG-стороне; для нас
  оно не имеет полезной семантики до partnership'а).
- Нет alembic-миграции, нет shared-models изменений, нет нового
  python-сервиса, нет адаптера в archive-service.
- Нет importer'а / автоматической генерации hypotheses со
  `provenance.archive_source='jewishgen'`. До partnership'а fact'ы
  с JG'шной стороны импортируются вручную пользователем (тот же
  путь, что для любого внешнего источника без API: см. ADR-0007
  evidence-first model).
- Нет living-people gating'а на саму кнопку — кнопка не передаёт
  данные, рендерит URL в DOM'е owner'а тех данных. Если в будущем
  `PersonDetail` API начнёт отдавать `is_alive`, кнопку можно
  условно скрыть для ALIVE-персон, но это не блокирует Phase 9.2.

### Связь с research note и Phase 9.6

Research note описывает Phase 9.6 как «JewishGen + JRI-Poland data
partnership, 8–16+ недель outreach'а, потом ~2–3 недели engineering'а
per data feed». Этот ADR — реализация **9.6a** (helper), не отменяющая
9.6 (partnership). Когда / если 9.6 выходит — вместо deep-link'а
появляется importer + автоматические hypotheses; deep-link остаётся
как UX-fallback для коллекций, не покрытых партнёрским fee'ом, или
deprecated'ится отдельным ADR.

### Что выбрали в URL'е

- **Match type Q (Phonetically Like)** — JG'шный default по UI.
  Лучший вариант для транслитерированных еврейских имён, где
  «Goldberg» / «Голдберг» / «Goldberger» должны мэтчиться.
- **AND boolean** между линиями — передаём явно, чтобы поведение не
  менялось, если JG поменяет UI-default на OR.
- **Surname → GivenName → Town** — ordering лишь UI-косметический у
  JG, но фиксируем для тестируемости (URL детерминирован по входу).

## Последствия

- **Положительные:**
  - Фича шипится за день, без миграций, без backend'а — minimal blast
    radius.
  - Соответствие CLAUDE.md §5 и research note полное; PR не
    требует override'ов проектных правил.
  - Pattern переиспользуемый: следующие deep-link helper'ы
    (JRI-Poland, Szukaj w Archiwach, GenTeam — research note
    Tier C / партнёрские) копируют ту же структуру: pure URL
    builder + thin React component + i18n. Это закладывает базу
    для «External searches» panel'и (research note line 577) без
    дополнительного backend'а.
- **Отрицательные / стоимость:**
  - Нет автоматических hypotheses из JG до partnership'а. Пользователь
    делает дополнительный шаг — открывает вкладку, читает результаты,
    вручную вносит обратно в TreeGen. Это сознательная цена за
    §5-compliance.
  - JG может изменить URL-format `/databases/all/?srch1v=…`. Тест на
    URL builder зафиксирует expected output; если JG поменяет
    параметры, тест продолжит проходить (он проверяет наш URL, не
    JG-server-side behaviour), а сам URL начнёт open'аться в empty
    search'е на JG-side. Mitigation — single line in our codebase
    обновляется и шипится; не блокирующий риск.
  - Place отсутствует: `EventSummary.place_id` сейчас отдаёт UUID,
    не текст. Town-фильтр поэтому не задействован. **TODO** —
    expose `place_label` в API (отдельная фаза 4.x), потом передать
    в `JewishGenQuery.town`.
- **Риски:**
  - Если research note будет deprecated'ить deep-link path вообще
    («не поощряем sending users off-platform») — этот ADR станет
    Superseded. Триггер: явное решение про unified «External
    searches» UX guideline'а.
- **Что нужно сделать в коде** (этим PR):
  - `apps/web/src/lib/jewishgen.ts` — URL builder.
  - `apps/web/src/components/jewishgen-search-button.tsx` — компонент.
  - `apps/web/src/app/persons/[id]/page.tsx` — интеграция секции.
  - `apps/web/messages/{en,ru}.json` — i18n strings.
  - `apps/web/src/__tests__/jewishgen-url.test.ts` — unit-тесты на
    URL builder.
  - `apps/web/src/__tests__/jewishgen-search-button.test.tsx` —
    component-тесты (renders, target=_blank, rel=noopener noreferrer,
    handles empty query, dual-locale rendering).

## Когда пересмотреть

- Если JewishGen опубликует public API — заменяем deep-link на
  HTTP-адаптер в archive-service, помечаем этот ADR Superseded.
- Если JewishGen / JRI-Poland data partnership ляжет (Phase 9.6) —
  поверх deep-link'а появляется importer'ная фича; ADR либо
  remains active (helper остаётся), либо Superseded by Phase 9.6
  ADR в зависимости от того, покрывает ли partnership всю
  поверхность.
- Если ToS JewishGen явно запретит deep-linking из third-party UIs
  (нестандартное condition для генеалогического сайта, но возможное)
  — кнопка снимается, ADR Deprecated'ится.
- Если research note будет deprecated'ить deep-link approach в
  пользу другого паттерна (например, обязательный «JewishGen
  account-linking» через manual login на их side) — пересмотреть
  всю стратегию Phase 9.6a.

## Ссылки

- Связанные ADR: ADR-0007 (evidence-first), ADR-0011 (FS client
  pattern), ADR-0055 (archive-service scaffold + FamilySearch
  adapter — **первый** Tier-A адаптер; этот ADR — **первый**
  Tier-C deep-link helper).
- ROADMAP §13 — Phase 9 интеграции.
- `docs/research/archive-integrations-2026.md` — research note,
  §«JewishGen (incl. JRI-Poland)» (Tier C, Phase 9.6) и
  §«Weekend-hackable items» (deep-link smart-search helpers).
- CLAUDE.md §5 — «no scraping platforms without public API».
- JewishGen Unified Search: <https://www.jewishgen.org/databases/all/>
- JewishGen robots.txt: <https://www.jewishgen.org/robots.txt>
  (verified 2026-04-30: `Content-Signal: search=yes, ai-train=no`,
  EU DSM Art. 4 reservation).
