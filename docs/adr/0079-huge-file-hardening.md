# ADR-0079: Phase 5.11 — Huge-File & Encoding Hardening (discovery-first)

- **Status:** Accepted
- **Date:** 2026-05-02
- **Authors:** @autotreegen
- **Tags:** `phase-5-11`, `gedcom-parser`, `encoding`, `dedup`, `geoffrey-demo`, `discovery`

> Helper `scripts/next-chain-number.ps1 -Type adr` returned **0078** as next
> free; brief explicitly assigned **0079** to leave 0078 to a sibling phase
> in flight. Following brief.

## Контекст

Демо для Geoffrey Michael запланировано на **2026-05-06** (через 4 дня). Его
рабочий tree — `GM317_utf-16.ged`, 145 МБ, UTF-16-LE, ~30 000+ дубликатов
персон, INTENTIONAL: Geoffrey помечает всех «related» людей в одном файле
(rolodex-style), чтобы перекрёстно фиксировать связи без потери видимости.
Дополнительно у него есть `RR.ged` — 116 МБ дерево от родственника
(Robert, deceased), которое он унаследовал и хочет загрузить ради сверки.

До Phase 5.11a у нас не было фактических метрик, как ведут себя 5.6
(compat sim), 5.7 (diff/merge), 5.8 (validator) и 5.10 (fantasy filter)
на файлах такого размера/формы. Мы видели «что-то медленное» в
single-shot ad-hoc прогонах, но не имели:

- надёжного peak-RSS / wall-time замера на target-файлах;
- ответа, парсится ли GM317 вообще (UTF-16, 30K дубликатов);
- картины xref-integrity (orphans, dangling, ancestor cycles);
- понимания где в pipeline-стэке проявляются задержки/OOM;
- дифференциального сравнения с corpus'ом (Ancestry, MyHeritage, Geni и
  тривиальными ru-файлами).

Без этих чисел любой план фиксов — спекуляция. Решения уровня «давайте
параллелизуем парсер» или «давайте дедуплицировать на лету» рисковали
бы за оставшиеся 4 дня испортить демо или, хуже, нарушить Geoffrey'ев
рабочий поток (см. ниже).

Параллельно проявились архитектурные **соблазны-ловушки**, которые ADR
закрывает заранее:

1. **«Forced dedup как fix»** — 30K+ collision-групп выглядят как баг,
   но это намеренное поведение пользователя. Любой автомерж разрушит его
   workflow и даст ложное «улучшение качества».
2. **«Rewrite the parser»** — у нас рабочий 5.5.5-парсер с round-trip и
   валидатором. Полный rewrite за 4 дня — катастрофа.
3. **«Manual encoding flag»** — UTF-16 файлы должны открываться без
   флага: Geoffrey ждёт, что его файл просто заработает.
4. **«PII в коммите»** — discovery даёт sample имён, дат, мест; ни один
   из них не должен попасть в diff. Только агрегированные метрики.

## Рассмотренные варианты

### A. Просто запустить полный fix-sprint без discovery

- ✅ Кажется быстрее: 4 дня = «давайте сразу чинить».
- ❌ Без чисел невозможно ранжировать, что демо-блокер, а что косметика.
- ❌ Большой риск чинить «неправильное»: например, тратить день на
  ANSEL-decoder, когда блокер — peak RSS на UTF-16.
- ❌ Ничего не остаётся для будущей регрессионной защиты.

### B. Discovery-only фаза без ADR / без plan-of-fixes

- ✅ Безопасно, никаких артефактов кроме отчёта.
- ❌ Не даёт владельцу способ выбрать, какой fix-PR следующим вкатить.
- ❌ Через неделю отчёт устареет (файлы могут немного измениться,
  парсер — точно), и никто не вспомнит, какие из issues остались.

### C. **Discovery-first → targeted fix-briefs (выбрано)**

Двухслойная фаза:

1. **5.11a (этот ADR + отчёт)** — read-only diagnostic pass:
   `scripts/discovery/` пробы → `docs/discovery/2026-05-02-huge-file-findings.md`
   с numeric-первой Issue Catalog (HUGE-NNN), severity, demo-blocker
   маркер, conkretным repro и suggested-фазой.
2. **5.11b/c/d/e** (отдельные брифы, ADR-0080+ если потребуется) — каждый
   решает один сфокусированный issue из catalog'а: например 5.11b ≈
   peak-RSS / streaming parse, 5.11c ≈ UTF-16 zero-flag путь, 5.11d ≈
   dup-tolerance в validator (но НЕ автомерж), 5.11e ≈ progress UX.
   Что именно станет b/c/d/e — диктуется отчётом, а не этим ADR.

- ✅ За 4 дня успеваем сначала измерить, потом точечно чинить только
  реальные демо-блокеры, потом продемонстрировать Geoffrey'ю.
- ✅ Issue catalog становится регрессионным контрактом для будущих фаз.
- ✅ Никакой production-код не трогается, риск нулевой.
- ❌ Один лишний PR-цикл (5.11a сам по себе) до начала фиксов. Цена
  оправдана соотношением «4 часа discovery / N×часов промахнутых fix'ов».

## Решение

Выбран **вариант C — discovery-first**. Phase 5.11a:

- **Output A:** `docs/discovery/2026-05-02-huge-file-findings.md`
  — fact-based numeric catalog HUGE-001..NNN, ранжированный по severity и
  demo-blocker-флагу.
- **Output B:** этот ADR — фиксирует scope, anti-drift инварианты и
  что задеферено в 0080+.
- **Output C:** `scripts/discovery/{probe.py, probe_dupes.py, run_all.py}`
  — переиспользуемые read-only пробы. Никаких production-сайд-эффектов,
  никаких hardcoded путей (env var `GEDCOM_TEST_CORPUS`).

Phase 5.11b+ будут отдельными фазами, опирающимися на Issue Catalog.

## Инварианты (anti-drift)

Эти инварианты — **жёсткие**: любой будущий fix-бриф для 5.11.x должен
явно их соблюдать.

1. **NO forced dedup.** Geoffrey'ев workflow с 30K дублей —
   намеренный. Любая автоматическая «чистка» ломает его.
   Fix-предложения уровня «дедуплицировать на импорте» отвергаются.
   Допустимы: отдельный merge-suggestion UX (рекомендация, не действие);
   tolerance в validator/compat-sim, чтобы они не «кричали» на
   intentional duplicates.
2. **UTF-16 zero-flag.** GM317_utf-16.ged должен парситься без
   передачи каких-либо «encoding hint» флагов. BOM-detection — required
   path, не опциональный.
3. **No production code in 5.11a.** Только `docs/` и
   `scripts/discovery/`. Никаких изменений в `packages/` или `services/`.
4. **No GED bytes / PII в diff.** Любые sample-имена в отчёте — либо
   агрегированные counts, либо xref-only. Raw probe outputs хранятся в
   `_discovery_runs/`, который gitignored.
5. **No hardcoded `F:\Projects\GED`** в committed коде. Только
   `GEDCOM_TEST_CORPUS` env var (соответствует существующему соглашению
   `tests/test_smoke_personal.py` и memory `test_corpus_gedcom_files.md`).
6. **No "rewrite the parser".** Все улучшения — инкрементальные хирург-
   ические патчи рабочего парсера, валидатора, compat-sim'а. ADR-0007
   (GEDCOM 5.5.5 как канонический) остаётся в силе.
7. **Geoffrey demo (2026-05-06) — приоритет.** При конфликте между
   «строгая корректность» vs «не сломать демо» — приоритет за демо;
   корректность доводится в фоновом порядке.

## Последствия

**Положительные:**

- Подтверждаемые числа вместо догадок при планировании 5.11b+.
- `scripts/discovery/` — переиспользуемая инфраструктура для будущих
  «как ведёт себя парсер на огромных файлах» прогонов (например, перед
  каждым релизом).
- Issue Catalog (HUGE-NNN) становится trackable backlog'ом.

**Отрицательные / стоимость:**

- +1 PR-цикл и +1 ADR в дереве истории до начала фиксов. ~2–3 часа
  разработческого времени на discovery до первого fix'а.

**Риски:**

- Riska, что во время probe мы дёрнем какой-нибудь парсерный crash'ер
  и потеряем результаты — митируется через `subprocess.run(timeout=…)`
  с peak-RSS sampler'ом и per-file сохранением результатов в
  `_discovery_runs/`.
- Риск, что probe-метрики окажутся неточными (например, мы измерим
  peak RSS не в той точке) — митируется тем, что отчёт чётко указывает,
  как был получен каждый показатель, и пробы остаются в репо для
  переинвокации.

**Что нужно сделать в коде в рамках 5.11a:**

- Только новые файлы в `docs/` и `scripts/discovery/`.
- Никакие production-модули не правятся.

## Что задеферено (open questions для 0080+)

Эти вопросы намеренно оставлены вне scope 5.11a и должны быть закрыты в
отдельных ADR/брифах если соответствующие fix-фазы вкатываются:

- **OQ-1: Streaming-парсер vs in-memory.** Если 145 МБ + 30K INDI
  пробивают какой-то порог по peak RSS, нужен ли refactor parser →
  streaming pipeline с lazy entity build? Решает 5.11b после прочтения
  отчёта.
- **OQ-2: ANSEL-декодер.** Сейчас fallback на latin1 с warning. Стоит
  ли вкладываться в полноценный ANSEL → unicode mapping, и если да, в
  какой фазе? Решает 5.11c (или 5.11.x), если хоть один файл из corpus
  действительно ANSEL.
- **OQ-3: Dup-tolerance в validator.** Validator должен ли иметь режим
  «high-dup tree» где `duplicate_child` / `same_sex_spouse_pair` /
  `parent_age` finding'и downgrade'ятся до info? Решает 5.11d.
- **OQ-4: Frontend rendering at 30K nodes.** Известны ли breakpoints
  у текущего tree-viewer'а на 30K-tree, и если да, какой UX-fallback
  (виртуализация / пагинация / level-of-detail rendering)? Решает 5.11e
  или отдельная фронтовая фаза, опираясь на ADR-0013 / 0066.
- **OQ-5: Merge-suggestion UX без автомержа.** Как показать пользователю
  «вот 12000 пар возможных дублей» так, чтобы он мог отбросить
  intentional ones и принять решение по реальным? Решает отдельная фаза,
  опираясь на ADR-0044 (Person Merge UI).

## Когда пересмотреть

- После 2026-05-06 (демо Geoffrey'я): если демо прошло без блокеров,
  фокус 5.11.x смещается с demo-блокеров на корректность.
- Если в 5.11b+ обнаружится, что один из инвариантов выше слишком жёсток
  (например, пользователь сам просит автомерж как opt-in) — надо
  пересмотреть данный ADR явным supersession-ом, не неявным дрейфом.
- Если parser получает несовместимый rewrite (см. ADR-0007 ревизия) —
  пересмотреть discovery-first структуру и пересоздать issue catalog.

## Ссылки

- Связанные ADR: ADR-0007 (GEDCOM 5.5.5 канонический), ADR-0044 (Person
  Merge UI), ADR-0062 (GEDCOM Quarantine Round-trip), ADR-0067 (design
  system v1).
- Связанные фазы: 5.6 (compat-sim), 5.7 (diff/merge), 5.8 (validator),
  5.10 (fantasy filter).
- Discovery report: `docs/discovery/2026-05-02-huge-file-findings.md`.
- Probe scripts: `scripts/discovery/`.
