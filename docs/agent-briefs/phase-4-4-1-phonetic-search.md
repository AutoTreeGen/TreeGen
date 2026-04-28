# Agent brief — Phase 4.4.1: Daitch-Mokotoff phonetic search

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-phonetic`.
> Это естественное продолжение твоей Phase 4.4 (person search). Сейчас
> поиск по `Zhit` находит только substring-matches. Не находит
> `Жытницкий` (cyrillic), `Zhytnicki` (transliteration), `Schitnitzky`
> (older spelling). DM решает это в один индекс-лукап.
> Перед стартом: `CLAUDE.md`, своя Phase 4.4 PR (мерж был),
> `packages/entity-resolution/src/.../phonetic.py` (Phase 3.4 от Agent 2,
> уже в main — там DM имплементация).

---

## Зачем

Восточно-европейская / еврейская генеалогия — это лес транслитераций.
Мой дед Zhitnitzky пишется в архивах как Жытницкий, Жытніцкі (бел.),
Zhytnicki (укр.), Žytnicki, Schitnitzky (немецкий протокол). Все
эти варианты мапятся в одинаковый Daitch-Mokotoff bucket: `463950`.

Если поиск работает по DM bucket'у, юзер ввёл `Zhitnitzky` — нашли все
варианты. Это **revolutionary feature for Jewish genealogy** — на
Ancestry/MyHeritage есть soundex (плохой) и Beider-Morse (better, но
закрытый). У нас будет open DM с правильной поддержкой кириллицы.

---

## Что НЕ делать

- ❌ Реимплементировать DM — он уже есть в `entity-resolution`
  (Phase 3.4). Используй его.
- ❌ Включать DM по дефолту в обычном поиске. Должен быть toggle
  `?phonetic=true` или галочка в UI «Phonetic».
- ❌ Полагаться только на DM — комбинируй: substring сначала, DM
  если ничего не нашли ИЛИ если phonetic flag.
- ❌ Кэшировать DM-buckets в каждом запросе. Считаем bucket один
  раз при INSERT в `persons`, храним в колонке.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — Backend: добавить phonetic_bucket колонку

**Файл:** Alembic миграция в `infrastructure/alembic/versions/`.

```sql
ALTER TABLE persons ADD COLUMN surname_dm TEXT[];
ALTER TABLE persons ADD COLUMN given_name_dm TEXT[];
CREATE INDEX persons_surname_dm_gin ON persons USING GIN (surname_dm);
CREATE INDEX persons_given_name_dm_gin ON persons USING GIN (given_name_dm);
```

(DM возвращает list of buckets — surname с одним вариантом
кодирования может дать 1-3 bucket'а из-за неоднозначных позиций.
Поэтому массив + GIN-индекс.)

**Backfill:** скрипт `scripts/backfill_dm_buckets.py` который проходит
все `persons` пачками по 1000 и считает DM используя
`entity_resolution.phonetic.daitch_mokotoff_buckets(name)`. Idempotent
(можно перезапускать).

В `import_runner.py` (parser-service) добавь:

- При `upsert_person` вычислять DM-buckets и записывать в новые колонки.

### Task 2 — Backend: phonetic search endpoint

**Файл:** `services/parser-service/src/parser_service/api/persons.py`

В существующий `GET /trees/{id}/persons/search` добавь параметр
`phonetic: bool = False`:

- Если `phonetic=False` (default): прежнее поведение (ILIKE substring).
- Если `phonetic=True`: вычисли DM от `q`, найди persons где
  `surname_dm && computed_buckets` ИЛИ `given_name_dm && computed_buckets`
  (operator `&&` в Postgres = arrays overlap).
- Можно комбинировать с `birth_year_min/max`.

Response shape — добавь поле `match_type: "substring" | "phonetic"` для
прозрачности юзеру.

Тесты в `tests/test_persons_phonetic_search.py`:

- `q=Zhitnitzky&phonetic=true` находит вариации (синтетические в
  фикстуре: `Zhytnicki`, `Жытницкий`, `Schitnitzky`).
- `q=Cohen&phonetic=true` находит `Kohen`, `Cohn`, `Kahan`, `Кохен`.
- Substring поиск (phonetic=false) НЕ находит `Жытницкий` для `q=Zhit` —
  доказывает разницу.
- Performance: на 12k persons GIN index lookup < 50 мс.

### Task 3 — Frontend: phonetic toggle

**Файл:** `apps/web/src/app/trees/[id]/persons/page.tsx` (или твоя
Phase 4.4 страница).

Добавь:

1. `<Checkbox>` с label «Phonetic search (Daitch-Mokotoff)» рядом с
   search input.
2. Tooltip объясняющий: «Find name variants across spellings —
   Zhitnitzky finds Жытницкий, Zhytnicki, etc. Useful for Jewish/
   Eastern European genealogy.»
3. URL state: `?phonetic=true` пробрасывается в API call.
4. В результатах добавь маленькую плашку рядом с persons где
   `match_type === "phonetic"`: «via phonetic match» (тонкий gray
   текст), чтобы юзер понимал почему этот результат.

### Task 4 — Финал

1. ROADMAP §4.4.1 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-4.4.1-phonetic-search`.
4. CI green до merge. Никакого `--no-verify`.
5. **Скриншот в PR**: search по `Zhitnitzky` с phonetic=true и
   результатами с разными вариантами spelling.

---

## Сигналы успеха

1. ✅ DM-buckets хранятся в БД, GIN-индекс работает.
2. ✅ Backfill скрипт прогнан на моём GED — все persons имеют buckets.
3. ✅ Phonetic search находит варианты (manual test:
    `Zhitnitzky` → вариации в моём древе).
4. ✅ Performance < 100 мс на 12k persons.
5. ✅ UI toggle работает, плашка `via phonetic match` видна.

---

## Если застрял

- DM из entity-resolution возвращает не то что ожидал → проверь сигнатуру
  функции, возможно есть `daitch_mokotoff_buckets()` или
  `daitch_mokotoff()` — название может отличаться.
- Кириллица в DM не поддерживается → fallback: транслитерируй кириллицу
  в латиницу через `entity_resolution.string_matching.iso9_transliterate`,
  потом DM. Pre-bucket компонентом, не в каждом запросе.
- GIN-индекс tormoзит INSERT в больших импортах → batch-insert уже
  должен помогать; если нет — добавь `CREATE INDEX CONCURRENTLY` после
  больших import jobs (TODO в коде).
- entity-resolution package не экспортирует DM-функции public →
  обновить `packages/entity-resolution/src/.../__init__.py`.

Удачи.
