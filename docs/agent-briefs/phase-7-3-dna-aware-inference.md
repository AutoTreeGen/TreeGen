# Agent brief — Phase 7.3: DNA-aware inference rules

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-dna-inference`.
> Это THE killer feature для AutoTreeGen. Все prerequisites теперь в main:
> Hypothesis ORM (Phase 7.2), inference engine (Phase 7.0), DNA matching
> (Phase 6.1). Соединяем их.
> Перед стартом: `CLAUDE.md` §3.2 (hypothesis-aware), `ROADMAP.md`,
> `docs/adr/0016` (inference engine), Phase 6.1 PRs #38–#49 в main —
> особенно `packages/dna-analysis/src/.../matcher.py`,
> `packages/inference-engine/src/.../rules/`,
> `packages/shared-models/src/.../orm.py` (Hypothesis + Evidence).

---

## Зачем

Сейчас inference engine оперирует только GEDCOM-evidence (имена, даты,
места). DNA — это **сильнейший** evidence в современной генеалогии.
ThruLines на Ancestry популярны именно потому, что DNA подтверждает или
отбрасывает гипотезы родства которые иначе на догадках.

После Phase 7.3:

- Гипотеза «X — родственник Y N-степени» получает evidence типа
  `dna_segment_match` с конкретным сегментом + cM + chromosome.
- Если кластер DNA-матчей (>= 5 человек share segment с known descendant
  of person Z) — генерируется гипотеза «Z is common ancestor of cluster».
- Confidence-scoring учитывает Shared cM Project distribution.

Это **не auto-merge**. Это auto-**hypothesis** — запись с rationale,
evidence, status='proposed'. Юзер ревьюит вручную (Phase 4.5/4.6
review UI уже есть).

---

## Что НЕ делать

- ❌ Auto-merge persons на основе DNA. Только гипотезы.
- ❌ Скачивать raw DNA. Используем уже спарсенное в Phase 6.0/6.1.
- ❌ Хранить raw rsids/genotypes в evidence. Только агрегаты:
  segment ranges, cM, chromosome, kit_id (псевдоним) — **не** sample data.
- ❌ Game endogamy (jewish/eastern european особенность) с примитивным
  threshold'ом. См. секцию «Endogamy notes» ниже.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — ADR-0023: DNA evidence в inference engine

**Файл:** `docs/adr/0023-dna-aware-inference.md`

Зафиксируй:

1. **Какой evidence type новый**: `dna_segment_match` с полями
   `chromosome`, `start_bp`, `end_bp`, `cm`, `snp_count`, `match_kit_id`,
   `subject_kit_id`. Хранится в `HypothesisEvidence.evidence_data` (jsonb).
2. **Endogamy adjustment**: для kit'ов с признаком endogamy
   (определяется по % shared > threshold с unrelated kit'ом или флаг
   в DnaTestRecord) — конвертация cM → relationship distance меняется
   по таблице из Shared cM Project endogamy variant. Конкретные числа —
   ссылка на CC-BY таблицу.
3. **Confidence formula**: per-rule, но базовый принцип:
   - `cm >= 200` → high confidence (0.8+) for close relationships
   - `cm 50–200` → medium (0.5–0.7), может быть multiple generations
   - `cm 7–50` → low (0.2–0.5), distant cousin или endogamy noise
   - `cm < 7` → не считать (noise threshold per Phase 6.1)
4. **Cluster threshold**: минимум 3 kit'а share сегмент чтобы триггерить
   "common ancestor cluster" гипотезу. <3 — слабый сигнал.
5. **Tree-DNA bridging**: как DnaTestRecord связан с Person? Через FK
   `dna_test_records.person_id` (nullable — не каждый kit owner есть
   в дереве). Inference rule идёт от kit → person → hypothesis.

### Task 2 — Inference rules

**Файл:** `packages/inference-engine/src/inference_engine/rules/dna.py`, плюс tests в `packages/inference-engine/tests/test_dna_rules.py`.

**Rule 1: `DnaSegmentRelationshipRule`**

```python
class DnaSegmentRelationshipRule(InferenceRule):
    rule_id = "dna_segment_relationship"
    
    def apply(self, ctx: InferenceContext) -> list[Hypothesis]:
        """Для каждой пары kit'ов с shared segments >= threshold
        генерирует гипотезу о степени родства между их subjects."""
        # 1. Получить все DNA-segments из ctx (через DnaTestRecord JOIN
        #    DnaSegmentMatch).
        # 2. Для каждой пары (subject_a, subject_b) compute total_cm.
        # 3. Map total_cm → relationship distribution per Shared cM Project.
        # 4. Generate Hypothesis с evidence_data содержащим segments.
        # 5. Учесть endogamy флаг.
```

**Rule 2: `DnaCommonAncestorClusterRule`**

```python
class DnaCommonAncestorClusterRule(InferenceRule):
    rule_id = "dna_common_ancestor_cluster"
    
    def apply(self, ctx: InferenceContext) -> list[Hypothesis]:
        """Если >= 3 kit'а share единый segment range AND каждый из них
        descendant of различной известной person в дереве, генерирует
        гипотезу 'X is common ancestor for cluster'. X — наиболее свежий
        известный common ancestor этих descendants по дереву."""
```

**Rule 3 (опционально, если успеешь): `DnaContradictsRelationshipRule`**

Если в дереве записано родство 1-3 степени, но DNA-сегменты дают
total_cm НИЖЕ 95% confidence interval из Shared cM Project — генерирует
гипотезу-warning «recorded relationship X may be incorrect; expected
Y cM, observed Z cM». **Не** удаляет ничего, только warning hypothesis.

### Task 3 — Wire в hypothesis_runner

**Файл:** `services/parser-service/src/parser_service/services/hypothesis_runner.py`
(существующий из Phase 7.2).

Зарегистрируй новые rules в registry. Добавь в `compute_hypothesis`
endpoint поддержку DNA evidence — если у persons есть associated
kit_ids, rules должны их видеть.

Тесты integration в `services/parser-service/tests/test_dna_inference_integration.py`:

- Synthetic DNA fixture (seed=42 per Phase 6.0): два kit'а share 1500 cM
  на одном сегменте → DnaSegmentRelationshipRule генерирует гипотезу
  «parent-child or full sibling» (Shared cM 0.5%-99.5% range).
- Cluster: 4 синтетических kit'а share 50 cM на chr 7:120-130 Mb →
  DnaCommonAncestorClusterRule generates 1 cluster hypothesis.
- Endogamy флаг: тот же 50 cM → confidence НИЖЕ чем без endogamy.

### Task 4 — DNA → Person bridging

Если ещё нет: миграция Alembic добавляющая `person_id INTEGER NULLABLE`
в `dna_test_records` + индекс. Это позволит rules идти от kit → person.

Endpoint `PATCH /dna/tests/{kit_id}/link-person` body `{person_id: int}`
для manual linking (юзер указывает «этот кит мой дед Иван»).

Тесты: linking + unlinking, что rules видят linked persons.

### Task 5 — Endogamy notes

Добавь в `docs/dna-endogamy-notes.md`:

- Что такое endogamy (closed populations: AJ, Quebec French, Amish, etc.).
- Почему cM inflated в endogamic populations.
- Как мы детектируем (threshold + manual flag).
- Какую таблицу Shared cM Project используем (link, version).

## Endogamy notes (важно для владельца)

Mой (владельца) cohort — Ashkenazi Jewish, classic endogamy. Без
endogamy adjustment standard Shared cM таблицы будут давать FALSE
HIGH confidence на distant matches. Например, 50 cM total в AJ-cohort
часто означает 5–7 cousins, не 3–4. Rule должен давать `confidence: 0.3`
там где non-endogamy logic дал бы `0.6`.

Default-mark endogamy=true можно в DnaTestRecord если kit_id principal
имеет фамилию из AJ surname list (TODO: можно расширить эту фичу
позже). Для MVP — manual флаг в API.

### Task 6 — Финал

1. ROADMAP §7.3 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-7.3-dna-aware-inference`.
4. CI green до merge. Никакого `--no-verify`.
5. PR description: что нового, как использовать, скриншот example
   hypothesis (можно скопировать JSON из теста).

---

## Сигналы успеха

1. ✅ ADR-0023 в `docs/adr/`.
2. ✅ DnaSegmentRelationshipRule + DnaCommonAncestorClusterRule
    зарегистрированы и тесты зелёные.
3. ✅ Endogamy adjustment работает (тест показывает разницу).
4. ✅ Cluster threshold 3+ работает.
5. ✅ DNA → Person bridging endpoint работает.
6. ✅ docs/dna-endogamy-notes.md создан.

---

## Если застрял

- Shared cM Project table большая → возьми только первые ~20 levels
  (1C — 6C), достаточно для MVP. Полный JSON в follow-up.
- Endogamy detection auto — отложи в follow-up. MVP = manual флаг.
- Cluster algorithm O(n²) на больших cohort'ах → уже limit'нём scope:
  только matches >= 7 cM, только для linked persons.
- Hypothesis_runner integration сложно → начни с pure unit-test rules,
  hypothesis_runner integration можно follow-up PR.

Удачи. Это та фича, ради которой проект и затеян.
