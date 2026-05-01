# ADR-0068: Self-anchor (`trees.owner_person_id`) + ego-relationship resolver (Phase 10.7a)

- **Status:** Proposed
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `phase-10.7`, `ai`, `tree-ontology`, `i18n`, `mcp-precursor`

## Контекст

Phase 10.0 (ADR-0043) и 10.1 (ADR-0057) посадили AI-foundation: Anthropic
для рассуждений, Voyage для эмбеддингов, prompt-registry, pricing-таблица,
Redis-телеметрия. Phase 10.2 / 10.3 (ADR-0059, 0060) вырастили source/text
extraction. Phase 10.9 (ADR-0064) — voice-to-tree.

**Pain-point владельца, который Phase 10.7 решает:** AI-фичи **не знают,
кто ты** в твоём собственном дереве. Hypothesis-explainer описывает
гипотезу как "person X — son of person Y"; chat (10.7d) запрашивая родство,
не различает «брата жены» (degree 2 через spouse) и «брата тёщи»
(degree 3 через spouse → mother → sibling); MCP server (10.8 — стратегическое
решение позже) без эго-якоря не может ответить на самый базовый вопрос:
«как ко мне относится этот человек?».

В реальности AJ-genealogy дерева owner — это не просто «root persona»: он
сам — точка отсчёта для большинства narrative-описаний. Без явного якоря AI
вынужден строить родство относительно произвольной персоны (обычно root,
которым может быть прадед); это даёт грамматически правильные, но
семантически непригодные подписи («твой пра-прадед») вместо естественных
(«ты сам»/«твоя жена»).

Phase 10.7 распадается на 5 компонентов:

- **10.7a** — **Self-anchor + ego-resolver** (этот ADR). Фундамент: где
  сидит ego в дереве + как считать родство «от ego». Без этого 10.7b/d/8
  невозможны — нет answering-perspective.
- **10.7b** — Context Pack serializer. Использует 10.7a, чтобы собрать
  ego-aware текстовый снапшот дерева для prompt-инъекции.
- **10.7c** — Research annotations layer (отдельный data-model вопрос).
- **10.7d** — Chat UI. Потребитель 10.7a через `GET /relationships/{id}`
  для badge'ов «your relationship to this person: wife's brother».
- **10.8** — MCP server identity API. Потребитель 10.7a для exposing'а
  ego-relations внешним AI-host'ам.

## Решение

Принять четыре связанных решения, оставшиеся 4 компонента 10.7 строятся
поверх них.

### Решение 1: `owner_person_id` колонка на `trees`, не отдельная таблица

`ALTER TABLE trees ADD COLUMN owner_person_id uuid NULL REFERENCES persons(id) ON DELETE SET NULL`.

**Why:** ego — 1:1 с деревом. История смены якоря не нужна (если поменял —
новое значение, старое забыто). Отдельная `tree_owner_anchor` таблица
давала бы:

- лишний JOIN на каждом read'е,
- audit-trail сменам, который никому не нужен (это user setting, не
  доменный факт),
- composite-FK сложности при cascade'ах.

`ON DELETE SET NULL`: если person удалена из дерева (legitimate flow —
person-merge, GDPR-erasure), anchor сбрасывается тихо, без RESTRICT'а
блокирующего delete. Re-anchor — owner-action, который UI попросит сделать.

### Решение 2: BFS at query-time, не materialized

Резолвер — `relate(from_person_id, to_person_id, *, tree: FamilyTraversal)`,
вызывается на каждый `GET /trees/{id}/relationships/{person_id}`. Caller
собирает `FamilyTraversal` snapshot (one SELECT по families/family_children,
плюс person_sex для humanize) и передаёт в pure-function пакета
`inference-engine.ego_relations`.

**Why:** для типичного дерева <1000 персон BFS обходится <50ms. Materialized
view (precomputed all-pairs distances) дал бы:

- write-amplification: каждый person-merge / family-edit инвалидирует
  весь куб,
- сложность invalidation logic,
- хранение O(N²) даже при sparse-родстве.

V1 — naive recompute. **Trade-off** записан в коде: если стрельнёт перф —
кэш по `(tree.version_id, ego_id)` hash'у в Redis с TTL. Это будущее
решение, не текущее.

### Решение 3: humanize() с in-package en/ru/he/nl/de строками

Пакет `inference-engine.ego_relations.humanize` содержит hardcoded
nominative + genitive (для русского) словари по пяти языкам. Путь
`['wife', 'mother', 'brother']` рендерится в:

- en: `"wife's mother's brother"`
- ru: `"брат матери жены"` (target в номинативе, цепочка genitive'ов справа налево)
- de: `"Bruder von Mutter von Ehefrau"` (preposition-based)
- nl: `"broer van moeder van vrouw"`
- he: `"אח של אם של אישה"`

**Why hardcoded vs i18n catalog:** словарный объём маленький (≤16 терминов
× 5 языков), и тексты нужны только в HTTP/Chat surface'е, не в основной
БД-логике. Каталог (next-intl JSON) был бы overhead'ом без выгод.
Когда понадобится 6-й язык — добавляется один словарь в одном файле.

### Решение 4: Twin disambiguation — explicit flag, не отдельный kind

`RelationshipPath.is_twin: bool` — флаг, который humanize вставляет как
«twin» в финальный термин:

- en: `"wife's brother"` + `is_twin=True` → `"wife's twin brother"`
- ru: `"брат жены"` + `is_twin=True` → `"брат-близнец жены"`

`kind` остаётся каноничным (`wife.brother`), а различимы близнецы через
флаг.

**Why flag vs separate kind:** `kind='wife.twin_brother'` дал бы
combinatorial explosion (`wife.mother.twin_brother`, `wife.mother.twin_sister`,
…) на каждое sibling-ребро в пути. Twin — это локальное свойство ребра, а
не глобальный путь-тип. Сохраняем canonical kind для удобства downstream
classifier'ов (Context Pack, MCP), флаг — отдельная ось.

Twin-detection в V1: два ребёнка одной семьи с одинаковым `birth_order > 0`.
Дефолт `birth_order = 0` означает «порядок неизвестен» — twin-pairs пусты,
false-positive исключён. Future-work: fallback на одинаковый `date_start`
у BIRT-event'ов; сейчас этого не делаем, потому что 90% импортных GED'ов
не выставляют `birth_order` явно, и адекватный twin-detection — отдельная
задача.

## Альтернативы

- **Materialized all-pairs path table.** Отвергнуто: O(N²) хранение +
  invalidation hell. См. Решение 2.
- **GraphQL-schema с computed fields**, где `Person.relationship_to_owner`
  всегда вычислялось бы. Отвергнуто: перевод всего API на GraphQL —
  не задача 10.7a; REST-endpoint'а достаточно.
- **Chat-style natural-language relationship engine** (LLM собирает kind
  по описанию). Отвергнуто: детерминизм критичен (см. CLAUDE.md §3 п.6).
  Эго-родство — closed-domain задача, LLM-overhead не оправдан.
- **`relationships` view-таблица** (Phase 15.x идея, ADR-0058). Отвергнуто
  для 10.7a: вьюшки нет, и она нужна для другого use case'а
  (relationship-level evidence). 10.7a живёт на families/family_children
  напрямую.

## Последствия

**Положительные:**

- Phase 10.7b Context Pack serializer получает чистый contract:
  `relate(ego, target_person)` для каждого факта в pack'е.
- Phase 10.7d Chat UI рисует ego-aware badges без дополнительных
  per-person resolve'ов на frontend'е.
- Phase 10.8 MCP server (если ship'нем) экспортирует identity-API
  естественно: `whoami(tree_id)` + `relate(target)`.
- Pure-function пакет — testable без БД (24 теста в `test_ego_relations.py`).

**Отрицательные / trade-off'ы:**

- BFS recompute на каждый GET — peak O(N) при N персон. Mitigation
  записана; до сигнала перф-проблемы — naive.
- Twin-detection пока упрощённый. Документировано в коде как future-work.
- 5 hardcoded языков — добавление 6-го требует code change, не каталога.
  Compromise pragmatic; full-i18n catalog — overhead для 16-терминного
  словаря.

## Будущая работа

- 10.7b serializer: использовать `humanize(path, tree.default_locale)`
  для подписей в Context Pack JSON.
- 10.7d Chat UI: badge компонент `<RelationshipBadge personId={...} />`
  потребляет `GET /trees/{id}/relationships/{personId}`.
- 10.8 MCP: thin wrapper вокруг `relate()` + `humanize()`, exposing
  как MCP tool `treegen.relate`.
- Когда 15.10 name-engine ship'нется — picker (Phase 10.7a UI) переключается
  на variant-aware matching (искать «Vladimir» = «Влад» = «Володя»).
- Twin-detection v2: fallback на `BIRT.date_start` equality.
- Пер-tree кэш `relate(ego, _)` в Redis с invalidation по
  `tree.version_id` — если стрельнёт перф.
