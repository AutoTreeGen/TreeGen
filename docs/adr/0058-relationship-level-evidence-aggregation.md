# ADR-0058: Relationship-level evidence aggregation

- **Status:** Accepted
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `evidence`, `inference`, `ui`, `phase-15`

## Контекст

Phase 6.x доставила DNA matches, Phase 7.x — hypothesis engine с
Bayesian fusion (Phase 7.5 / ADR-0057), Phase 10.x — AI source extraction.
Backend знает, какие sources подтверждают каждую гипотезу о связи между
двумя сущностями. Но в UI пользователь видит **дерево**, не список гипотез.
Когда профессиональный генеалог щёлкает по линии «отец → сын» в дереве,
он хочет увидеть **«какие источники именно про этот линк»**, а не
«какие источники упоминают этих двух людей» — это разные множества.

Phase 15 — pro-tier UI слой. Phase 15.1 — первая фича в нём, которую
пользователь видит глазами, не в API: **Relationship Evidence Panel**.
Right-side drawer, выезжающий по клику на edge между двумя persons,
показывающий supporting / contradicting / provenance с aggregated
confidence score.

Силы давления на решение:

1. **Mismatch между concept-моделью и схемой.** В UI пользователь думает
   термином «relationship» — узел графа со стабильным ID. В схеме
   parent-child живёт в `family_children` (junction-row, без provenance),
   spouse — в `families` (husband_id + wife_id, симметричная), sibling —
   derived (два children одного family). Нет single-table view с
   `relationship_id`.

2. **Provenance scattered.** Family-level provenance jsonb уже есть
   (TreeEntityMixins). Citations полиморфны (`entity_type` ∈ person /
   family / event). Hypothesis evidences — separate table с FK на
   hypothesis. UI не должен знать про эти три источника по-отдельности.

3. **Confidence rollup.** Phase 7.5 уже считает Bayesian-fused
   composite_score per hypothesis. Если на pair (parent, child) есть
   hypothesis — берём оттуда; иначе нужен какой-то fallback.

4. **Pro-genealogy UX.** Pro юзеры особенно ценят explicit absence —
   «нет supporting evidence» это **information**, а не пустой экран.
   Empty states важнее loading indicators.

5. **Phase 15.1 scope.** Это первый pro-feature. Нельзя scope-creep'нуть
   в Add evidence flow, archive search, hypothesis sandbox — это 15.2,
   15.5, 15.3 соответственно. 15.1 — **read-only consumer**.

## Рассмотренные варианты

### Вариант A — Composite-key URL + parser-service hosting (выбран)

URL: `GET /trees/{tree_id}/relationships/{kind}/{subject_id}/{object_id}/evidence`.

`kind` — enum (parent_child / spouse / sibling). Composite key вместо
stable `relationship_id` — компенсация за отсутствие single-relationship
view в схеме. Endpoint живёт в parser-service (где Family / Citation /
Hypothesis ORM-модели и mature permission gate `require_tree_role`).

- ✅ Никаких миграций / новых таблиц.
- ✅ Никаких новых сервисов (`api-gateway` пока пустой scaffold; не
  блокируем 15.1 на нём).
- ✅ Parser-service уже владеет authn + sharing semantics.
- ❌ RESTfully ugly URL — путь длинный.
- ❌ Если в будущем появится «relationships view» (Phase 15.x), эта
  ручка либо переезжает, либо дублируется.

### Вариант B — Добавить provenance к FamilyChild + использовать его UUID

Миграция: расширить `family_children` до TreeEntityMixins (provenance,
version_id, deleted_at, status, confidence_score). Затем
`/relationships/family-child/{family_child_id}/evidence`.

- ✅ Stable URL.
- ✅ FamilyChild становится first-class entity, can be soft-deleted /
  audited / cited напрямую.
- ❌ Миграция + бэкфилл существующих rows.
- ❌ Schema-change в Phase 15.1, который должен был быть «UI поверх
  готового backend» — нарушает scope.
- ❌ Spouse-relationship всё равно остаётся имплицитным внутри Family —
  отдельный URL pattern. Дискомфорт вместо одного.
- Откладывается до Phase 15.x когда будет явный signal.

### Вариант C — Scaffold api-gateway сначала

Phase 14.5 (или 15.0): новый FastAPI service `api-gateway` с
lifespan, auth, observability, security middleware. Phase 15.1 —
endpoint в нём.

- ✅ Архитектурно правильно (api-gateway как BFF).
- ❌ 2× работы: один PR на scaffold, второй на feature.
- ❌ Phase 15.1 теряет «первый pro-feature» momentum.
- Откладывается. Когда api-gateway появится по другим причинам,
  endpoint мигрируется (URL contract сохраняется).

### Вариант D — Frontend-only aggregation

UI читает `/trees/{id}/sources`, `/persons/{id}/citations`, `/hypotheses`
независимо и собирает relationship-view на клиенте.

- ❌ Дублирует логику резолва relationship → families в JS.
- ❌ N×M запросов на каждое открытие drawer'а.
- ❌ Frontend знает SQL-уровневые детали schema. Tight coupling.

## Решение

Принят **Вариант A — composite-key URL в parser-service.**

### Endpoint contract

```text
GET /trees/{tree_id}/relationships/{kind}/{subject_id}/{object_id}/evidence
```

**Path params:**

- `tree_id` — UUID. Auth через `require_tree_role(VIEWER)`.
- `kind` — enum `parent_child | spouse | sibling`. Прочие 422.
- `subject_id` — UUID person. Для `parent_child` это **родитель**
  (направленная связь); для `spouse` / `sibling` — симметричный участник.
- `object_id` — UUID person. Для `parent_child` это **ребёнок**.

**Response (Pydantic-схема — `parser_service.schemas.RelationshipEvidenceResponse`):**

```jsonc
{
  "relationship": {
    "kind": "parent_child",
    "subject_person_id": "...",
    "object_person_id": "..."
  },
  "supporting": [
    {
      "source_id": "...",      // null для inference_rule
      "citation_id": "...",
      "title": "...",
      "repository": "...",
      "reliability": 0.8,        // 0..1, citation.quality или evidence.weight
      "citation": "p. 17",
      "snippet": "...",
      "url": "...",
      "added_at": "2026-...",
      "kind": "citation",        // "citation" | "inference_rule"
      "rule_id": null            // заполнен для kind="inference_rule"
    }
  ],
  "contradicting": [...],
  "confidence": {
    "score": 0.87,
    "method": "bayesian_fusion_v2",   // или "naive_count"
    "computed_at": "...",
    "hypothesis_id": "..."             // null если method="naive_count"
  },
  "provenance": {
    "source_files": [...],
    "import_job_id": "...",
    "manual_edits": [...]
  }
}
```

**Status codes:**

- `200` — happy path. Empty `supporting`/`contradicting` — это **valid 200**
  с явным empty state в UI.
- `400` — `subject_id == object_id` (self-loop защита).
- `403` — caller не имеет VIEWER+ роли в tree.
- `404` — tree не найден / один из persons не найден / relationship
  данного kind между этой парой не существует в данных.
- `422` — `kind` вне enum.

### Aggregation algorithm

1. **Resolve relationship → ORM rows.**
   - `parent_child(parent, child)` — Family где parent ∈ {husband, wife}
     **И** FamilyChild с child_person_id=child.
   - `spouse(a, b)` — Family где `{husband_id, wife_id} == {a, b}`.
   - `sibling(a, b)` — все Families где **оба** a и b в FamilyChild.
   - Нет совпадений → 404.

2. **Aggregate supporting sources:**
   - Все `Citation` rows на `entity_type='family'`, `entity_id ∈ family_ids`.
   - Для `spouse` дополнительно: `Citation` rows на `entity_type='event'`
     для events типа MARR / DIV / ENGA / ANUL / MARC / MARS, где
     `EventParticipant.family_id ∈ family_ids`.
   - Каждый Citation + его Source → один `RelationshipEvidenceSource`
     с `kind="citation"`.

3. **Look up hypothesis** для (tree, hypothesis_type, ordered subjects).
   `hypothesis_type`:
   - parent_child → `parent_child`
   - spouse → `marriage`
   - sibling → `siblings`

4. **Hypothesis evidences:**
   - `direction='supports'` → push в `supporting` с `kind="inference_rule"`.
   - `direction='contradicts'` → push в `contradicting` (то же).
   - `direction='neutral'` — пропускаем (нет UI-семантики).

5. **Confidence:**
   - Если есть hypothesis: `score=hypothesis.composite_score`,
     `method="bayesian_fusion_v2"`, `hypothesis_id` заполнен.
   - Иначе: `score = supporting / (supporting + contradicting)`,
     `method="naive_count"`, `hypothesis_id=None`. UI рендерит
     `naive_count` другим оттенком — пользователь должен видеть, что это
     не настоящий Bayesian rollup.

6. **Provenance:** union `source_files` всех вошедших Family.provenance,
   последний `import_job_id`, concat `manual_edits`.

### UI contract (Phase 15.1)

`<RelationshipEvidencePanel open onClose treeId kind subjectId objectId>`:

- shadcn-style right-drawer (built без shadcn Sheet — его нет в проекте,
  drawer self-contained на Tailwind + role="dialog").
- Three tabs: Supporting (default) | Contradicting | Provenance.
- Confidence badge color thresholds:
  - score ≥ 0.85 → green (high confidence)
  - 0.6 ≤ score < 0.85 → amber
  - score < 0.6 → red
- Empty `supporting`: **yellow warning** — pro genealogists ценят
  explicit absence — с disabled CTA "Add archive search (coming soon)"
  → Phase 15.5.
- Empty `contradicting`: **neutral grey** — отсутствие противоречий ≠
  тревога.
- Footer: disabled `Add evidence` CTA → Phase 15.2.
- i18n: en + ru через next-intl, все user-strings в `relationshipEvidence`
  namespace.

### Trigger ownership

Phase 15.1 **НЕ** трогает tree rendering layer / D3 / canvas. Trigger
(клик на edge / context menu) — задача Phase 15.1.x или явного
follow-up wiring PR. Сейчас компонент полностью controlled (open prop
плюс onClose callback), родитель решает когда показать.

## Последствия

**Положительные:**

- Pro-genealogy users получают первую "evidence engine" фичу, видимую
  в UI. Differentiation от Ancestry / MyHeritage (которые показывают
  citations на person, не на link).
- Backend aggregation contract стабилизируется до того, как хайпотезы
  расширятся (Phase 7.6+) — UI не сломается.
- Empty states first-class: pro-юзеры видят «здесь чисто, добавь
  evidence» вместо blank screen.
- Никаких миграций — обратная совместимость идеальная.

**Отрицательные / стоимость:**

- Composite-key URL семантически тяжелее `/relationships/{id}` —
  document'ируется через ADR + OpenAPI.
- Phase 15.2 (Add evidence) и 15.5 (Archive search) должны соблюсти
  тот же URL pattern — иначе UI получит inconsistent shape.
- Spouse aggregation includes MARR/DIV events — могут быть marriage
  records на стороне DIV-event'а, которые семантически принадлежат к
  development of marriage, не к самому факту брака. Phase 15.x может
  фильтровать жёстче.

**Риски:**

- **Performance.** Per-request 4–5 SQL queries (resolve family + citations
  on family + maybe spouse-event citations + hypothesis + hypothesis
  evidences). p95 target — < 300 мс. На большом дереве с десятками
  citations per family может взлететь до ~50 мс уже сейчас. Mitigation:
  индексы есть (Citation `ix_citations_entity` + Hypothesis tree+subjects),
  если будет проблема — добавим Redis cache как в Phase 6.4 (триангуляция).
- **Schema drift с Phase 15.x.** Если потом появится first-class
  Relationship table, существующий endpoint станет shim. Versioning
  через URL path (current — без `/api/v1/`; будущая миграция → новый
  endpoint).
- **Hypothesis canonical ordering.** Hypothesis storage — `subject_a < subject_b`
  лексикографически по UUID. Endpoint пробует оба порядка при lookup —
  правильно, но если caller pass'ает direction-sensitive pair (parent → child),
  parent_child-hypothesis'у *направление* не передаётся через ordering.
  Семантически это OK для Phase 15.1: relationship hypothesis "is parent_child
  between A и B" не имеет direction (если A родитель B, то ровно одна
  hypothesis). Direction передаётся через subject_a_type='person:parent' vs
  'person:child' если потом понадобится.

**Что нужно сделать в коде (Phase 15.1):**

1. `services/parser-service/src/parser_service/api/relationships.py` —
   endpoint, resolver helpers, aggregation.
2. `services/parser-service/src/parser_service/schemas.py` — DTOs:
   `RelationshipReference`, `RelationshipEvidenceSource`,
   `RelationshipEvidenceConfidence`, `RelationshipEvidenceProvenance`,
   `RelationshipEvidenceResponse`.
3. `services/parser-service/src/parser_service/main.py` — register router
   до `sharing` (порядок path-overlap).
4. `services/parser-service/tests/test_relationships_evidence.py` —
   integration tests (10 тестов: 3 happy paths × kinds, hypothesis,
   contradicting, empty, 404 unknown relationship, 404 unknown person,
   403 non-member, 400 self-loop, 422 unknown kind).
5. `apps/web/src/lib/relationships-api.ts` — typed client (зеркало DTOs).
6. `apps/web/src/components/relationship-evidence-panel.tsx` —
   self-contained drawer + tabs + confidence badge + empty states.
7. `apps/web/src/components/__tests__/relationship-evidence-panel.test.tsx`
   — Vitest на 12 тест-кейсов (badge tones, tab switch, empty CTAs,
   close, no-render-when-closed, provenance content).
8. `apps/web/messages/{en,ru}.json` — i18n ключи в namespace
   `relationshipEvidence`.
9. `ROADMAP.md` — добавить Phase 15 секцию.

## Когда пересмотреть

- **Phase 15.2** добавит "Add evidence" flow → endpoint POST на этом же
  URL pattern (или sibling URL). Контракт response остаётся.
- **Phase 15.5** (archive search) включит "Add archive search" CTA.
- **Phase 15.x** (если появится first-class Relationship view): URL
  version bump или migration shim.
- **api-gateway scaffolded** → endpoint мигрируется в gateway, parser-service
  endpoint становится internal.
- **p95 > 300 мс** на типовых деревьях → Redis cache через тот же
  pattern, что Phase 6.4 triangulation (`CacheBackend` Protocol +
  `get_cache` dep + 1h TTL).
- **Endogamous деревья** дают spouse-multifamilies (один человек супруг
  трёх разных people в разных families) → текущий endpoint вернёт
  evidence только для матчащей пары; OK as-is.
- **Half-sibling vs full-sibling** distinction (Phase 6.5 IBD2) → ввести
  `kind=half_sibling` или filter parameter.

## Ссылки

- ADR-0003 — versioning + provenance jsonb (нашему source).
- ADR-0014 — DNA matching, source of confidence-thresholds intuition.
- ADR-0021 — Hypothesis persistence (FK Hypothesis ↔ HypothesisEvidence).
- ADR-0036 — Sharing & permissions model (`require_tree_role` semantics).
- ADR-0057 — inference-engine v2 aggregation (Bayesian fusion source).
- ROADMAP §15 (этот PR добавляет секцию).
