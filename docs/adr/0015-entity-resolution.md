# ADR-0015: Entity resolution — suggestions, не auto-merge

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `entity-resolution`, `dedup`, `fuzzy-matching`, `phase-3`

## Контекст

После Phase 3.3 в БД лежит полный набор GEDCOM-сущностей: persons,
names, places, sources, citations, multimedia. Реальный workflow
владельца — последовательно импортировать **несколько разных GED**
(Ancestry, MyHeritage, Geni, собственные сборки разных лет) в одно
дерево, плюс сюда же позднее придут архивные находки. Каждый импорт
создаёт *новые* строки — без дедупликации одни и те же люди / места /
источники появляются 2–4 раза.

Системные источники путаницы:

1. **Транслитерация фамилий.** Восточно-европейская / еврейская
   генеалогия — `Zhitnitzky` / `Zhytnicki` / `Жytницкий` / `Żytnicki` —
   это один человек. Стандартный Soundex (английский) сглаживает
   гласные, но плохо работает на славянских корнях.
2. **Иерархия мест.** «Slonim» / «Slonim, Grodno» / «Slonim, Grodno
   Governorate, Russian Empire» — то же место с разной точностью.
3. **Шумные source-titles.** «Lubelskie parish records 1838» vs
   «Lubelskie Parish 1838» vs «Любельские метрики 1838» — один SOUR.
4. **Опечатки и смесь регистров** в любых полях.

Параллельно действует **жёсткое правило проекта** (CLAUDE.md §5):

> ❌ Автоматический merge персон с близким родством без manual review.

Цена ошибочного auto-merge на дереве с 5–10 поколениями — потеря
ветви или фабрикация связей, которые потом мучительно undoить через
audit-log. Поэтому Phase 3.4 — **только suggestions**: алгоритм
указывает «вот пары, которые похожи, вот score и компоненты», а
финальное решение принимает человек через UI Phase 4.5.

## Рассмотренные варианты

### Вариант A — Не делать ничего, dedup руками в UI

Ничего автоматически не считаем, в UI Phase 4.5 user сам ищет дубликаты.

- ✅ Минимум кода, ноль false-positive.
- ❌ На дереве в 1–10k персон руками не справиться. User уйдёт.
- ❌ Не лечит главную боль — повторный импорт.

### Вариант B — Auto-merge при confidence > 0.95

Считаем pair-score; всё ≥ 0.95 мерджим автоматически, audit-log
сохранит «откат через 30 дней».

- ✅ Меньше кликов в UI.
- ❌ Прямо нарушает CLAUDE.md §5.
- ❌ False-positive на близких родственниках (отец/сын с одинаковыми
  именами — частое явление в восточноевропейской традиции).
- ❌ Audit-undo на сложном merge (много детей, событий, медиа) —
  технически сложно сделать корректно.

### Вариант C — Pure-function алгоритмы → suggestions, без mutation БД

Алгоритмы (Soundex, Daitch-Mokotoff, Levenshtein, token-set, blocking)
живут в `packages/entity-resolution/` как pure functions без зависимости
от БД и SQLAlchemy. Сервисный слой (`services/dedup_finder.py`) только
**читает** БД и возвращает `list[DuplicateSuggestion]`. API
(`GET /trees/{id}/duplicate-suggestions`) — просто фасад. Мердж ⇒
Phase 4.5 через UI с manual approval, отдельный endpoint, audit-log.

- ✅ Соблюдает CLAUDE.md §5 (no auto-merge).
- ✅ Алгоритмы тестируются синтетически (без БД).
- ✅ Один и тот же scorer переиспользуется в Phase 4.5 (UI), Phase
  3.5 (background pre-import dedup), Phase 6.x (DNA-+ паспорт matching).
- ✅ Performance budget управляемый: blocking → O(n × bucket_size)
  вместо O(n²).
- ❌ Нужен второй шаг (UI) — больше кликов для user'а.
- ❌ Дубликаты в БД остаются до явного approve.

### Вариант D — ML / sentence-transformers embedding match

`paraphrase-multilingual-MiniLM` или аналог: имена → vectors →
cosine similarity.

- ✅ Лучше всего на свободных текстах (source titles, captions).
- ❌ Нужна GPU-инфраструктура (Phase 4.x+, не сейчас).
- ❌ Нет explainability — почему совпало, почему нет.
- ❌ Языковая специфика (русский ↔ польский ↔ idиш) у MiniLM слабая,
  нужно дообучать.

## Решение

Выбран **Вариант C**.

Алгоритмы — детерминированные, легко проверяемые на фикстурах,
объяснимые («совпало по DM-bucket + Levenshtein name + birth_year ±2»).
ML отложен до Phase 4.x, когда появится GPU-runtime и больший корпус
для обучения / валидации.

### Алгоритмы

**Sources (`source_match_score`):**

- `token_set_ratio` на normalized title — weight 0.7.
- `Jaccard` на split authors (split по `,;`) — weight 0.2.
- Boost +0.1, если abbreviation совпадают exactly.
- Threshold 0.85+ → likely duplicate.

**Places (`place_match_score`):**

- `token_set_ratio` (handles «Slonim» vs «Slonim, Grodno»).
- Substring containment boost — +0.15, если короткая строка
  целиком входит в длинную как префикс. Это ловит «Slonim ⊂ Slonim,
  Grodno, Russian Empire».
- Threshold 0.80+.

**Persons (`person_match_score`):**

`PersonForMatching` dataclass — `(given, surname, birth_year,
death_year, birth_place, sex)`. Композитный score:

1. **Phonetic bucket match** (weight 0.30):
   - Soundex(surname) совпал → 1.0.
   - DM(surname) множества пересеклись → 1.0 (DM возвращает 1+ кодов
     для имени, два имени совпадают если хоть один код общий).
   - Иначе → 0.0.
2. **Name Levenshtein** (weight 0.30):
   - max(`levenshtein_ratio(given_a, given_b)`,
         `levenshtein_ratio(surname_a, surname_b)`) — на нормализованных
     (lower, без диакритики).
3. **Birth year proximity** (weight 0.20):
   - exact match → 1.0.
   - ±1–2 года → 0.7.
   - Если у одного `None` → нейтрально 0.5.
   - Иначе → 0.0.
4. **Birth place** (weight 0.20):
   - `place_match_score(birth_place_a, birth_place_b)`.
   - Если у одного `None` → нейтрально 0.5.

**Hard filter:** `sex_a != sex_b` и оба известны (ни один не `U`/`X`)
→ score = 0.0 (return immediately).

`person_match_score` возвращает `(composite, components)` —
components используется для explainability в UI Phase 4.5.

### Confidence levels (для UI)

| Score      | Семантика                       | UI поведение |
|------------|----------------------------------|--------------|
| ≥ 0.95     | Almost certainly same person/source/place | Highlight strongly, кнопка «Merge» в первой линии |
| 0.80–0.95  | Likely same                      | Suggest, user reviews |
| 0.60–0.80  | Possibly same                    | Show in «inspect carefully» list |
| < 0.60     | Discard                          | Не показывать |

**Default API threshold:** 0.80 (показываем likely + verify).

### Daitch-Mokotoff

Daitch-Mokotoff Soundex (1985, обновление традиционного Soundex
специально для еврейских / восточно-европейских фамилий) — основной
phonetic кодер для persons. Стандартный Soundex остаётся как
дополнительный bucket (handles англоязычные ветви).

**Библиотека:** `pyphonetics` (MIT license, активный maintenance,
поддерживает оба алгоритма). MVP — внешняя зависимость, переход на
own implementation — Phase 3.4.x если понадобится контроль над
вариантами кодов или пограничными случаями (например, кириллица
напрямую без транслитерации).

### Performance / blocking

Naive pair comparison на дереве из N persons — O(N²). На 10k persons
это 50M пар, каждая ~1 мс с Levenshtein → 14 часов. Неприемлемо
даже для async background.

**Blocking strategy:**

1. Каждая persona получает множество DM(surname) кодов (1–2 шт.
   для большинства имён).
2. Bucket = «все persons с общим DM-кодом».
3. Compare только внутри bucket → O(N × max_bucket_size).
4. Для дерева 10k персон с разумным распределением фамилий это
   ~10k × 50 = 500k пар → секунды.

`blocking.py` — pure function, тестируется отдельно.

## Последствия

**Положительные:**

- Дедуп не ломает дерево: всё, что предлагает алгоритм, явно идёт
  через manual approval. Audit-log в порядке, undo тривиальный
  (suggestions просто удаляются / помечаются rejected).
- Алгоритмы переиспользуются: тот же `source_match_score` нужен
  Phase 3.5 (idempotent re-import) и Phase 6 (DNA match → archive
  source).
- Pure-function пакет → 100% покрытие unit-тестами без БД, быстрый CI.
- Explainability: UI может показывать, почему confidence именно такой
  («совпали DM-bucket + birth_year ±1»).

**Отрицательные / стоимость:**

- Дубликаты остаются физически в БД пока user не одобрит merge.
  Запросы по дереву (counts, ancestors) могут давать «странные»
  результаты в окне между импортом и approve. Mitigation — UI
  показывает «N suggestions pending» баннер.
- Pyphonetics добавляет dependency (минорная).
- Blocking может пропустить пары где DM(surname_a) ≠ DM(surname_b)
  даже при опечатке. Mitigation — fallback full O(N²) для small trees
  (< 500 persons), плюс будущая фаза с n-gram blocking.

**Риски:**

- False-positive на близких родственниках с одинаковыми именами
  («Меер Житницкий» отец и сын). Mitigation — birth_year filter
  (если оба известны и разница > 15 лет, score падает); UI
  обязательно показывает дату/место для disambiguation.
- Performance на дереве > 100k persons: blocking может оказаться
  недостаточным, понадобится более продвинутый MinHash/LSH.
  Mitigation — отдельная metric «time to compute suggestions» в
  Prometheus, alert при > 60s.

**Что нужно сделать в коде:**

1. `packages/entity-resolution/` — новый workspace member с
   `phonetic.py`, `string_matching.py`, `sources.py`, `places.py`,
   `persons.py`, `blocking.py`. Зависимости: `rapidfuzz>=3.0`,
   `pyphonetics>=0.5.3`.
2. `services/parser-service/services/dedup_finder.py` —
   `find_source/place/person_duplicates(session, tree_id, threshold)`
   возвращающий `list[DuplicateSuggestion]`. **READ-ONLY** — никаких
   `UPDATE` / `DELETE`, проверяется тестом.
3. `services/parser-service/api/dedup.py` —
   `GET /trees/{tree_id}/duplicate-suggestions` с query params
   `entity_type`, `min_confidence`, `limit`, `offset`.
4. `parser_service/schemas.py` — `DuplicateSuggestion`,
   `DuplicateSuggestionListResponse`.
5. `parser_service/main.py` — register новый router.
6. ROADMAP §7.0 — отметить 3.4 done.

## Когда пересмотреть

- Если дерево вырастает до > 100k persons и blocking перестаёт
  справляться (> 60s на full scan) → пересмотреть на MinHash / LSH
  или partition-based blocking.
- Если появится stable ML-runtime с GPU (Phase 4.x+) → добавить
  embedding-based fallback **рядом** с детерминированным scorer'ом
  (не вместо — explainability нельзя терять).
- Если pyphonetics станет неподдерживаемым / дроповым → переписать
  Daitch-Mokotoff на собственной реализации (алгоритм public, описан
  в Avotaynu Vol. I no. 3).
- Если cross-tree dedup понадобится (например, public-tree merge) —
  отдельный ADR, текущий scope строго within-tree.

## Ссылки

- CLAUDE.md §5 — запрет auto-merge персон.
- ADR-0001 — стек (rapidfuzz, pyphonetics уже совместимы).
- ADR-0003 — versioning (audit-log для merge подтверждённых через UI).
- ROADMAP §7.0 task 3.4.
- Daitch-Mokotoff Soundex: <https://www.avotaynu.com/soundex.htm>
- pyphonetics: <https://github.com/Lilykos/pyphonetics> (MIT).
- RapidFuzz: <https://github.com/rapidfuzz/RapidFuzz> (MIT).
