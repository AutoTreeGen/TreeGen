# ADR-0044: Person merge UI architecture (Phase 6.4)

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `frontend`, `merge`, `phase-6`, `audit`

## Контекст

Phase 4.6 (ADR-0022) приземлила backend и первый UI ручного merge'а персон по
маршруту `/persons/{id}/merge/{targetId}` — survivor-toggle (left/right) +
diff-секции. Этот UI работает, но не помогает пользователю принимать решение
**по полю**: он смотрит на 4–10 разнящихся атрибутов одновременно и решает,
какая сторона канонична. На реальных GED-данных (Ancestry vs MyHeritage import)
это часто длинный список расхождений, и пользователь не помнит, почему он
выбрал «left».

Phase 6.4 добавляет field-by-field merge UI:

- маршрут `/persons/merge/{primaryId}?candidate={candidateId}`;
- для каждого conflicting field — отдельный resolver с radio-выбором и
  опциональной заметкой;
- preview-pane показывает «как будет выглядеть merged person» с учётом
  выборов;
- merge log по маршруту `/persons/{id}/merge-log` с undo-кнопкой в 90-day
  окне (ADR-0022 §undo policy).

Ограничения:

1. **Backend контракт фиксирован** ADR-0022: `MergeCommitRequest` принимает
   ровно один `survivor_choice: "left" | "right"`, поле-уровневых
   override'ов нет. Field-level UI не может изменить per-field result —
   только повлиять на «какая сторона survivor».
2. **CLAUDE.md §5 запрещает auto-merge.** Любая UI-эвристика должна
   оставаться **подсказкой**, а не действием — финальный submit требует
   явного `reviewed`-чекбокса и `confirm_token` (одноразовый UUID).
3. **PR-бюджет ≈ 500 LOC.** Frontend-only; никаких изменений schema /
   миграций / backend-кода.
4. **i18n обязательная** (ADR-0037): все user-facing строки — в
   `messages/{en,ru}.json` под ключом `persons.merge.*`.

## Рассмотренные варианты

### Wizard (multi-step)

Каждое conflicting field — отдельный шаг wizard'а: «Step 1/8: Sex», «Step
2/8: Birth date», … В конце — preview, потом confirm.

- ✅ Принудительная сосредоточенность: пользователь видит одно поле и
  одно решение.
- ✅ Хорошо подходит для длинных списков (≥10 полей).
- ❌ Не видно общей картины — нет контекста «как это меняет персону в
  целом».
- ❌ Накладные расходы: navigation + back-button логика, валидация
  «прошёл ли все шаги».
- ❌ Уход от текущего UI Phase 4.6 (single-page diff) — повышенная
  cognitive load для пользователей, которые уже привыкли.
- ❌ Хуже на mobile: каждый шаг — отдельный round-trip mental.

### Side-by-side (Phase 4.6 текущая) — расширение

Оставляем single page; на ней — список field-resolver'ов (один на каждое
конфликтующее поле) + preview-pane снизу + confirm-блок.

- ✅ Видна общая картина: пользователь scroll'ит, видит все расхождения
  сразу.
- ✅ Continuity с Phase 4.6 UI: тот же layout (header, sides, diff,
  confirm), только diff превращается в interactive resolver'ы.
- ✅ Preview-pane прямо под resolver'ами: каждый клик радио меняет
  preview — мгновенный feedback.
- ✅ Простой компонентный декомпоз: `ConflictResolver` reusable, можно
  переиспользовать для merge источников / мест в будущем.
- ❌ На очень длинных списках (15+ конфликтов) скроллинг становится
  неудобным — но реальный datasize редко такой.
- ❌ Все поля сразу = меньше принуждения к вдумчивости — компенсируем
  обязательным reviewed-чекбоксом.

### Side-by-side с modal-resolver на клик

Поля показываются как summary-плашки; клик открывает модал с radio +
note. После закрытия плашка обновляется.

- ✅ Чище визуально для длинных списков.
- ❌ Двойной клик на каждое поле — медленнее для типичного case'а
  (3–5 полей).
- ❌ Модалы плохо сочетаются с keyboard navigation; accessibility
  сложнее.

## Решение

Выбран **side-by-side single-page** (Вариант 2). Reasoning:

1. **Continuity** с Phase 4.6 UI важнее, чем UX-плюсы wizard'а: пользователь,
   прошедший туториал на старом UI, не теряется на новом.
2. **Preview pane инкрементально пересчитывается** при каждом radio-клике.
   Это даёт мгновенную обратную связь, недоступную в wizard'е (там
   нужно перейти в финальный шаг).
3. **`ConflictResolver` компонент** — reusable: можно переиспользовать
   при merge источников (Phase 6.5) и мест (Phase 6.6) без изменения
   контракта. Wizard-шаги переиспользовать сложнее.
4. **Mapping field-picks → survivor_choice**: мажоритарное голосование.
   Если 3 из 5 полей пользователь выбрал «right» — `survivor_choice='right'`.
   Tie → `default_survivor_id` бэкенда. Это документировано в UI («survivor
   determined by your majority of field picks») и не претендует на
   per-field semantics, которой бэкенд не поддерживает.

## Уважение invariant'а 90-day undo

Merge log (`/persons/{id}/merge-log`) показывает каждый merge с timestamp,
ролью (survivor / merged) и кнопкой undo. Кнопка:

- **Disabled**, если `now - merged_at > 90 days` (UI-side `isMergeUndoable`
  helper, server остаётся source of truth и вернёт 410 если промахнулись).
- **Disabled**, если `undone_at != null` (уже откачено).
- **Disabled**, если `purged_at != null` (фоновый cron физически удалил).
- **Enabled** иначе → POST `/persons/merge/{id}/undo`, на success
  invalidate'им query `["merge-history", personId]` + `["person", personId]`.

90-day окно жёстко закодировано в `MERGE_UNDO_WINDOW_DAYS = 90` в
`apps/web/src/lib/api.ts` рядом с типами merge-API. Эта константа — **mirror**
ADR-0022 §undo policy; backend остаётся источником правды (UI не может
«просрочить» окно раньше, чем backend, и не может «продлить» за 90 дней).

Если backend в будущем изменит окно — нужно обновить константу и тесты;
DB-миграция сама по себе UI-константу не двигает (специально, чтобы
случайное изменение конфига не «удлинило» окно в UI без явного PR).

## Поля на UI

Backend возвращает `MergeFieldDiff[]` с произвольными `field`-строками.
`persons.merge.fields.labels.*` в i18n знает 10 канонических ключей
(`gedcom_xref`, `sex`, `confidence_score`, `birth_date`, `death_date`,
`given_name`, `surname`, `primary_name`, `place`, `sources`); неизвестные
поля — fallback на raw-имя. Это удерживает UI стабильным при добавлении
новых полей в backend без обязательной обновки i18n (но добавление label'а
в обе локали в том же PR — сильно рекомендуется).

## Свободно-текстовая заметка (note)

`onNoteChange` — опциональный callback в `ConflictResolver`. Заметка
**не отправляется на бэкенд** (request schema `MergeCommitRequest` имеет
`extra="forbid"`). Это сознательное решение Phase 6.4: заметки служат
**пользователю самому** для рассуждения «вслух» перед коммитом и попадают
в preview-pane как «Note: …» под выбранным значением.

В будущем (после расширения backend audit-payload'а) заметки можно
сериализовать в `provenance.merge_notes[]` через отдельный endpoint, но
это вне scope'а Phase 6.4.

## Тестирование

- **`conflict-resolver.test.tsx`** — render для разных типов значений
  (string, number, null, boolean, object), verify radio onChange'и,
  identical-плашка, conditional рендер note-textarea.
- **`person-merge.test.tsx`** — submit-flow (mock fetchMergePreview +
  commitMerge), confirm-checkbox gating, missing-candidate state,
  90-day window edge cases с `vi.useFakeTimers`, i18n parity (en + ru
  без missing-key fallback'ов).

## Последствия

- ✅ Field-by-field UI снижает frustration на длинных списках расхождений;
  заметки помогают будущему-себе вспомнить «почему».
- ✅ Reusable `ConflictResolver` готов для merge источников/мест.
- ✅ Backend контракт не изменился — нулевая миграционная стоимость.
- ❌ UI-mapping «majority → survivor_choice» — упрощение. Если
  пользователь хочет per-field результат («оставь сурвайвором A, но
  возьми у B birth_date»), эмуляция через два последовательных merge'а
  возможна, но нетривиальна. Документируем как known-limitation и
  пересматриваем, если жалобы.
- ❌ Текстовая заметка живёт только в UI до commit'а. После commit'а она
  не сохраняется. Не блокирующее ограничение Phase 6.4 — будущая фаза
  может добавить персистентность.

## Когда пересмотреть

- Если 80%+ пользователей делают merge с 1 полем — wizard-шаг становится
  оправданным (одно поле = один шаг).
- Если backend ADR будет дополнен per-field override'ами — UI mapping
  «majority → survivor_choice» уберём в пользу прямого `field_overrides`
  payload'а.
- Если заметки реально будут полезны после-merge (audit, undo с
  объяснением «почему мы так сделали») — Phase 6.5+ добавит endpoint
  для их персистентности.
